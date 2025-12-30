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
    """
    Converts Confluence storage HTML to Markdown
    while preserving heading structure.
    """
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

def build_chunk_id(page_id: str, header_path: str) -> str:
    base = f"{page_id}:{header_path}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def chunk_page_structurally(page_id: str) -> List[Dict]:
    page = get_confluence_page_content(page_id)
    html_content = page["body"]["storage"]["value"]

    markdown_text = confluence_html_to_markdown(html_content)

    doc = Document(
        text=markdown_text,
        metadata={"page_id": page_id}
    )

    nodes = _NODE_PARSER.get_nodes_from_documents([doc])

    chunks = []

    for node in nodes:
        header_path = node.metadata.get("header_path", "/")
        chunk_text = node.get_content()
        chunk_id = build_chunk_id(page_id, header_path)

        chunks.append({
            "page_id": page_id,
            "chunk_id": chunk_id,
            "header_path": header_path,
            "chunk_text": chunk_text
        })

    return chunks

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def compute_merkle_root(chunk_hashes: List[str]) -> str:
    if not chunk_hashes:
        return None

    current = chunk_hashes[:]

    while len(current) > 1:
        next_level = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else left
            next_level.append(hash_text(left + right))
        current = next_level

    return current[0]

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

def build_page_merkle_snapshot(versioned_chunks: List[Dict]) -> Dict:
    ordered = sorted(versioned_chunks, key=lambda x: x["chunk_id"])
    hashes = [c["chunk_hash"] for c in ordered]

    return {
        "merkle_root": compute_merkle_root(hashes),
        "chunk_count": len(ordered),
        "chunks": ordered
    }

def process_page(page_id: str) -> Dict:
    chunks = chunk_page_structurally(page_id)
    versioned_chunks = build_chunk_versions(chunks)
    snapshot = build_page_merkle_snapshot(versioned_chunks)

    return {
        "page_id": page_id,
        "merkle_root": snapshot["merkle_root"],
        "chunk_count": snapshot["chunk_count"],
        "chunks": snapshot["chunks"]
    }

if __name__ == "__main__":
    PAGE_ID = "PAGE_ID" 

    result = process_page(PAGE_ID)

    print("\n=== PAGE MERKLE SNAPSHOT ===")
    print(f"Page ID     : {result['page_id']}")
    print(f"Merkle Root : {result['merkle_root']}")
    print(f"Chunks      : {result['chunk_count']}\n")

    for c in result["chunks"]:
        print("-----")
        print("Header Path :", c["header_path"])
        print("Chunk Hash  :", c["chunk_hash"])
        print("Text (first 200 chars):")
        print(c["chunk_text"][:200])
