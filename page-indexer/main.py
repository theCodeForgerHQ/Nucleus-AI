import os
import requests
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from datetime import datetime
from requests.auth import HTTPBasicAuth
import time
from pymilvus import Collection, connections

connections.connect(
    alias="default",
    uri="https://in03-daffa9519d80931.serverless.aws-eu-central-1.cloud.zilliz.com",
    token=os.environ["ZILLIZ_API_KEY"]
)
collection = Collection("kb_pages")
collection.load()

DATABASE_URL = os.environ["NEON_DB_URL"]
CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

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
    url = f"{CONFLUENCE_BASE_URL}/pages/{page_id}"
    return url

def upsert_neon(page_id, title, source_url, created_at):
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_pages (page_id, page_title, source_url, created_at, is_stashed)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (page_id) DO NOTHING
                """,
                (page_id, title, source_url, created_at, True)
            )

import time

def upsert_milvus_with_retry(page_id, title, retries=3, delay=2):
    attempt = 0
    while attempt < retries:
        try:
            r = requests.post(
                "https://patient-husky-uniquely.ngrok-free.app/v1/embeddings",
                json={"text": title},
                headers={"Content-Type": "application/json"}
            )
            resp_json = r.json()

            if "data" not in resp_json or not resp_json["data"]:
                raise RuntimeError(f"embedding data missing: {resp_json}")

            embedding = resp_json["data"][0]["embedding"]
            if len(embedding) != 2048:
                raise RuntimeError("invalid_embedding_dim")

            collection.insert({
                "page_id": page_id,
                "vector": embedding
            })
            collection.flush()
            return

        except Exception as e:
            attempt += 1
            if attempt >= retries:
                raise RuntimeError(f"Milvus upsert failed after {retries} attempts: {e}")
            time.sleep(delay)

@app.post("/")
async def page_created(req: Request):
    try:
        body = await req.json()
        page_id = body["page_id"]

        title, created_at = fetch_confluence_page(page_id)
        source_url = build_source_url(page_id)

        upsert_neon(page_id, title, source_url, created_at)
        upsert_milvus_with_retry(page_id, title)

        return {"page_id": page_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
