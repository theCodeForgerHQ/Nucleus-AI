import os
import hashlib
import requests
import psycopg2
from datetime import datetime, timezone
from requests.auth import HTTPBasicAuth
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from llama_index.core.embeddings import BaseEmbedding
from pinecone import Pinecone
from common.logging import setup_logging
from image_extractor import extract_images
from text_processor import extract_tables, html_to_markdown

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]
HF_EMBEDDER_URL = os.environ["HF_EMBEDDER_URL"]

PINECONE_BATCH_SIZE = 90
PINECONE_IMAGE_BATCH_SIZE = 90

DATABASE_URL = os.environ["NEON_DB_URL"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

pc = Pinecone(api_key=PINECONE_API_KEY)

app = FastAPI()
logger = setup_logging("page-processor")

HF_EMBED_BATCH_SIZE = 32

class HFHTTPEmbedding(BaseEmbedding):
    url: str = Field()

    def _get_text_embedding(self, text):
        r = requests.post(self.url, json={"texts": [text]}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"][0]

    def _get_text_embeddings(self, texts):
        all_embeddings = []

        for i in range(0, len(texts), HF_EMBED_BATCH_SIZE):
            batch = texts[i : i + HF_EMBED_BATCH_SIZE]

            r = requests.post(
                self.url,
                json={"texts": batch},
                timeout=60,
            )
            r.raise_for_status()
            all_embeddings.extend(r.json()["embeddings"])

        return all_embeddings

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_text_embedding(query)

class PageRequest(BaseModel):
    page_id: str

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def fetch_confluence_page(page_id):
    logger.info("confluence_fetch_start", page_id=page_id)
    r = requests.get(
        f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}",
        headers=HEADERS,
        params={"expand": "body.storage"},
        auth=AUTH,
        timeout=15,
    )
    r.raise_for_status()
    logger.info("confluence_fetch_success", page_id=page_id)
    return r.json()["body"]["storage"]["value"]

def flatten_tables(tables):
    out = []
    for table in tables:
        for fact in table:
            if fact and fact.strip():
                out.append(fact.strip())
    return out

def upsert_neon_images(page_id, images):
    logger.info("neon_images_upsert_start", page_id=page_id, count=len(images))
    now = datetime.now(timezone.utc)
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for img in images:
                h = sha256(img["src"] + img["caption"])
                cur.execute(
                    """
                    INSERT INTO kb_images
                    (image_hash, page_id, image_src, caption, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (image_hash) DO NOTHING
                    """,
                    (h, page_id, img["src"], img["caption"], True, now),
                )
    logger.info("neon_images_upsert_success", page_id=page_id)

def upsert_neon_chunks(page_id, chunks, section_paths):
    logger.info("neon_chunks_upsert_start", page_id=page_id, count=len(chunks))
    now = datetime.now(timezone.utc)
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for text, section in zip(chunks, section_paths):
                h = sha256(text)
                cur.execute(
                    """
                    INSERT INTO kb_chunks
                    (chunk_hash, raw_chunk, is_active, created_at, section_path, page_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_hash) DO NOTHING
                    """,
                    (h, text, True, now, section, page_id),
                )
    logger.info("neon_chunks_upsert_success", page_id=page_id)

def upsert_pinecone_chunks(chunks):
    total = len(chunks)
    logger.info("pinecone_chunks_upsert_start", count=total)

    if not chunks:
        return

    index = pc.Index("kb-chunks")

    for i in range(0, total, PINECONE_BATCH_SIZE):
        batch = chunks[i : i + PINECONE_BATCH_SIZE]
        records = [
            {"_id": sha256(text), "raw_chunk": text}
            for text in batch
        ]
        index.upsert_records(namespace="default", records=records)

        logger.info(
            "pinecone_chunks_upsert_batch_success",
            batch_start=i,
            batch_size=len(records),
        )

    logger.info("pinecone_chunks_upsert_success", count=total)

def upsert_pinecone_images(images):
    total = len(images)
    logger.info("pinecone_images_upsert_start", count=total)

    if not images:
        return

    index = pc.Index("kb-images")

    for i in range(0, total, PINECONE_IMAGE_BATCH_SIZE):
        batch = images[i : i + PINECONE_IMAGE_BATCH_SIZE]
        records = [
            {
                "_id": sha256(img["src"] + img["caption"]),
                "caption": img["caption"],
            }
            for img in batch
        ]

        index.upsert_records(namespace="default", records=records)

        logger.info(
            "pinecone_images_upsert_batch_success",
            batch_start=i,
            batch_size=len(records),
        )

    logger.info("pinecone_images_upsert_success", count=total)

def infer_section_paths(markdown, chunks):
    current = None
    for line in markdown.splitlines():
        if line.startswith("#"):
            current = line.lstrip("#").strip()
    return [current for _ in chunks]

def process_page_internal(page_id: str):
    logger.info("page_processing_start", page_id=page_id)

    html = fetch_confluence_page(page_id)

    images = extract_images(html)
    tables = extract_tables(html)
    table_chunks = flatten_tables(tables)
    table_section_paths = [None] * len(table_chunks)

    logger.info(
        "content_extraction_complete",
        page_id=page_id,
        images=len(images),
        table_facts=len(table_chunks),
    )

    markdown = html_to_markdown(html)
    doc = Document(text=markdown)

    structural = SentenceSplitter(
        chunk_size=300,
        chunk_overlap=50,
        paragraph_separator="\n\n",
    ).get_nodes_from_documents([doc])

    embedder = HFHTTPEmbedding(url=HF_EMBEDDER_URL)
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
    section_paths = infer_section_paths(markdown, text_chunks)

    logger.info(
        "chunking_complete",
        page_id=page_id,
        chunks=len(text_chunks),
    )

    upsert_neon_images(page_id, images)
    upsert_neon_chunks(page_id, text_chunks, section_paths)
    upsert_neon_chunks(page_id, table_chunks, table_section_paths)

    upsert_pinecone_chunks(text_chunks)
    upsert_pinecone_chunks(table_chunks)
    upsert_pinecone_images(images)

    logger.info("page_processing_success", page_id=page_id)

@app.post("/")
def process_page(req: PageRequest, background_tasks: BackgroundTasks):
    page_id = req.page_id
    logger.info("page_processing_accepted", page_id=page_id)
    background_tasks.add_task(process_page_internal, page_id)
    return {"page_id": page_id, "status": "accepted"}

@app.get("/health")
def health():
    logger.info("health_check")
    return {"status": "ok"}
