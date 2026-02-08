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

@traceable
def fetch_chunks_from_neon(trace_id, conn, chunk_hash):
    start = time.time()
    try:
        if not chunk_hash:
            safe_record_stage(trace_id, "fetch_chunks", "success", start)
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
        
        safe_record_stage(trace_id, "fetch_chunks", "success", start)

        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3]}
            for row in rows
        }
    except Exception:
        safe_record_stage(trace_id, "fetch_chunks", "failure", start)
        return None

@traceable
def fetch_images_from_neon(trace_id, conn, image_hash):
    start = time.time()
    try:
        if not image_hash:
            safe_record_stage(trace_id, "fetch_images", "success", start)
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
        
        safe_record_stage(trace_id, "fetch_images", "success", start)

        return {
            row[0]: {"page_id": row[1], "url": row[2], "caption": row[3]}
            for row in rows
        }
    except Exception:
        safe_record_stage(trace_id, "fetch_images", "failure", start)
        return None

@traceable
def call_reranker(trace_id, query, texts):
    start = time.time()
    try:
        reranker_url = get_env("RERANKER_URL")
        if not reranker_url:
            safe_record_stage(trace_id, "reranker", "failure", start)
            return None

        r = http_session.post(
            reranker_url,
            json={"query": query, "texts": texts},
            timeout=120,
        )
        r.raise_for_status()

        safe_record_stage(trace_id, "reranker", "success", start)

        return r.json()["scores"]
    except Exception:
        safe_record_stage(trace_id, "reranker", "failure", start)
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

@traceable
def call_groq_llm(trace_id, query, context, history=[]):
    start = time.time()
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model or context is None:
            safe_record_stage(trace_id, "llm_generate", "failure", start)
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
            safe_record_stage(trace_id, "llm_generate", "failure", start)
            return None

        answer = choices[0].message.content

        safe_record_stage(trace_id, "llm_generate", "success", start)
        return answer

    except Exception:
        safe_record_stage(trace_id, "llm_generate", "failure", start)
        return None

@traceable
def call_nli(trace_id, premise, hypothesis):
    start = time.time()
    try:
        nli_url = get_env("NLI_URL")

        if not nli_url:
            safe_record_stage(trace_id, "contradiction_check", "failure", start)
            return None
        
        r = http_session.post(
            nli_url,
            json={"premise": premise, "hypothesis": hypothesis},
            timeout=60,
        )
        r.raise_for_status()

        safe_record_stage(trace_id, "contradiction_check", "success", start)
        return r.json()
    except Exception:
        safe_record_stage(trace_id, "contradiction_check", "failure", start)
        return None

@traceable
def call_web_search_fallback(trace_id, query):
    start = time.time()
    try:
        groq_model = get_env("GROQ_MODEL")
        if not groq_model:
            safe_record_stage(trace_id, "web_search", "failure", start)
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

        safe_record_stage(trace_id, "web_search", "success", start)
        return summary
    except Exception:
        safe_record_stage(trace_id, "web_search", "failure", start)
        return "Web search information could not be retrieved at this time."

@traceable
def validate_answer_length(trace_id, answer):
    start = time.time()
    try:
        result = length_guard.validate(answer)
        safe_record_stage(
            trace_id,
            "answer_validation",
            "success" if result.validation_passed else "failure",
            start,
        )
        return result.validation_passed
    except Exception:
        safe_record_stage(trace_id, "answer_validation", "failure", start)
        return False

@traceable
def classify_intent(trace_id, query):
    start = time.time()

    try:
        groq_model = get_env("GROQ_MODEL")
        if groq_model is None:
            safe_record_stage(trace_id, "classify_intent", "failure", start)
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
            safe_record_stage(trace_id, "classify_intent", "failure", start)
            return None

        intent = choices[0].message.content.strip().lower()

        if intent not in ("knowledge", "general"):
            safe_record_stage(trace_id, "classify_intent", "failure", start)
            return None

        safe_record_stage(trace_id, "classify_intent", "success", start)
        return intent

    except Exception:
        safe_record_stage(trace_id, "classify_intent", "failure", start)
        return None

@traceable
def get_images(trace_id, images_index, query, context):
    start = time.time()
    IMAGE_SCORE_THRESHOLD = 0.15
    TOP_K_IMAGES = 5
    try:
        image_search_input = f"{query}\n\n{context}"
        image_scores = search_with_text(images_index, image_search_input, 20)
        if not image_scores:
            safe_record_stage(trace_id, "image_search", "failure", start)
            return None

        filtered_ids = [img_id for img_id, score in image_scores.items() if score >= IMAGE_SCORE_THRESHOLD]
        if not filtered_ids:
            safe_record_stage(trace_id, "image_search", "failure", start)
            return None

        fetched_images = fetch_images_from_neon(trace_id, filtered_ids)
        if not fetched_images:
            safe_record_stage(trace_id, "image_search", "failure", start)
            return None

        ordered_images = sorted(fetched_images, key=lambda img: image_scores.get(img.get("image_hash"), 0), reverse=True)

        safe_record_stage(trace_id, "image_search", "success", start)
        return [{"url": img.get("url"), "page_id": img.get("page_id"), "caption": img.get("caption")} for img in ordered_images[:TOP_K_IMAGES]]
    except Exception:
        safe_record_stage(trace_id, "image_search", "failure", start)
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
