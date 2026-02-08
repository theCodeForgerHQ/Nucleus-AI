from common.utils import get_env, get_db_conn, get_pinecone_client
import time
from typing import List, Dict, Optional, TypedDict
from langsmith import traceable
from common.analytics import record_stage_execution
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
import promptlayer
from langchain_community.tools import DuckDuckGoSearchRun
from guardrails import Guard
from guardrails.hub import ValidLength

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
                SELECT chunk_hash, raw_chunk, section_path, page_id
                FROM kb_chunks
                WHERE chunk_hash IN ({placeholders})
                """,
                chunk_hash,
            )
            rows = cur.fetchall()
        
        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3]}
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
            f"[Page ID: {c['page_id']}]\nSection: {c['section']}\n{c['text']}"
            for c in chunks
        )
    except Exception:
        return None

def call_groq_llm(query, context, history=[]):
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
            return "Web search information could not be retrieved at this time."

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
        return "Web search information could not be retrieved at this time."

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

def get_images(images_index, query, context):
    IMAGE_SCORE_THRESHOLD = 0.15
    TOP_K_IMAGES = 5
    try:
        image_search_input = f"{query}\n\n{context}"
        image_scores = search_with_text(images_index, image_search_input, 20)
        if not image_scores:
            return None

        filtered_ids = [img_id for img_id, score in image_scores.items() if score >= IMAGE_SCORE_THRESHOLD]
        if not filtered_ids:
            return None

        fetched_images = fetch_images_from_neon(trace_id, filtered_ids)
        if not fetched_images:
            return None

        ordered_images = sorted(fetched_images, key=lambda img: image_scores.get(img.get("image_hash"), 0), reverse=True)
        return [{"url": img.get("url"), "page_id": img.get("page_id"), "caption": img.get("caption")} for img in ordered_images[:TOP_K_IMAGES]]
    except Exception:
        return None

class FinalAnswer(TypedDict):
    answer: str
    sources: List[dict]
    images: List[dict]
    contradiction_score: float

class AgentState(TypedDict):
    query: str
    history: List[Dict[str, str]]
    trace_id: str
    start_total: float

    intent: Optional[str]

    top_chunks: Optional[List[dict]]
    web_findings: Optional[str]
    sources: Optional[List[dict]]
    images: Optional[List[dict]]

    context: Optional[str]
    answer: Optional[str]
    contradiction_score: Optional[float]

    final_output: Optional[FinalAnswer]

def intent_router_node(state: AgentState):
    intent = classify_intent(state["trace_id"], state["query"])
    return {"intent": intent}

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
                "answer": answer,
                "sources": [],
                "images": [],
                "contradiction_score": 0.0,
            }
        }

    except Exception:
        safe_record_stage(trace_id, "general_reply", "failure", start)
        return fallback

def retrieve_node(state: AgentState):
    KB_CHUNKS_INDEX = "kb-chunks"
    KB_PAGES_INDEX = "kb-pages"
    KB_IMAGES_INDEX = "kb-images"
    TOP_K_CHUNKS = 50
    TOP_K_PAGES = 20
    FINAL_TOP_K = 8
    W_CHUNK = 0.7
    W_PAGE = 0.3
    chunk_scores = search_with_text(state["trace_id"], chunks_index, state["query"], TOP_K_CHUNKS)
    page_scores = search_with_text(state["trace_id"], pages_index, state["query"], TOP_K_PAGES)
    chunk_metadata = fetch_chunks_from_neon(state["trace_id"], list(chunk_scores.keys()))
    
    fused = []
    for chunk_id, chunk_score in chunk_scores.items():
        if chunk_id not in chunk_metadata: continue
        meta = chunk_metadata[chunk_id]
        page_score = page_scores.get(str(meta["page_id"]), 0.0)
        fused.append({
            "chunk_id": chunk_id, "page_id": meta["page_id"], "section": meta["section"],
            "text": meta["text"], "fused_score": (W_CHUNK * chunk_score) + (W_PAGE * page_score),
        })
    
    if not fused:
        return {"top_chunks": [], "context": ""}
        
    rerank_scores = call_reranker(state["trace_id"], state["query"], [f["text"] for f in fused])
    for item, score in zip(fused, rerank_scores):
        item["rerank_score"] = score
    
    fused.sort(key=lambda x: x["rerank_score"], reverse=True)
    top_chunks = fused[:FINAL_TOP_K]
    return {"top_chunks": top_chunks, "context": build_context(top_chunks)}
