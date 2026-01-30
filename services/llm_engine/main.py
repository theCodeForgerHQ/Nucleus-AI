import os
import logging
import time
from typing import List, Dict

import psycopg2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pinecone import Pinecone
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from guardrails import Guard
from guardrails.hub import ValidLength

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy
from datasets import Dataset

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.embeddings import HuggingFaceEmbeddings

load_dotenv()

PROMPTLAYER_API_KEY = os.environ["PROMPTLAYER_API_KEY"]
PROMPTLAYER_TRACK_REQUEST_URL = "https://api.promptlayer.com/track-request"

PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
NEON_DB_URL = os.environ["NEON_DB_URL"]
RERANKER_URL = os.environ["RERANKER_URL"]

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]

NLI_URL = os.environ["NLI_URL"]
NLI_CONTRADICTION_THRESHOLD = 0.5

KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"

TOP_K_CHUNKS = 50
TOP_K_PAGES = 20
FINAL_TOP_K = 8

W_CHUNK = 0.7
W_PAGE = 0.3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("query-service")

evaluation_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0
)

ragas_embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

ragas_metrics = [
    faithfulness,
    answer_relevancy
]

length_guard = Guard().use_many(
    ValidLength(min=20, max=4000)
)

pc = Pinecone(api_key=PINECONE_API_KEY)
chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)

app = FastAPI()

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=1
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

class QueryRequest(BaseModel):
    query: str

def search_with_text(index, index_name: str, text: str, top_k: int):
    start = time.time()
    response = index.search(
        namespace="default",
        query={
            "inputs": {"text": text},
            "top_k": top_k
        }
    )
    hits = response.get("result", {}).get("hits") or []
    logger.info(f"Pinecone search | index={index_name} | hits={len(hits)} | time={round(time.time()-start,2)}s")
    return {hit["_id"]: hit["_score"] for hit in hits}

def fetch_chunks_from_neon(chunk_ids: List[str]) -> Dict[str, dict]:
    if not chunk_ids:
        return {}
    placeholders = ",".join(["%s"] * len(chunk_ids))
    query = f"""
        SELECT chunk_hash, raw_chunk, section_path, page_id
        FROM kb_chunks
        WHERE chunk_hash IN ({placeholders})
    """
    start = time.time()
    with psycopg2.connect(NEON_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, chunk_ids)
            rows = cur.fetchall()
    logger.info(f"Neon fetch | rows={len(rows)} | time={round(time.time()-start,2)}s")
    result = {}
    for row in rows:
        result[row[0]] = {
            "text": row[1],
            "section": row[2],
            "page_id": row[3]
        }
    return result

def call_reranker(query: str, texts: List[str]) -> List[float]:
    payload = {"query": query, "texts": texts}
    start = time.time()
    r = http_session.post(RERANKER_URL, json=payload, timeout=120)
    r.raise_for_status()
    logger.info(f"Reranker completed | time={round(time.time()-start,2)}s")
    return r.json()["scores"]

def build_context(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        blocks.append(
            f"[Page ID: {c['page_id']}]\n"
            f"Section: {c['section']}\n"
            f"{c['text']}"
        )
    context = "\n\n".join(blocks)
    logger.info(f"Context built | chars={len(context)}")
    return context

def call_groq_llm(query: str, context: str) -> str:
    system_prompt = (
        "You are an internal company knowledge assistant. "
        "Answer only using the provided context. "
        "If the answer is not explicitly in the context, reply: "
        "'Not found in knowledge base.'"
    )
    user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 800
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    start = time.time()
    r = http_session.post(GROQ_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    answer = r.json()["choices"][0]["message"]["content"]
    logger.info(f"Groq completed | chars={len(answer)} | time={round(time.time()-start,2)}s")
    return answer

def validate_answer_length(answer: str) -> bool:
    result = length_guard.validate(answer)
    return result.validation_passed

def call_nli(premise: str, hypothesis: str) -> dict:
    payload = {
        "premise": premise,
        "hypothesis": hypothesis
    }
    start = time.time()
    r = http_session.post(NLI_URL, json=payload, timeout=60)
    r.raise_for_status()
    logger.info(f"NLI completed | time={round(time.time()-start,2)}s")
    return r.json()

def evaluate_with_ragas(query: str, answer: str, top_chunks: List[Dict]):
    request_id = os.urandom(4).hex()
    try:
        contexts = [c["text"] for c in top_chunks]
        dataset = Dataset.from_dict({
            "question": [query],
            "answer": [answer],
            "contexts": [contexts]
        })
        evaluate(
            dataset=dataset,
            metrics=ragas_metrics,
            llm=evaluation_llm,
            embeddings=ragas_embeddings
        )
    except Exception as e:
        logger.error(f"[{request_id}] RAGAS failed | {e}", exc_info=True)

@app.post("/query")
def run_query(req: QueryRequest, background_tasks: BackgroundTasks):
    request_id = os.urandom(4).hex()
    try:
        query = req.query
        chunk_scores = search_with_text(chunks_index, KB_CHUNKS_INDEX, query, TOP_K_CHUNKS)
        if not chunk_scores:
            return {"query": query, "answer": "Not found in knowledge base.", "sources": []}
        page_scores = search_with_text(pages_index, KB_PAGES_INDEX, query, TOP_K_PAGES)
        chunk_metadata = fetch_chunks_from_neon(list(chunk_scores.keys()))
        if not chunk_metadata:
            return {"query": query, "answer": "Not found in knowledge base.", "sources": []}
        fused = []
        for chunk_id, chunk_score in chunk_scores.items():
            if chunk_id not in chunk_metadata:
                continue
            meta = chunk_metadata[chunk_id]
            page_score = page_scores.get(str(meta["page_id"]), 0.0)
            fused.append({
                "chunk_id": chunk_id,
                "page_id": meta["page_id"],
                "section": meta["section"],
                "text": meta["text"],
                "fused_score": (W_CHUNK * chunk_score) + (W_PAGE * page_score)
            })
        texts = [f["text"] for f in fused]
        rerank_scores = call_reranker(query, texts)
        for item, score in zip(fused, rerank_scores):
            item["rerank_score"] = score
        fused.sort(key=lambda x: x["rerank_score"], reverse=True)
        top_chunks = fused[:FINAL_TOP_K]
        context = build_context(top_chunks)
        answer = call_groq_llm(query, context)
        nli_result = call_nli(context, answer)
        if nli_result.get("contradiction", 0.0) >= NLI_CONTRADICTION_THRESHOLD:
            return {"query": query, "answer": "LLM response contradicted the knowledge base.", "sources": []}
        if not validate_answer_length(answer):
            return {"query": query, "answer": "Invalid LLM output.", "sources": []}
        background_tasks.add_task(evaluate_with_ragas, query, answer, top_chunks)
        return {
            "query": query,
            "answer": answer,
            "sources": top_chunks
        }
    except Exception as e:
        logger.error(f"[{request_id}] Query failed | {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
