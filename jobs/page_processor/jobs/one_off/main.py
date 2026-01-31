import os
import time
import uuid
import hashlib
import psycopg2
import requests
import logging
from datetime import datetime, timezone
from requests.auth import HTTPBasicAuth
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from llama_index.core.embeddings import BaseEmbedding
from pinecone import Pinecone
from common.analytics import (
    record_stage_execution,
    record_processing_result,
    init_analytics_schema,
)
from jobs.page_processor.helpers.embedder.hf_embedder import embed
from jobs.page_processor.helpers.extractors.image_extractor import extract_images
from jobs.page_processor.helpers.extractors.text_processor import extract_tables, html_to_markdown
from jobs.common.confluence_pages import fetch_page_ids

init_analytics_schema()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("page-processor")

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]
DATABASE_URL = os.environ["NEON_DB_URL"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

PINECONE_BATCH_SIZE = 90
PINECONE_IMAGE_BATCH_SIZE = 90
HF_EMBED_BATCH_SIZE = 32
RETRIES = 3
RETRY_SLEEP = 1.0

pc = Pinecone(api_key=PINECONE_API_KEY)

class HFEmbedding(BaseEmbedding):
    def _get_text_embedding(self, text):
        return self._get_text_embeddings([text])[0]

    def _get_text_embeddings(self, texts):
        out = []
        for i in range(0, len(texts), HF_EMBED_BATCH_SIZE):
            out.extend(embed(texts[i : i + HF_EMBED_BATCH_SIZE]))
        return out

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_text_embedding(query)

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def record_stage(trace_id, stage, start, status):
    record_stage_execution(
        trace_id=trace_id,
        pipeline="processing",
        stage_name=stage,
        status=status,
        latency_ms=int((time.time() - start) * 1000),
    )

def fetch_confluence_page(page_id, trace_id):
    start = time.time()
    try:
        r = requests.get(
            f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}",
            headers=HEADERS,
            params={"expand": "body.storage"},
            auth=AUTH,
            timeout=15,
        )
        r.raise_for_status()
        record_stage(trace_id, "confluence", start, "success")
        return r.json()["body"]["storage"]["value"]
    except Exception:
        record_stage(trace_id, "confluence", start, "failed")
        raise

def flatten_tables(tables):
    out = []
    for table in tables:
        for fact in table:
            if fact and fact.strip():
                out.append(fact.strip())
    return out

def upsert_neon_images(page_id, images, trace_id):
    start = time.time()
    try:
        now = datetime.now(timezone.utc)
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                for img in images:
                    cur.execute(
                        """
                        INSERT INTO kb_images
                        (image_hash, page_id, image_src, caption, is_active, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (image_hash) DO NOTHING
                        """,
                        (
                            sha256(img["src"] + img["caption"]),
                            page_id,
                            img["src"],
                            img["caption"],
                            True,
                            now,
                        ),
                    )
        record_stage(trace_id, "neon_images", start, "success")
    except Exception:
        record_stage(trace_id, "neon_images", start, "failed")
        raise

def upsert_neon_chunks(page_id, chunks, section_paths, trace_id):
    start = time.time()
    try:
        now = datetime.now(timezone.utc)
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                for text, section in zip(chunks, section_paths):
                    cur.execute(
                        """
                        INSERT INTO kb_chunks
                        (chunk_hash, raw_chunk, is_active, created_at, section_path, page_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_hash) DO NOTHING
                        """,
                        (
                            sha256(text),
                            text,
                            True,
                            now,
                            section,
                            page_id,
                        ),
                    )
        record_stage(trace_id, "neon_chunks", start, "success")
    except Exception:
        record_stage(trace_id, "neon_chunks", start, "failed")
        raise

def upsert_pinecone_chunks(chunks, trace_id):
    start = time.time()
    try:
        if chunks:
            index = pc.Index("kb-chunks")
            for i in range(0, len(chunks), PINECONE_BATCH_SIZE):
                index.upsert_records(
                    namespace="default",
                    records=[
                        {"_id": sha256(text), "raw_chunk": text}
                        for text in chunks[i : i + PINECONE_BATCH_SIZE]
                    ],
                )
        record_stage(trace_id, "pinecone_chunks", start, "success")
    except Exception:
        record_stage(trace_id, "pinecone_chunks", start, "failed")
        raise

def upsert_pinecone_images(images, trace_id):
    start = time.time()
    try:
        if images:
            index = pc.Index("kb-images")
            for i in range(0, len(images), PINECONE_IMAGE_BATCH_SIZE):
                index.upsert_records(
                    namespace="default",
                    records=[
                        {
                            "_id": sha256(img["src"] + img["caption"]),
                            "caption": img["caption"],
                        }
                        for img in images[i : i + PINECONE_IMAGE_BATCH_SIZE]
                    ],
                )
        record_stage(trace_id, "pinecone_images", start, "success")
    except Exception:
        record_stage(trace_id, "pinecone_images", start, "failed")
        raise

def process_page(page_id):
    trace_id = str(uuid.uuid4())
    start = time.time()
    status = "failed"

    try:
        html = fetch_confluence_page(page_id, trace_id)

        s = time.time()
        images = extract_images(html)
        tables = extract_tables(html)
        table_chunks = flatten_tables(tables)
        record_stage(trace_id, "extract", s, "success")

        s = time.time()
        markdown = html_to_markdown(html)
        doc = Document(text=markdown)
        structural = SentenceSplitter(
            chunk_size=300,
            chunk_overlap=50,
            paragraph_separator="\n\n",
        ).get_nodes_from_documents([doc])

        embedder = HFEmbedding()
        semantic = SemanticSplitterNodeParser(
            embed_model=embedder,
            buffer_size=1,
            breakpoint_percentile_threshold=90,
        )

        semantic_nodes = []
        for node in structural:
            semantic_nodes.extend(
                semantic.get_nodes_from_documents([Document(text=node.text)])
            )

        text_chunks = [n.text for n in semantic_nodes if len(n.text.strip()) > 30]
        section_paths = [None] * len(text_chunks)
        record_stage(trace_id, "chunking", s, "success")

        upsert_neon_images(page_id, images, trace_id)
        upsert_neon_chunks(page_id, text_chunks, section_paths, trace_id)
        upsert_neon_chunks(page_id, table_chunks, [None] * len(table_chunks), trace_id)

        upsert_pinecone_chunks(text_chunks + table_chunks, trace_id)
        upsert_pinecone_images(images, trace_id)

        lengths = [len(c) for c in text_chunks]
        avg_len = sum(lengths) // len(lengths) if lengths else 0

        record_processing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="success",
            text_chunk_count=len(text_chunks),
            table_chunk_count=len(table_chunks),
            image_count=len(images),
            avg_chunk_length=avg_len,
            min_chunk_length=min(lengths) if lengths else 0,
            max_chunk_length=max(lengths) if lengths else 0,
            total_embeddings=len(text_chunks) + len(table_chunks),
            total_latency_ms=int((time.time() - start) * 1000),
        )

        status = "success"
        return status

    except Exception:
        record_processing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="failed",
            text_chunk_count=0,
            table_chunk_count=0,
            image_count=0,
            avg_chunk_length=0,
            min_chunk_length=0,
            max_chunk_length=0,
            total_embeddings=0,
            total_latency_ms=int((time.time() - start) * 1000),
        )
        raise
    finally:
        logger.info(f"page_id={page_id} status={status}")

def main():
    page_ids = fetch_page_ids()
    failures = 0

    for page_id in page_ids:
        last_err = None
        for _ in range(RETRIES):
            try:
                process_page(page_id)
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
                time.sleep(RETRY_SLEEP)

        if last_err:
            failures += 1

    logger.info(f"completed pages={len(page_ids)} failures={failures}")

    if failures:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
