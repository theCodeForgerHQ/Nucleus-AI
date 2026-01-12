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
from pinecone import Pinecone
from image_extractor import extract_images
from text_processor import extract_tables, html_to_markdown

load_dotenv()

CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
HF_EMBEDDER_URL = os.getenv("HF_EMBEDDER_URL", "http://localhost:8000/")

DATABASE_URL = os.getenv("NEON_DB_URL")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

pc = Pinecone(api_key=PINECONE_API_KEY)
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

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

def upsert_pinecone_chunks(chunks):
    records = []
    for text in chunks:
        h = sha256(text)
        records.append({"_id": h, "text": text})
    if records:
        pc.Index("kb-chunks").upsert_records(
            namespace="default",
            records=records
        )

def upsert_pinecone_images(images):
    records = []
    for img in images:
        h = sha256(img["src"] + img["caption"])
        records.append({"_id": h, "text": img["caption"]})
    if records:
        pc.Index("kb-images").upsert_records(
            namespace="default",
            records=records
        )

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

        upsert_pinecone_chunks(text_chunks)
        upsert_pinecone_chunks(table_chunks)
        upsert_pinecone_images(images)

        return {
            "page_id": page_id,
            "images": len(images),
            "chunks": len(text_chunks),
            "table_facts": len(table_chunks)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
