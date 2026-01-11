import os
import requests
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from datetime import datetime, timezone
from requests.auth import HTTPBasicAuth
from pinecone import Pinecone

DATABASE_URL = os.environ["NEON_DB_URL"]

PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
PINECONE_INDEX = os.environ["PAGE_PINECONE_INDEX"]

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

pc = Pinecone(api_key=PINECONE_API_KEY)
pc_index = pc.Index(PINECONE_INDEX)

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

app = FastAPI()

def fetch_confluence_page(page_id):
    api_url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}?expand=history"
    r = requests.get(api_url, auth=AUTH, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        raise RuntimeError("fetch_failed")
    data = r.json()
    title = data["title"]
    created_at = datetime.fromisoformat(
        data["history"]["createdDate"].replace("Z", "+00:00")
    )
    return title, created_at

def build_source_url(page_id):
    return f"{CONFLUENCE_BASE_URL}/pages/{page_id}"

def upsert_neon(page_id, source_url, created_at):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pages (page_id, source_url, created_at, is_stashed)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (page_id) DO NOTHING
            """,
            (page_id, source_url, created_at, False)
        )

def upsert_pinecone(page_id, title):
    pc_index.upsert(
        records=[
            {
                "id": f"page:{page_id}",
                "text": title
            }
        ]
    )

@app.post("/")
async def page_created(req: Request):
    try:
        body = await req.json()
        page_id = body["page_id"]
        title, created_at = fetch_confluence_page(page_id)
        source_url = build_source_url(page_id)
        upsert_neon(page_id, source_url, created_at)
        upsert_pinecone(page_id, title)
        return {"page_id": page_id}
    except Exception:
        raise HTTPException(status_code=500)
