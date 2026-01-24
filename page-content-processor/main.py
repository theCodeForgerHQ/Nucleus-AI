import os
import hashlib
import requests
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from llama_index.core.embeddings import BaseEmbedding
from image_extractor import extract_images
from text_processor import extract_tables, html_to_markdown
from pymilvus import Collection, connections
import time

load_dotenv()

CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
HF_EMBEDDER_URL = os.getenv("HF_EMBEDDER_URL")

DATABASE_URL = os.getenv("NEON_DB_URL")

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

connections.connect(
    alias="default",
    uri="https://in03-daffa9519d80931.serverless.aws-eu-central-1.cloud.zilliz.com",
    token=os.environ["ZILLIZ_API_KEY"]
)

chunk_collection = Collection("kb_pages")
chunk_collection.load()

image_collection = Collection("kb_images")
image_collection.load()

app = FastAPI()

class HFHTTPEmbedding(BaseEmbedding):
    url: str = Field()

    def _get_text_embedding(self, text):
        r = requests.post(self.url, json={"texts": [text]}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"][0]

    def _get_text_embeddings(self, texts):
        r = requests.post(self.url, json={"texts": texts}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"]

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_text_embedding(query)


class PageRequest(BaseModel):
    page_id: str

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def fetch_confluence_page(page_id):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    r = requests.get(url, headers=HEADERS, params=params, auth=AUTH)
    r.raise_for_status()
    return r.json()["body"]["storage"]["value"]

def flatten_tables(tables):
    out = []
    for table in tables:
        for fact in table:
            if fact and fact.strip():
                out.append(fact.strip())
    return out

def upsert_neon_images(page_id, images):
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
                    (h, page_id, img["src"], img["caption"], True, now)
                )

def upsert_neon_chunks(page_id, chunks, section_paths):
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
                    (h, text, True, now, section, page_id)
                )

import time

def upsert_milvus_chunks_with_retry(chunks, retries=3, delay=2):
    if not chunks:
        return

    records = []
    for text in chunks:
        h = sha256(text)
        r = requests.post(
            "https://patient-husky-uniquely.ngrok-free.app/v1/embeddings",
            json={"text": text},
            headers={"Content-Type": "application/json"}
        )
        embedding = r.json()["data"][0]["embedding"]
        records.append({"chunk_hash": h, "vector": embedding})

    attempt = 0
    while attempt < retries:
        try:
            chunk_collection.insert(records)
            chunk_collection.flush()
            return
        except Exception as e:
            attempt += 1
            if attempt >= retries:
                raise RuntimeError(f"Milvus chunk upsert failed after {retries} attempts: {e}")
            time.sleep(delay)


def upsert_milvus_images_with_retry(images, retries=3, delay=2):
    if not images:
        return

    records = []
    for img in images:
        h = sha256(img["src"] + img["caption"])
        r = requests.post(
            "https://patient-husky-uniquely.ngrok-free.app/v1/embeddings",
            json={"text": img["caption"]},
            headers={"Content-Type": "application/json"}
        )
        embedding = r.json()["data"][0]["embedding"]
        records.append({"image_hash": h, "vector": embedding})

    attempt = 0
    while attempt < retries:
        try:
            image_collection.insert(records)
            image_collection.flush()
            return
        except Exception as e:
            attempt += 1
            if attempt >= retries:
                raise RuntimeError(f"Milvus image upsert failed after {retries} attempts: {e}")
            time.sleep(delay)

def infer_section_paths(markdown, chunks):
    lines = markdown.splitlines()
    current = None
    headings = []
    for line in lines:
        if line.startswith("#"):
            current = line.lstrip("#").strip()
        headings.append(current)
    return [current for _ in chunks]

@app.post("/")
def process_page(req: PageRequest):
    try:
        page_id = req.page_id
        html = fetch_confluence_page(page_id)

        images = extract_images(html)
        tables = extract_tables(html)
        table_chunks = flatten_tables(tables)
        table_section_paths = [None] * len(table_chunks)

        markdown = html_to_markdown(html)
        doc = Document(text=markdown)

        structural = SentenceSplitter(
            chunk_size=300,
            chunk_overlap=50
        ).get_nodes_from_documents([doc])

        embedder = HFHTTPEmbedding(url=HF_EMBEDDER_URL)
        semantic = SemanticSplitterNodeParser(
            embed_model=embedder,
            buffer_size=1,
            breakpoint_percentile_threshold=90
        )

        semantic_nodes = []
        for node in structural:
            semantic_nodes.extend(
                semantic.get_nodes_from_documents(
                    [Document(text=node.text)]
                )
            )

        text_chunks = [n.text for n in semantic_nodes if len(n.text.strip()) > 30]
        section_paths = infer_section_paths(markdown, text_chunks)

        upsert_neon_images(page_id, images)
        upsert_neon_chunks(page_id, text_chunks, section_paths)
        upsert_neon_chunks(page_id, table_chunks, table_section_paths)

        upsert_milvus_chunks_with_retry(text_chunks)
        upsert_milvus_chunks_with_retry(table_chunks)
        upsert_milvus_images_with_retry(images)

        return {
            "page_id": page_id,
            "images": len(images),
            "chunks": len(text_chunks),
            "table_facts": len(table_chunks)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
