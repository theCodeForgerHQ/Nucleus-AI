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

length_guard = Guard().use_many(
    ValidLength(min=20, max=4000)
)

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
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name=stage_name,
            status=status,
            latency_ms=int((time.time() - start) * 1000),
        )
        return True
    except Exception:
        return False

def search_with_text(index, text, top_k):
    try:
        response = index.search(
            namespace="default",
            query={"inputs": {"text": text}, "top_k": top_k},
        )
        hits = response.get("result", {}).get("hits") or []
        return {hit["_id"]: hit["_score"] for hit in hits}

    except Exception:
        return None

def fetch_chunks_from_neon(conn, chunk_hash):
    try:
        if not chunk_hash:
            return {}

        placeholders = ",".join(["%s"] * len(chunk_hash))

        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT chunk_hash, raw_chunk, section_path, page_id, is_active
                FROM kb_chunks
                WHERE chunk_hash IN ({placeholders})
                """,
                chunk_hash,
            )
            rows = cur.fetchall()
        
        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3], "is_active": row[4]}
            for row in rows
        }
    except Exception:
        return None

def fetch_images_from_neon(conn, image_hash):
    try:
        if not image_hash:
            return {}

        placeholders = ",".join(["%s"] * len(image_hash))

        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT image_hash, page_id, image_src, caption
                FROM kb_images
                WHERE image_hash IN ({placeholders})
                """,
                image_hash,
            )
            rows = cur.fetchall()
        
        return {
            row[0]: {"page_id": row[1], "url": row[2], "caption": row[3]}
            for row in rows
        }
    except Exception:
        return None

def call_reranker(query, texts):
    try:
        reranker_url = get_env("RERANKER_URL")
        if not reranker_url:
            return None

        r = http_session.post(
            reranker_url,
            json={"query": query, "texts": texts},
            timeout=120,
        )
        r.raise_for_status()

        return r.json()["scores"]
    except Exception:
        return None

def build_context(chunks):
    try:
        if not chunks:
            return ""
        return "\n\n".join(
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
    except Exception:
        return None

def call_groq_llm(query, context, history=None):
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model or context is None:
            return None

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an internal company knowledge assistant. "
                    "Answer only using the provided context. "
                    "If the answer is not explicitly in the context, reply: "
                    "'Not found in knowledge base.' "
                    "After answering, always add one relevant follow-up question "
                    "based only on the context and the user's question."
                ),
            }
        ]

        if history:
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
            return None

        answer = choices[0].message.content
        return answer

    except Exception:
        return None

def call_nli(premise, hypothesis):
    try:
        nli_url = get_env("NLI_URL")

        if not nli_url:
            return None
        
        r = http_session.post(
            nli_url,
            json={"premise": premise, "hypothesis": hypothesis},
            timeout=60,
        )
        r.raise_for_status()

        return r.json()
    except Exception:
        return None

def call_web_search_fallback(query):
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model:
            return None

        search = DuckDuckGoSearchRun()
        web_results = search.run(query)
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

        return summary
    except Exception:
        return None

def validate_answer_length(answer):
    try:
        result = length_guard.validate(answer)
        return result.validation_passed
    except Exception:
        return False

def classify_intent(query):
    try:
        groq_model = get_env("GROQ_MODEL")
        if groq_model is None:
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
            return None

        intent = choices[0].message.content.strip().lower()

        if intent not in ("knowledge", "general"):
            return None

        return intent

    except Exception:
        return None

def get_images(conn, images_index, query, context):
    IMAGE_SCORE_THRESHOLD = 0.15
    TOP_K_IMAGES = 5
    try:
        image_search_input = f"{query}\n\n{context}"
        image_scores = search_with_text(images_index, image_search_input, 20)
        if not image_scores:
            return None

        filtered_ids = [
            img_id for img_id, score in image_scores.items()
            if score >= IMAGE_SCORE_THRESHOLD
        ]
        if not filtered_ids:
            return None

        fetched_images = fetch_images_from_neon(conn, filtered_ids)
        if not fetched_images:
            return None

        ordered_images = sorted(
            fetched_images.items(),
            key=lambda kv: image_scores.get(kv[0], 0),
            reverse=True,
        )

        return [
            {
                "url": img["url"],
                "page_id": img["page_id"],
                "caption": img["caption"],
            }
            for _, img in ordered_images[:TOP_K_IMAGES]
        ]

    except Exception:
        return None

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
        intent = classify_intent(state["query"])
        if intent is None:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"intent": None}

        safe_record_stage(trace_id, stage_name, "success", start)
        return {"intent": intent}

    except Exception:
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {"intent": None}

@traceable
def general_reply_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    fallback = { "final_output": None }

    try:
        groq_model = get_env("GROQ_MODEL")
        if groq_model is None:
            safe_record_stage(trace_id, "general_reply", "failure", start)
            return fallback
        
        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful company internal knowledge assistant. "
                        "Greet the user or respond to their general query politely. "
                        "After your response, always include a proactive follow-up question "
                        "inviting them to ask about Alphabet, Google products, or company updates."
                    )
                },
                {"role": "user", "content": state["query"]}
            ],
            temperature=0.7,
            max_tokens=400,
        )

        choices = response.choices
        if not choices:
            safe_record_stage(trace_id, "general_reply", "failure", start)
            return fallback

        answer = choices[0].message.content

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

    except Exception:
        safe_record_stage(trace_id, "general_reply", "failure", start)
        return fallback

@traceable
def retrieve_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "retrieve" 
    THRESHOLD = 0.6

    try:
        indexes = state["indexes"]
        if not indexes:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        if state["db_conn"] is None:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        chunks_index = indexes["chunks"]
        pages_index = indexes["pages"]

        TOP_K_CHUNKS = 50
        TOP_K_PAGES = 20
        FINAL_TOP_K = 10
        W_CHUNK = 0.7
        W_PAGE = 0.3

        chunk_scores = search_with_text(
            chunks_index, state["query"], TOP_K_CHUNKS
        )
        if not chunk_scores:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        page_scores = search_with_text(
            pages_index, state["query"], TOP_K_PAGES
        ) or {}

        chunk_metadata = fetch_chunks_from_neon(
            state["db_conn"], list(chunk_scores.keys())
        )
        if not chunk_metadata:
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
                "fused_score": (W_CHUNK * chunk_score) + (W_PAGE * page_score),
            })

        if not fused:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        rerank_scores = call_reranker(
            state["query"], [f["text"] for f in fused]
        )
        if not rerank_scores:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        ALPHA = 0.7

        for item, score in zip(fused, rerank_scores):
            item["rerank_score"] = score
            item["final_score"] = (
                ALPHA * score +
                (1 - ALPHA) * item["fused_score"]
            )

        fused.sort(key=lambda x: x["final_score"], reverse=True)
        top_chunks = fused[:FINAL_TOP_K]
        
        filtered = [f for f in top_chunks if f["final_score"] >= THRESHOLD]

        if not filtered:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        context = build_context(filtered)
        if context is None:
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"top_chunks": None, "context": None}

        safe_record_stage(trace_id, stage_name, "success", start)
        return {
            "top_chunks": filtered,
            "context": context,
        }

    except Exception:
        safe_record_stage(trace_id, stage_name, "failure", start)
        return {"top_chunks": None, "context": None}

@traceable
def generation_node(state: AgentState):
    start = time.time()
    trace_id = state["trace_id"]
    stage_name = "generation"

    try:
        if not state["top_chunks"]:
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
                state["db_conn"],
                state["indexes"]["images"],
                state["query"],
                state["context"],
            )

            answer = llm_future.result()
            images = image_future.result()

        if answer is None:
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
            web_findings = call_web_search_fallback(state["query"])

        safe_record_stage(trace_id, stage_name, "success", start)
        return {
            "answer": answer,
            "images": images or [],
            "web_findings": web_findings,
            "contradiction_score": 0.0,
            "final_output": None,
        }

    except Exception:
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
        answer = state.get("answer")
        context = state.get("context")
        top_chunks = state.get("top_chunks") or []

        if answer is None or context is None:
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                status="no_answer",
                n_chunks=len(top_chunks),
                n_sources=0,
                n_images=0,
                contradiction_score=0.0,
                latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"final_output": None}

        nli = call_nli(context, answer)
        if not nli:
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                status="nli_failed",
                n_chunks=len(top_chunks),
                n_sources=0,
                n_images=0,
                contradiction_score=0.0,
                latency_ms=int((time.time() - state["start_total"]) * 1000),
            )
            safe_record_stage(trace_id, stage_name, "failure", start)
            return {"final_output": None}

        contradiction_score = nli.get("contradiction", 0.0)

        if contradiction_score >= NLI_CONTRADICTION_THRESHOLD:
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                status="contradicted",
                n_chunks=len(top_chunks),
                n_sources=0,
                n_images=0,
                contradiction_score=contradiction_score,
                latency_ms=int((time.time() - state["start_total"]) * 1000),
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
            record_query_result(
                trace_id=trace_id,
                query=state["query"],
                status="invalid_output",
                n_chunks=len(top_chunks),
                n_sources=0,
                n_images=0,
                contradiction_score=contradiction_score,
                latency_ms=int((time.time() - state["start_total"]) * 1000),
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
            status="success",
            n_chunks=len(top_chunks),
            n_sources=len(sources),
            n_images=len(state.get("images") or []),
            contradiction_score=contradiction_score,
            latency_ms=int((time.time() - state["start_total"]) * 1000),
        )

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

    except Exception:
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
    pc = get_pinecone_client()
    db_conn = get_db_conn()

    indexes = None
    if pc is not None:
        indexes = {
            "chunks": pc.Index("kb-chunks"),
            "pages": pc.Index("kb-pages"),
            "images": pc.Index("kb-images"),
        }

    app.state.indexes = indexes
    app.state.db_conn = db_conn

@app.post("/query")
@traceable(run_type="chain", name="RAG Query")
def run_query(req: QueryRequest):
    trace_id = str(uuid.uuid4())
    start_total = time.time()

    inputs = {
        "query": req.query,
        "history": req.history,
        "trace_id": trace_id,
        "start_total": start_total,
        "indexes": app.state.indexes,
        "db_conn": app.state.db_conn,
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

    final_output = result.get("final_output")
    if final_output is None:
        return {
            "query": req.query,
            "answer": "There was some issue processing your request. Please try again later.",
            "sources": [],
            "images": [],
            "contradiction_score": 0.0,
        }

    return final_output
