import os
import logging
from typing import List, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import psycopg2
import requests
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("query-service")

PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
NEON_DB_URL = os.environ["NEON_DB_URL"]
RERANKER_URL = os.environ["RERANKER_URL"]

KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"

TOP_K_CHUNKS = 50
TOP_K_PAGES = 20
FINAL_TOP_K = 20

W_CHUNK = 0.7
W_PAGE = 0.3

logger.info("Initializing Pinecone client")
pc = Pinecone(api_key=PINECONE_API_KEY)

chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)

app = FastAPI()

class QueryRequest(BaseModel):
    query: str

def search_with_text(index, index_name: str, text: str, top_k: int):
    logger.info(f"Searching Pinecone index='{index_name}' query='{text}' top_k={top_k}")

    response = index.search(
        namespace="default",
        query={
            "inputs": {"text": text},
            "top_k": top_k
        }
    )

    hits = response.get("result", {}).get("hits") or []
    logger.info(f"Pinecone index='{index_name}' returned {len(hits)} hits")

    return {hit["_id"]: hit["_score"] for hit in hits}

def fetch_chunks_from_neon(chunk_ids: List[str]) -> Dict[str, dict]:
    logger.info(f"Fetching {len(chunk_ids)} chunks from Neon")

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

    logger.info(f"Neon returned {len(rows)} chunk rows")

    result = {}
    for row in rows:
        result[row[0]] = {
            "text": row[1],
            "section": row[2],
            "page_id": row[3]
        }

    return result

def call_reranker(query: str, texts: list[str]) -> list[float]:
    logger.info(f"Calling reranker for {len(texts)} candidates")

    payload = {"query": query, "texts": texts}
    r = requests.post(RERANKER_URL, json=payload, timeout=60)

    if r.status_code != 200:
        logger.error(f"Reranker error: {r.text}")
        raise RuntimeError(r.text)

    scores = r.json()["scores"]
    logger.info("Reranker returned scores successfully")

    return scores


@app.post("/query")
def run_query(req: QueryRequest):
    try:
        query = req.query
        logger.info(f"Received query: {query}")

        chunk_scores = search_with_text(chunks_index, KB_CHUNKS_INDEX, query, TOP_K_CHUNKS)
        if not chunk_scores:
            logger.warning("No chunk matches found")
            return {"query": query, "results": []}

        page_scores = search_with_text(pages_index, KB_PAGES_INDEX, query, TOP_K_PAGES)

        chunk_metadata = fetch_chunks_from_neon(list(chunk_scores.keys()))
        if not chunk_metadata:
            logger.warning("No matching chunk metadata found in Neon")
            return {"query": query, "results": []}

        fused_candidates = []

        for chunk_id, chunk_score in chunk_scores.items():
            if chunk_id not in chunk_metadata:
                continue

            meta = chunk_metadata[chunk_id]
            page_id = meta["page_id"]

            page_score = page_scores.get(str(page_id), 0.0)
            fused_score = (W_CHUNK * chunk_score) + (W_PAGE * page_score)

            fused_candidates.append({
                "chunk_id": chunk_id,
                "page_id": page_id,
                "section": meta["section"],
                "text": meta["text"],
                "fused_score": fused_score
            })

        if not fused_candidates:
            logger.warning("Fusion produced no candidates")
            return {"query": query, "results": []}

        logger.info(f"Fusion produced {len(fused_candidates)} candidates")

        texts = [item["text"] for item in fused_candidates]
        rerank_scores = call_reranker(query, texts)

        for item, score in zip(fused_candidates, rerank_scores):
            item["rerank_score"] = score

        fused_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

        logger.info(f"Returning top {FINAL_TOP_K} reranked results")

        return {
            "query": query,
            "results": fused_candidates[:FINAL_TOP_K]
        }

    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
