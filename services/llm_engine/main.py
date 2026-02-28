from concurrent.futures import ThreadPoolExecutor
import uuid
from common.utils import get_env, get_db_conn, get_pinecone_client
import time
from typing import Any, List, Dict, Optional, TypedDict
from langsmith import traceable
from common.analytics import record_stage_execution
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
import promptlayer
from langchain_community.tools import DuckDuckGoSearchRun
from guardrails import Guard
from guardrails.hub import ValidLength
from common.analytics import record_query_result
from langgraph.graph import StateGraph, END
from fastapi import FastAPI
from pydantic import BaseModel, Field

length_guard = Guard().use(
    ValidLength(min=20, max=4000)
)

def log(message: str):
    print(f"[llm_engine] {message}")

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=1,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

promptlayer_client = promptlayer.PromptLayer()
OpenAI = promptlayer_client.openai.OpenAI
groq_client = OpenAI(
    api_key=get_env("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

def safe_record_stage(trace_id, stage_name, status, start):
    try:
        log(f"safe_record_stage start trace_id={trace_id} stage={stage_name} status={status}")
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name=stage_name,
            status=status,
            latency_ms=int((time.time() - start) * 1000),
        )
        log(f"safe_record_stage success trace_id={trace_id} stage={stage_name} status={status}")
        return True
    except Exception as exc:
        log(f"safe_record_stage failure trace_id={trace_id} stage={stage_name} status={status} error={exc}")
        return False

def search_with_text(index, text, top_k):
    try:
        log(f"search_with_text start top_k={top_k}")
        response = index.search(
            namespace="default",
            query={"inputs": {"text": text}, "top_k": top_k},
        )
        hits = response.get("result", {}).get("hits") or []
        log(f"search_with_text hits={len(hits)}")
        return {hit["_id"]: hit["_score"] for hit in hits}

    except Exception as exc:
        log(f"search_with_text error={exc}")
        return None

def fetch_chunks_from_neon(conn, chunk_hash):
    try:
        if not chunk_hash:
            log("fetch_chunks_from_neon empty chunk_hash")
            return {}

        placeholders = ",".join(["%s"] * len(chunk_hash))
        log(f"fetch_chunks_from_neon start n_hashes={len(chunk_hash)}")

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT chunk_hash, raw_chunk, section_path, page_id, is_active
                FROM kb_chunks
                WHERE chunk_hash IN ({placeholders})
                """,
                chunk_hash,
            )
            rows = cur.fetchall()

        log(f"fetch_chunks_from_neon rows={len(rows)}")
        
        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3], "is_active": row[4]}
            for row in rows
        }
    except Exception as exc:
        log(f"fetch_chunks_from_neon error={exc}")
        return None

def fetch_images_from_neon(conn, image_hash):
    try:
        if not image_hash:
            log("fetch_images_from_neon empty image_hash")
            return {}

        placeholders = ",".join(["%s"] * len(image_hash))
        log(f"fetch_images_from_neon start n_hashes={len(image_hash)}")

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT image_hash, page_id, image_src, caption
                FROM kb_images
                WHERE image_hash IN ({placeholders})
                """,
                image_hash,
            )
            rows = cur.fetchall()

        log(f"fetch_images_from_neon rows={len(rows)}")
        
        return {
            row[0]: {"page_id": row[1], "url": row[2], "caption": row[3]}
            for row in rows
        }
    except Exception as exc:
        log(f"fetch_images_from_neon error={exc}")
        return None

def call_reranker(query, texts):
    try:
        reranker_url = get_env("RERANKER_URL")
        if not reranker_url:
            log("call_reranker missing RERANKER_URL")
            return None

        log(f"call_reranker start n_texts={len(texts)}")
        r = http_session.post(
            reranker_url,
            json={"query": query, "texts": texts},
            timeout=120,
        )
        r.raise_for_status()
        scores = r.json()["scores"]
        log(f"call_reranker scores={len(scores)}")
        return scores
    except Exception as exc:
        log(f"call_reranker error={exc}")
        return None

def build_context(chunks):
    try:
        if not chunks:
            log("build_context empty chunks")
            return ""
        log(f"build_context start n_chunks={len(chunks)}")
        context = "\n\n".join(
            (
                f"[Page ID: {c['page_id']}]\n"
                f"This is an ACTIVE chunk. "
                f"It belongs to the section '{c['section']}'. "
                f"The content is:\n{c['text']}"
                if c["is_active"]
                else
                f"[Page ID: {c['page_id']}]\n"
                f"This is an INACTIVE chunk. "
                f"It belongs to the section '{c['section']}'. "
                f"The content is:\n{c['text']}"
            )
            for c in chunks
        )
        log(f"build_context size={len(context)}")
        return context
    except Exception as exc:
        log(f"build_context error={exc}")
        return None

def call_groq_llm(query, context, history=None):
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model or context is None:
            log("call_groq_llm missing GROQ_MODEL or context")
            return None

        messages = [
            {
                "role": "system",
                "content": (
                        "You are an internal company knowledge assistant for Alphabet/Google. "
                        "Answer only using the provided context. Be clear and concise. "
                        "If the answer is not explicitly in the context, reply: "
                        "'I couldn't find that in the knowledge base.' "
                        "\n\n"
                        "After your answer, add a natural conversational closing. "
                        "This should be a smooth 1-2 sentence transition that either: "
                        "(a) offers to dive deeper into a related aspect, or "
                        "(b) poses one relevant follow-up question — "
                        "phrased naturally like 'Would you like to explore...', "
                        "'Curious about...?', or 'If you're interested, I can also tell you about...'. "
                        "Never just drop a question abruptly. Make it feel like a conversation."
                    ),
            }
        ]

        if history:
            log(f"call_groq_llm history_len={len(history)}")
            messages.extend(history)

        messages.append({
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {query}",
        })

        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )

        choices = response.choices
        if not choices:
            log("call_groq_llm no choices")
            return None

        answer = choices[0].message.content
        log(f"call_groq_llm answer_len={len(answer) if answer else 0}")
        return answer

    except Exception as exc:
        log(f"call_groq_llm error={exc}")
        return None

def call_nli(premise, hypothesis):
    try:
        nli_url = get_env("NLI_URL")

        if not nli_url:
            log("call_nli missing NLI_URL")
            return None
        
        log("call_nli start")
        r = http_session.post(
            nli_url,
            json={"premise": premise, "hypothesis": hypothesis},
            timeout=60,
        )
        r.raise_for_status()
        result = r.json()
        log("call_nli success")
        return result
    except Exception as exc:
        log(f"call_nli error={exc}")
        return None

def call_web_search_fallback(query):
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model:
            log("call_web_search_fallback missing GROQ_MODEL")
            return None

        search = DuckDuckGoSearchRun()
        web_results = search.run(query)
        log("call_web_search_fallback web_results_ready")
        prompt = f"""
        The user asked: {query}
        The following information was found on the web:
        {web_results}
        Please provide a concise summary of these web findings. 
        State clearly that this information is from the web and not the internal knowledge base.
        """

        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
        )
        summary = response.choices[0].message.content
        log(f"call_web_search_fallback summary_len={len(summary) if summary else 0}")
        return summary
    except Exception as exc:
        log(f"call_web_search_fallback error={exc}")
        return None

def validate_answer_length(answer):
    try:
        log(f"validate_answer_length start len={len(answer) if answer else 0}")
        result = length_guard.validate(answer)
        log(f"validate_answer_length passed={result.validation_passed}")
        return result.validation_passed
    except Exception as exc:
        log(f"validate_answer_length error={exc}")
        return False

def classify_intent(query):
    try:
        groq_model = get_env("GROQ_MODEL")
        if groq_model is None:
            log("classify_intent missing GROQ_MODEL")
            return None

        prompt = f"""
            Decide if the user query needs specific facts from the company's internal knowledge base or if it is a general interaction.

            'knowledge': Questions about Alphabet and its funded companies. Questions relating to Google and its products or updates.
            'general': Greetings, identity questions (who are you), small talk like hey, hi.

            User Query: {query}

            Respond with exactly one word: 'knowledge' or 'general'.
        """

        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=5,
        )

        choices = response.choices
        if not choices:
            log("classify_intent no choices")
            return None

        intent = choices[0].message.content.strip().lower()

        if intent not in ("knowledge", "general"):
            log(f"classify_intent invalid_intent={intent}")
            return None

        log(f"classify_intent result={intent}")
        return intent

    except Exception as exc:
        log(f"classify_intent error={exc}")
        return None

def get_images(images_index, query, context):
    IMAGE_SCORE_THRESHOLD = 0.15
    TOP_K_IMAGES = 5
    conn = get_db_conn()
    if not conn:
        return None
    try:
        log("get_images start")
        image_search_input = f"{query}\n\n{context}"
        image_scores = search_with_text(images_index, image_search_input, 20)
        if not image_scores:
            log("get_images no image_scores")
            return None

        filtered_ids = [
            img_id for img_id, score in image_scores.items()
            if score >= IMAGE_SCORE_THRESHOLD
        ]
        if not filtered_ids:
            log("get_images no filtered_ids")
            return None

        fetched_images = fetch_images_from_neon(conn, filtered_ids)
        if not fetched_images:
            log("get_images no fetched_images")
            return None

        ordered_images = sorted(
            fetched_images.items(),
            key=lambda kv: image_scores.get(kv[0], 0),
            reverse=True,
        )

        result = [
            {
                "url": img["url"],
                "page_id": img["page_id"],
                "caption": img["caption"],
            }
            for _, img in ordered_images[:TOP_K_IMAGES]
        ]
        log(f"get_images result_count={len(result)}")
        return result

    except Exception as exc:
        log(f"get_images error={exc}")
        return None
    
    finally:
        conn.close()

class FinalAnswer(TypedDict):
    query: str
    answer: str
    sources: List[dict]
    images: List[dict]
    contradiction_score: float

class AgentState(TypedDict):
    query: str
    history: List[Dict[str, str]]
    trace_id: str
    start_total: float

    indexes: dict
    db_conn: Any
    
    intent: Optional[str]

    top_chunks: Optional[List[dict]]
    web_findings: Optional[str]
    sources: Optional[List[dict]]
    images: Optional[List[dict]]

    context: Optional[str]
    answer: Optional[str]
    contradiction_score: Optional[float]

    final_output: Optional[FinalAnswer]

@traceable
def intent_router_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "intent_router"

    try:
        log(f"intent_router_node start trace_id={trace_id}")
        intent = classify_intent(state["query"])
        if intent is None:
            log("intent_router_node intent=None")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"intent": None}

        log(f"intent_router_node intent={intent}")
        safe_record_stage(trace_id, stage_name, "success", start)
        return {"intent": intent}

    except Exception as exc:
        log(f"intent_router_node error={exc}")
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {"intent": None}

@traceable
def general_reply_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    fallback = { "final_output": None }

    try:
        log(f"general_reply_node start trace_id={trace_id}")
        groq_model = get_env("GROQ_MODEL")
        if groq_model is None:
            log("general_reply_node missing GROQ_MODEL")
            safe_record_stage(trace_id, "general_reply", "failure", start)
            return fallback
        
        messages = [
            {
                "role": "system",
                "content": (
                    """You are a helpful internal knowledge assistant for Google.
                    Greet the user or respond to their query politely and conversationally.
                    Use a natural, friendly tone — avoid sounding scripted or repetitive.
                    Respond in a natural, conversational tone. When it makes sense, continue the conversation with a relevant and engaging follow-up question related to Alphabet, Google products, or company updates."""
                )
            }
        ]

        if state.get("history"):
            messages.extend(state["history"])

        messages.append({"role": "user", "content": state["query"]})

        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=messages,
            temperature=0.7,
            max_tokens=400,
        )

        choices = response.choices
        if not choices:
            log("general_reply_node no choices")
            safe_record_stage(trace_id, "general_reply", "failure", start)
            return fallback

        answer = choices[0].message.content
        log(f"general_reply_node answer_len={len(answer) if answer else 0}")

        safe_record_stage(trace_id, "general_reply", "success", start)
        return {
            "final_output": {
                "query": state["query"],
                "answer": answer,
                "sources": [],
                "images": [],
                "contradiction_score": 0.0,
            }
        }

    except Exception as exc:
        log(f"general_reply_node error={exc}")
        safe_record_stage(trace_id, "general_reply", "failure", start)
        return fallback

@traceable
def retrieve_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "retrieve" 
    THRESHOLD = 0.6

    try:
        log(f"retrieve_node start trace_id={trace_id}")
        indexes = state["indexes"]
        if not indexes:
            log("retrieve_node missing indexes")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        if state["db_conn"] is None:
            log("retrieve_node missing db_conn")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        chunks_index = indexes["chunks"]
        pages_index = indexes["pages"]

        TOP_K_CHUNKS = 20
        TOP_K_PAGES = 20
        FINAL_TOP_K = 10
        W_CHUNK = 0.7
        W_PAGE = 0.3

        chunk_scores = search_with_text(
            chunks_index, state["query"], TOP_K_CHUNKS
        )
        if not chunk_scores:
            log("retrieve_node no chunk_scores")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        page_scores = search_with_text(
            pages_index, state["query"], TOP_K_PAGES
        ) or {}

        chunk_metadata = fetch_chunks_from_neon(
            state["db_conn"], list(chunk_scores.keys())
        )
        if not chunk_metadata:
            log("retrieve_node no chunk_metadata")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        fused = []
        for chunk_id, chunk_score in chunk_scores.items():
            meta = chunk_metadata.get(chunk_id)
            if not meta:
                continue

            page_score = page_scores.get(str(meta["page_id"]), 0.0)
            fused.append({
                "chunk_id": chunk_id,
                "page_id": meta["page_id"],
                "section": meta["section"],
                "text": meta["text"],
                "is_active": meta["is_active"],
                "fused_score": (W_CHUNK * chunk_score) + (W_PAGE * page_score),
            })

        if not fused:
            log("retrieve_node fused empty")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        top_fused = sorted(fused, key=lambda x: x["fused_score"], reverse=True)[:15]
        rerank_scores = call_reranker(
            state["query"],
            [f["text"] for f in top_fused]
        )
        if not rerank_scores:
            log("retrieve_node no rerank_scores")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        ALPHA = 0.7

        for item, score in zip(top_fused, rerank_scores):
            item["rerank_score"] = score
            item["final_score"] = (
                ALPHA * score +
                (1 - ALPHA) * item["fused_score"]
            )

        top_fused.sort(key=lambda x: x["final_score"], reverse=True)
        top_chunks = top_fused[:FINAL_TOP_K]
        
        filtered = [f for f in top_chunks if f["final_score"] >= THRESHOLD]

        if not filtered:
            log("retrieve_node filtered empty")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        context = build_context(filtered)
        if context is None:
            log("retrieve_node context is None")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        log(f"retrieve_node success n_chunks={len(filtered)}")
        safe_record_stage(trace_id, stage_name, "success", start)
        return {
            "top_chunks": filtered,
            "context": context,
        }

    except Exception as exc:
        log(f"retrieve_node error={exc}")
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {"top_chunks": None, "context": None}

@traceable
def generation_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "generation"

    try:
        log(f"generation_node start trace_id={trace_id}")
        if not state["top_chunks"]:
            log("generation_node no top_chunks")
            safe_record_stage(trace_id, stage_name, "success", start)
            return {
                "answer": None,
                "images": None,
                "web_findings": None,
                "contradiction_score": 0.0,
                "final_output": None,
            }

        with ThreadPoolExecutor() as executor:
            llm_future = executor.submit(
                call_groq_llm,
                state["query"],
                state["context"],
                state["history"],
            )
            image_future = executor.submit(
                get_images,
                state["indexes"]["images"],
                state["query"],
                state["context"],
            )

            answer = llm_future.result()
            images = image_future.result()
            log(f"generation_node got answer={answer is not None} images={len(images) if images else 0}")

        if answer is None:
            log("generation_node answer None")
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {
                "answer": None,
                "images": None,
                "web_findings": None,
                "contradiction_score": 0.0,
                "final_output": None,
            }

        web_findings = None
        if "not found in knowledge base" in answer.lower():
            log("generation_node triggering web_search_fallback")
            web_findings = call_web_search_fallback(state["query"])
            log(f"generation_node web_findings={web_findings is not None}")

        safe_record_stage(trace_id, stage_name, "success", start)
        return {
            "answer": answer,
            "images": images or [],
            "web_findings": web_findings,
            "contradiction_score": 0.0,
            "final_output": None,
        }

    except Exception as exc:
        log(f"generation_node error={exc}")
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {
            "answer": None,
            "images": None,
            "web_findings": None,
            "contradiction_score": 0.0,
            "final_output": None,
        }

@traceable
def validation_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "validation"
    NLI_CONTRADICTION_THRESHOLD = 0.7

    try:
        log(f"validation_node start trace_id={trace_id}")
        answer = state.get("answer")
        context = state.get("context")
        top_chunks = state.get("top_chunks") or []

        if answer is None or context is None:
            log("validation_node missing answer/context")
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                final_status="no_answer",
                top_k_chunks=len(top_chunks),
                context_chars=len(context) if context else 0,
                answer_chars=0,
                contradiction_score=0.0,
                total_latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"final_output": None}

        nli = call_nli(context, answer)
        if not nli:
            log("validation_node nli failed")
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                final_status="nli_failed",
                top_k_chunks=len(top_chunks),
                context_chars=len(context) if context else 0,
                answer_chars=0,
                contradiction_score=0.0,
                total_latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"final_output": None}

        contradiction_score = nli.get("contradiction", 0.0)
        log(f"validation_node contradiction_score={contradiction_score}")

        if contradiction_score >= NLI_CONTRADICTION_THRESHOLD:
            log("validation_node contradicted")
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                final_status="contradicted",
                top_k_chunks=len(top_chunks),
                context_chars=len(context) if context else 0,
                answer_chars=0,
                contradiction_score=0.0,
                total_latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "success", start)
            return {
                "final_output": {
                    "query": state["query"],
                    "answer": "LLM response contradicted the knowledge base.",
                    "sources": [],
                    "images": [],
                    "contradiction_score": contradiction_score,
                }
            }

        if not validate_answer_length(answer):
            log("validation_node invalid output length")
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                final_status="no_answer",
                top_k_chunks=len(top_chunks),
                context_chars=len(context) if context else 0,
                answer_chars=0,
                contradiction_score=0.0,
                total_latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "success", start)
            return {
                "final_output": {
                    "query": state["query"],
                    "answer": "Invalid LLM output.",
                    "sources": [],
                    "images": [],
                    "contradiction_score": contradiction_score,
                }
            }

        sources = [
            {
                "page_id": c["page_id"],
                "section": c["section"],
                "text": c["text"],
            }
            for c in top_chunks
        ]

        record_query_result(
            trace_id=trace_id,
            query=state["query"],
            final_status="success",
            top_k_chunks=len(top_chunks),
            context_chars=len(context),
            answer_chars=len(answer),
            contradiction_score=contradiction_score,
            total_latency_ms=int((time.time() - state["start_total"]) * 1000),
        )

        log(f"validation_node success sources={len(sources)} images={len(state.get('images') or [])}")
        safe_record_stage(trace_id, stage_name, "success", start)
        return {
            "final_output": {
                "query": state["query"],
                "answer": answer,
                "sources": sources,
                "images": state.get("images") or [],
                "contradiction_score": contradiction_score,
            }
        }

    except Exception as exc:
        log(f"validation_node error={exc}")
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {"final_output": None}

workflow = StateGraph(AgentState)
workflow.add_node("router", intent_router_node)
workflow.add_node("general_reply", general_reply_node)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate", generation_node)
workflow.add_node("validate", validation_node)

workflow.set_entry_point("router")

workflow.add_conditional_edges(
    "router",
    lambda x: x["intent"] or "general",
    {
        "knowledge": "retrieve",
        "general": "general_reply",
    },
)

workflow.add_edge("general_reply", END)
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", "validate")
workflow.add_edge("validate", END)
rag_app = workflow.compile()

app = FastAPI()

class QueryRequest(BaseModel):
    query: str
    history: List[Dict[str, str]] = Field(default_factory=list)

@app.on_event("startup")
def startup():
    log("startup begin")
    pc = get_pinecone_client()
    indexes = None

    if pc is not None:
        log("startup pinecone client ready")
        indexes = {
            "chunks": pc.Index("kb-chunks"),
            "pages": pc.Index("kb-pages"),
            "images": pc.Index("kb-images"),
        }
    else:
        log("startup pinecone client missing")

    app.state.indexes = indexes
    log("startup complete")

@app.post("/query")
@traceable(run_type="chain", name="RAG Query")
def run_query(req: QueryRequest):
    trace_id = str(uuid.uuid4())
    start_total = time.time()

    log(f"run_query start trace_id={trace_id} query_len={len(req.query) if req.query else 0}")

    conn = get_db_conn()
    if not conn:
        return {
            "query": req.query,
            "answer": "Database unavailable. Please try again later.",
            "sources": [],
            "images": [],
            "contradiction_score": 0.0,
        }

    try:
        inputs = {
            "query": req.query,
            "history": req.history,
            "trace_id": trace_id,
            "start_total": start_total,
            "indexes": app.state.indexes,
            "db_conn": conn,
            "intent": None,
            "top_chunks": None,
            "context": None,
            "answer": None,
            "web_findings": None,
            "images": None,
            "sources": None,
            "contradiction_score": None,
            "final_output": None,
        }

        result = rag_app.invoke(inputs)
        log(f"run_query rag_app.invoke done trace_id={trace_id}")

        final_output = result.get("final_output")
        if final_output is None:
            log(f"run_query final_output None trace_id={trace_id}")
            return {
                "query": req.query,
                "answer": "There was some issue processing your request. Please try again later.",
                "sources": [],
                "images": [],
                "contradiction_score": 0.0,
            }

        log(f"run_query success trace_id={trace_id}")
        return final_output

    finally:
        conn.close()
