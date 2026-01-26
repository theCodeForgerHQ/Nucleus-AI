import os
import logging
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from pinecone import Pinecone, SearchQuery

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("app")

from dotenv import load_dotenv
load_dotenv()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
NEON_DB_URL = os.environ.get("NEON_DB_URL")

KB_CHUNKS_INDEX = "kb-chunks"
KB_PAGES_INDEX = "kb-pages"

TOP_K_CHUNKS = 50
TOP_K_PAGES = 20

W_CHUNK = 0.7
W_PAGE = 0.3

app = FastAPI()

logger.info("Initializing Pinecone client")
pc = Pinecone(api_key=PINECONE_API_KEY)

chunks_index = pc.Index(KB_CHUNKS_INDEX)
pages_index = pc.Index(KB_PAGES_INDEX)

class QueryRequest(BaseModel):
    query: str

def search_with_text(index, text: str, top_k: int):
    """
    Uses Pinecone integrated text search via index.search().
    """
    logger.debug(f"search_with_text: text={text}, top_k={top_k}")
    try:

        response = index.search(
            namespace="default",
            query={
                "inputs": {"text": text},
                "top_k": top_k,
            }
        )
        # Result structure: response["result"]["hits"] usually
        hits = response.get("result", {}).get("hits") or []
        logger.debug(f"search_with_text: found {len(hits)} hits")

        # Build dict of id -> score
        return {hit["_id"]: hit["_score"] for hit in hits}

    except Exception as e:
        logger.error(f"search_with_text error: {e}", exc_info=True)
        return {}

def fetch_chunks_from_neon(chunk_ids: List[str]) -> Dict[str, dict]:
    logger.debug(f"fetch_chunks_from_neon: chunk_ids={chunk_ids}")
    if not chunk_ids:
        logger.debug("fetch_chunks_from_neon: no chunk_ids provided")
        return {}

    placeholders = ",".join(["%s"] * len(chunk_ids))
    query = f"""
        SELECT chunk_hash, raw_chunk, section_path, page_id
        FROM kb_chunks
        WHERE chunk_hash IN ({placeholders})
    """
    try:
        with psycopg2.connect(NEON_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, chunk_ids)
                rows = cur.fetchall()
        logger.debug(f"fetch_chunks_from_neon: retrieved {len(rows)} rows from DB")
    except Exception as e:
        logger.error(f"fetch_chunks_from_neon DB error: {e}", exc_info=True)
        return {}

    result = {}
    for row in rows:
        result[row[0]] = {
            "text": row[1],
            "section": row[2],
            "page_id": row[3]
        }
    return result

@app.post("/query")
def run_query(req: QueryRequest):
    logger.info(f"run_query: received query='{req.query}'")

    try:

        chunk_scores = search_with_text(chunks_index, req.query, TOP_K_CHUNKS)
        logger.info(f"run_query: chunk_scores returned {len(chunk_scores)} items")
        if not chunk_scores:
            return {
                "query": req.query,
                "results": [],
                "debug": "No chunks found (chunk_scores empty)"
            }

        page_scores = search_with_text(pages_index, req.query, TOP_K_PAGES)
        logger.info(f"run_query: page_scores returned {len(page_scores)} items")

        chunk_ids = list(chunk_scores.keys())
        chunk_metadata = fetch_chunks_from_neon(chunk_ids)
        logger.info(f"run_query: chunk_metadata has {len(chunk_metadata)} entries")

        if not chunk_metadata:
            return {
                "query": req.query,
                "results": [],
                "debug": "No chunk metadata found in Neon"
            }

        fused_results = []
        for chunk_id, chunk_score in chunk_scores.items():
            if chunk_id not in chunk_metadata:
                logger.warning(f"run_query: chunk_id {chunk_id} not in metadata")
                continue

            meta = chunk_metadata[chunk_id]
            page_id = meta["page_id"]
            page_score = page_scores.get(str(page_id), 0.0)

            final_score = (W_CHUNK * chunk_score) + (W_PAGE * page_score)

            fused_results.append({
                "chunk_id": chunk_id,
                "page_id": page_id,
                "section": meta["section"],
                "text": meta["text"],
                "chunk_score": chunk_score,
                "page_score": page_score,
                "final_score": final_score
            })

        fused_results.sort(key=lambda x: x["final_score"], reverse=True)
        logger.info(f"run_query: returning {len(fused_results[:20])} results")

        return {
            "query": req.query,
            "results": fused_results[:20]
        }

    except Exception as e:
        logger.error(f"run_query error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
