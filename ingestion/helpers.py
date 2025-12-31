import os
import re
import html
import hashlib
import requests
from typing import List, Dict
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

def get_confluence_page_content(page_id: str) -> dict:
    url = f"{BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    resp = requests.get(url, headers=HEADERS, params=params, auth=AUTH)
    resp.raise_for_status()
    return resp.json()

def confluence_html_to_markdown(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    markdown = md(str(soup), heading_style="ATX")
    markdown = html.unescape(markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()

_NODE_PARSER = MarkdownNodeParser(
    include_metadata=True,
    include_prev_next_rel=False
)

def build_chunk_id(page_id: str, header_path: str, chunk_text: str) -> str:
    base = f"{page_id}:{header_path}:{hash_text(chunk_text)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def chunk_page_structurally(page_id: str) -> List[Dict]:
    page = get_confluence_page_content(page_id)
    html_content = page["body"]["storage"]["value"]
    markdown_text = confluence_html_to_markdown(html_content)
    doc = Document(text=markdown_text, metadata={"page_id": page_id})
    nodes = _NODE_PARSER.get_nodes_from_documents([doc])
    chunks = []
    for node in nodes:
        header_path = node.metadata.get("header_path", "/")
        chunk_text = node.get_content()
        chunk_id = build_chunk_id(page_id, header_path, chunk_text)
        chunks.append({
            "page_id": page_id,
            "chunk_id": chunk_id,
            "header_path": header_path,
            "chunk_text": chunk_text
        })
    return chunks

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def build_chunk_versions(chunks: List[Dict]) -> List[Dict]:
    return [
        {
            "page_id": c["page_id"],
            "chunk_id": c["chunk_id"],
            "chunk_hash": hash_text(c["chunk_text"]),
            "header_path": c["header_path"],
            "chunk_text": c["chunk_text"]
        }
        for c in chunks
    ]

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("NEON_HOST"),
        database=os.getenv("NEON_DB"),
        user=os.getenv("NEON_USER"),
        password=os.getenv("NEON_PASSWORD"),
        port=os.getenv("NEON_PORT", 5432)
    )

def upsert_chunk_versions(page_id: str, chunks: List[Dict]):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        chunk_hash = chunk["chunk_hash"]
        chunk_text = chunk["chunk_text"]
        cur.execute(
            """
            INSERT INTO chunks (chunk_hash, chunk_text)
            VALUES (%s, %s)
            ON CONFLICT (chunk_hash) DO NOTHING
            """,
            (chunk_hash, chunk_text)
        )
        cur.execute(
            """
            SELECT * FROM page_chunk_versions
            WHERE page_id = %s
              AND chunk_id = %s
              AND is_active = true
            """,
            (page_id, chunk_id)
        )
        active = cur.fetchone()
        if not active:
            cur.execute(
                """
                INSERT INTO page_chunk_versions
                (page_id, chunk_id, chunk_hash, is_active)
                VALUES (%s, %s, %s, true)
                """,
                (page_id, chunk_id, chunk_hash)
            )
        elif active["chunk_hash"] != chunk_hash:
            cur.execute(
                """
                UPDATE page_chunk_versions
                SET is_active = false, valid_to = now()
                WHERE id = %s
                """,
                (active["id"],)
            )
            cur.execute(
                """
                INSERT INTO page_chunk_versions
                (page_id, chunk_id, chunk_hash, is_active)
                VALUES (%s, %s, %s, true)
                """,
                (page_id, chunk_id, chunk_hash)
            )
    conn.commit()
    cur.close()
    conn.close()

def process_page(page_id: str) -> Dict:
    chunks = chunk_page_structurally(page_id)
    versioned_chunks = build_chunk_versions(chunks)
    return {
        "page_id": page_id,
        "chunk_count": len(versioned_chunks),
        "chunks": versioned_chunks
    }

def process_and_store_page(page_id: str):
    result = process_page(page_id)
    upsert_chunk_versions(page_id, result["chunks"])
    return result

if __name__ == "__main__":
    PAGE_ID = "1605763"
    result = process_and_store_page(PAGE_ID)
    print("\n=== PAGE SNAPSHOT ===")
    print(f"Page ID : {result['page_id']}")
    print(f"Chunks  : {result['chunk_count']}\n")
    for c in result["chunks"]:
        print("-----")
        print("Header Path :", c["header_path"])
        print("Chunk Hash  :", c["chunk_hash"])
        print("Text (first 200 chars):")
        print(c["chunk_text"][:200])
