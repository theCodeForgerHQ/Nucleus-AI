import os
import time
import uuid
from typing import List, Dict, Optional, TypedDict
import logging
import psycopg2
import requests
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pinecone import Pinecone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from guardrails import Guard
from guardrails.hub import ValidLength
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.tools import DuckDuckGoSearchRun
from langsmith import traceable
import promptlayer
from langgraph.graph import StateGraph, END
from common.analytics import (
    record_stage_execution,
    record_query_result,
    init_analytics_schema,
)

load_dotenv()

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = os.environ.get("LANGCHAIN_API_KEY")
os.environ["LANGCHAIN_PROJECT"] = os.environ.get("LANGCHAIN_PROJECT")

promptlayer_client = promptlayer.PromptLayer()
OpenAI = promptlayer_client.openai.OpenAI
groq_client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

init_analytics_schema()

PROMPTLAYER_API_KEY = os.environ["PROMPTLAYER_API_KEY"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
NEON_DB_URL = os.environ["NEON_DB_URL"]
RERANKER_URL = os.environ["RERANKER_URL"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
NLI_URL = os.environ["NLI_URL"]

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NLI_CONTRADICTION_THRESHOLD = 0.7
IMAGE_SCORE_THRESHOLD = 0.5
KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"
KB_IMAGES_INDEX = "kb-images"
TOP_K_CHUNKS = 50
TOP_K_PAGES = 20
FINAL_TOP_K = 8
TOP_K_IMAGES = 5
W_CHUNK = 0.7
W_PAGE = 0.3

evaluation_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0,
)

length_guard = Guard().use_many(
    ValidLength(min=20, max=4000)
)

pc = Pinecone(api_key=PINECONE_API_KEY)
chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)
images_index = pc.Index(KB_IMAGES_INDEX)

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=1,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

@traceable
def search_with_text(trace_id, index, text: str, top_k: int):
    start = time.time()
    try:
        response = index.search(
            namespace="default",
            query={"inputs": {"text": text}, "top_k": top_k},
        )
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="vector_search",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        hits = response.get("result", {}).get("hits") or []
        return {hit["_id"]: hit["_score"] for hit in hits}
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="vector_search",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def fetch_chunks_from_neon(trace_id, chunk_ids: List[str]) -> Dict[str, dict]:
    start = time.time()
    try:
        if not chunk_ids:
            return {}
        placeholders = ",".join(["%s"] * len(chunk_ids))
        query = f"""
            SELECT chunk_hash, raw_chunk, section_path, page_id
            FROM kb_chunks
            WHERE chunk_hash IN ({placeholders})
        """
        with psycopg2.connect(NEON_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, chunk_ids)
                rows = cur.fetchall()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_chunks",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return {
            row[0]: {"text": row[1], "section": row[2], "page_id": row[3]}
            for row in rows
        }
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_chunks",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def fetch_images_from_neon(trace_id, image_ids: List[str]) -> List[Dict]:
    start = time.time()
    try:
        if not image_ids:
            return []
        placeholders = ",".join(["%s"] * len(image_ids))
        query = f"""
            SELECT image_hash, page_id, image_src, caption
            FROM kb_images
            WHERE image_hash IN ({placeholders})
        """
        with psycopg2.connect(NEON_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, image_ids)
                rows = cur.fetchall()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_images",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return [
            {"image_hash": row[0], "page_id": row[1], "url": row[2], "caption": row[3]}
            for row in rows
        ]
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="fetch_images",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def call_reranker(trace_id, query: str, texts: List[str]) -> List[float]:
    start = time.time()
    try:
        r = http_session.post(
            RERANKER_URL,
            json={"query": query, "texts": texts},
            timeout=120,
        )
        r.raise_for_status()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="rerank",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return r.json()["scores"]
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="rerank",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

def build_context(chunks: list[dict]) -> str:
    return "\n\n".join(
        f"[Page ID: {c['page_id']}]\nSection: {c['section']}\n{c['text']}"
        for c in chunks
    )

@traceable
def call_groq_llm(trace_id, query: str, context: str, history: List[Dict[str, str]] = []) -> str:
    start = time.time()
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an internal company knowledge assistant. "
                    "Answer only using the provided context. "
                    "If the answer is not explicitly in the context, reply: "
                    "'Not found in knowledge base.'"
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
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )
        answer = response.choices[0].message.content
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="llm_generate",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return answer
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="llm_generate",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def validate_answer_length(trace_id, answer: str) -> bool:
    start = time.time()
    result = length_guard.validate(answer)
    record_stage_execution(
        trace_id=trace_id,
        pipeline="query",
        stage_name="length_check",
        status="success" if result.validation_passed else "failure",
        latency_ms=int((time.time() - start) * 1000),
    )
    return result.validation_passed

@traceable
def call_nli(trace_id, premise: str, hypothesis: str) -> dict:
    start = time.time()
    try:
        r = http_session.post(
            NLI_URL,
            json={"premise": premise, "hypothesis": hypothesis},
            timeout=60,
        )
        r.raise_for_status()
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="contradiction_check",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return r.json()
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="contradiction_check",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise

@traceable
def call_web_search_fallback(trace_id, query: str) -> str:
    start = time.time()
    try:
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
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
        )
        summary = response.choices[0].message.content
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="web_search",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return summary
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="query",
            stage_name="web_search",
            status="failure",
            latency_ms=int((time.time() - start) * 1000),
        )
        return "Web search information could not be retrieved at this time."

class AgentState(TypedDict):
    query: str
    history: List[Dict[str, str]]
    trace_id: str
    start_total: float
    top_chunks: List[dict]
    context: str
    answer: str
    web_findings: Optional[str]
    images: List[dict]
    sources: List[dict]
    contradiction_score: float
    final_output: Optional[dict]

def retrieve_node(state: AgentState):
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

def _get_images(trace_id, query, context):
    try:
        image_search_input = f"{query}\n\n{context}"
        image_scores = search_with_text(trace_id, images_index, image_search_input, 20)
        if not image_scores:
            return []
        
        filtered_ids = [img_id for img_id, score in image_scores.items() if score >= IMAGE_SCORE_THRESHOLD]
        if not filtered_ids:
            return []

        fetched_images = fetch_images_from_neon(trace_id, filtered_ids)
        ordered_images = sorted(fetched_images, key=lambda img: image_scores.get(img.get("image_hash"), 0), reverse=True)
        
        return [{"url": img.get("url"), "page_id": img.get("page_id"), "caption": img.get("caption")} for img in ordered_images[:TOP_K_IMAGES]]
    except Exception:
        return []

def generation_node(state: AgentState):
    if not state["top_chunks"]:
        record_query_result(state["trace_id"], state["query"], "no_context", 0, 0, 0, 0.0, int((time.time() - state["start_total"]) * 1000))
        web_findings = call_web_search_fallback(state["trace_id"], state["query"])
        return {"final_output": {"query": state["query"], "answer": "Not found in knowledge base.", "web_findings": web_findings, "sources": [], "images": []}}

    with ThreadPoolExecutor() as executor:
        llm_future = executor.submit(call_groq_llm, state["trace_id"], state["query"], state["context"], state["history"])
        image_future = executor.submit(_get_images, state["trace_id"], state["query"], state["context"])
        
        answer = llm_future.result()
        images = image_future.result()

    web_findings = None
    if "Not found in knowledge base" in answer:
        web_findings = call_web_search_fallback(state["trace_id"], state["query"])
        
    return {"answer": answer, "web_findings": web_findings, "images": images}

def validation_node(state: AgentState):
    if state.get("final_output"): return state
    nli = call_nli(state["trace_id"], state["context"], state["answer"])
    contradiction_score = nli.get("contradiction", 0.0)
    if contradiction_score >= NLI_CONTRADICTION_THRESHOLD:
        record_query_result(state["trace_id"], state["query"], "contradicted", len(state["top_chunks"]), len(state["context"]), len(state["answer"]), contradiction_score, int((time.time() - state["start_total"]) * 1000))
        return {"final_output": {"query": state["query"], "answer": "LLM response contradicted the knowledge base.", "sources": [], "images": []}}
    if not validate_answer_length(state["trace_id"], state["answer"]):
        record_query_result(state["trace_id"], state["query"], "invalid_output", len(state["top_chunks"]), len(state["context"]), len(state["answer"]), contradiction_score, int((time.time() - state["start_total"]) * 1000))
        return {"final_output": {"query": state["query"], "answer": "Invalid LLM output.", "sources": [], "images": []}}
    total_latency_ms = int((time.time() - state["start_total"]) * 1000)
    record_query_result(state["trace_id"], state["query"], "success", len(state["top_chunks"]), len(state["context"]), len(state["answer"]), contradiction_score, total_latency_ms)
    sources = [{"page_id": c["page_id"], "section": c["section"], "text": c["text"]} for c in state["top_chunks"]]
    return {"final_output": {"query": state["query"], "answer": state["answer"], "web_findings": state.get("web_findings"), "sources": sources, "images": state.get("images", [])}}

workflow = StateGraph(AgentState)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate", generation_node)
workflow.add_node("validate", validation_node)
workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", "validate")
workflow.add_edge("validate", END)
rag_app = workflow.compile()

app = FastAPI()

class QueryRequest(BaseModel):
    query: str
    history: List[Dict[str, str]] = []

@app.post("/query")
@traceable(run_type="chain", name="RAG Query")
def run_query(req: QueryRequest):
    trace_id = str(uuid.uuid4())
    start_total = time.time()
    try:
        inputs = {"query": req.query, "history": req.history, "trace_id": trace_id, "start_total": start_total, "top_chunks": [], "context": "", "answer": "", "web_findings": None, "images": [], "sources": [], "contradiction_score": 0.0, "final_output": None}
        result = rag_app.invoke(inputs)
        return result["final_output"]
    except Exception as e:
        record_query_result(trace_id, req.query, "failure", 0, 0, 0, 0.0, int((time.time() - start_total) * 1000))
        raise HTTPException(status_code=500, detail=str(e))