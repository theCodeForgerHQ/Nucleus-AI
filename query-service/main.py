import os
import logging
import time
from typing import List, Dict

import psycopg2
import requests
from pinecone import Pinecone
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from guardrails import Guard
from guardrails.hub import ValidLength

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("query-service")

PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
NEON_DB_URL = os.environ["NEON_DB_URL"]
RERANKER_URL = os.environ["RERANKER_URL"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# NEW: Local NLI service exposed via ngrok
LOCAL_NLI_URL = os.environ["LOCAL_NLI_URL"]  
# Example: https://e23e24116767.ngrok-free.app/nli

KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"

TOP_K_CHUNKS = 50
TOP_K_PAGES = 20
FINAL_TOP_K = 8

W_CHUNK = 0.7
W_PAGE = 0.3

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

length_guard = Guard().use_many(
    ValidLength(min=20, max=4000)
)

pc = Pinecone(api_key=PINECONE_API_KEY)

chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)

app = FastAPI()

class QueryRequest(BaseModel):
    query: str


def search_with_text(index, index_name: str, text: str, top_k: int):
    logger.info(f"Pinecone search | index={index_name} | top_k={top_k}")
    start = time.time()

    response = index.search(
        namespace="default",
        query={
            "inputs": {"text": text},
            "top_k": top_k
        }
    )

    hits = response.get("result", {}).get("hits") or []
    logger.info(f"Pinecone results | index={index_name} | hits={len(hits)} | time={round(time.time()-start,2)}s")

    return {hit["_id"]: hit["_score"] for hit in hits}


def fetch_chunks_from_neon(chunk_ids: List[str]) -> Dict[str, dict]:
    logger.info(f"Neon fetch | chunk_ids={len(chunk_ids)}")
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

    logger.info(f"Neon returned | rows={len(rows)} | time={round(time.time()-start,2)}s")

    result = {}
    for row in rows:
        result[row[0]] = {
            "text": row[1],
            "section": row[2],
            "page_id": row[3]
        }
    return result


def call_reranker(query: str, texts: List[str]) -> List[float]:
    logger.info(f"Reranker call | candidates={len(texts)}")
    payload = {"query": query, "texts": texts}

    start = time.time()
    r = requests.post(RERANKER_URL, json=payload, timeout=120)

    if r.status_code != 200:
        logger.error(f"Reranker error | {r.text}")
        raise RuntimeError(r.text)

    logger.info(f"Reranker success | time={round(time.time()-start,2)}s")
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
        "If the answer is not explicitly in the context, reply: 'Not found in knowledge base.' "
        "Do not use outside knowledge."
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

    logger.info("Groq call started")
    start = time.time()

    r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=120)

    if r.status_code != 200:
        logger.error(f"Groq error | {r.text}")
        raise RuntimeError(r.text)

    answer = r.json()["choices"][0]["message"]["content"]
    logger.info(f"Groq success | answer_chars={len(answer)} | time={round(time.time()-start,2)}s")
    return answer


def validate_answer_length(answer: str) -> bool:
    logger.info("Running length guardrail")
    result = length_guard.validate(answer)
    return result.validation_passed


# ✅ UPDATED: Call your local NLI service via ngrok
def verify_answer_with_nli(context: str, answer: str, threshold: float = 0.75) -> bool:
    logger.info("Local NLI verification started")
    start = time.time()

    payload = {
        "context": context,
        "answer": answer
    }

    try:
        r = requests.post(LOCAL_NLI_URL, json=payload, timeout=120)
    except Exception as e:
        logger.error(f"Local NLI request failed: {e}")
        return False

    if r.status_code != 200:
        logger.error(f"Local NLI error | {r.text}")
        return False

    result = r.json()
    entailment_score = result["entailment_score"]

    logger.info(f"Local NLI entailment score = {entailment_score} | time={round(time.time()-start,2)}s")

    return entailment_score >= threshold


@app.post("/query")
def run_query(req: QueryRequest):
    request_id = os.urandom(4).hex()
    logger.info(f"[{request_id}] Query received")

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
            fused_score = (W_CHUNK * chunk_score) + (W_PAGE * page_score)

            fused.append({
                "chunk_id": chunk_id,
                "page_id": meta["page_id"],
                "section": meta["section"],
                "text": meta["text"],
                "fused_score": fused_score
            })

        texts = [f["text"] for f in fused]
        rerank_scores = call_reranker(query, texts)

        for item, score in zip(fused, rerank_scores):
            item["rerank_score"] = score

        fused.sort(key=lambda x: x["rerank_score"], reverse=True)
        top_chunks = fused[:FINAL_TOP_K]

        context = build_context(top_chunks)

        answer = call_groq_llm(query, context)

        if not validate_answer_length(answer):
            logger.warning("Length guardrail blocked output")
            return {
                "query": query,
                "answer": "LLM produced invalid output. Please retry.",
                "sources": []
            }

        if not verify_answer_with_nli(context, answer):
            logger.warning("NLI guard blocked hallucinated answer")
            return {
                "query": query,
                "answer": "Answer could not be verified against knowledge base.",
                "sources": []
            }

        logger.info(f"[{request_id}] Query completed successfully")

        return {
            "query": query,
            "answer": answer,
            "sources": top_chunks
        }

    except Exception as e:
        logger.error(f"[{request_id}] Query failed | {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
