import os
import time
import requests
import psycopg2
from fastapi import FastAPI, HTTPException, Request
from datetime import datetime
from requests.auth import HTTPBasicAuth
from pinecone import Pinecone
from common.logging import setup_logging

DATABASE_URL = os.environ["NEON_DB_URL"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

pc = Pinecone(
    api_key=PINECONE_API_KEY,
    timeout=10.0,
)
pc_index = pc.Index("kb-pages")

app = FastAPI()
logger = setup_logging("page-indexer")

RETRIES = 3
RETRY_SLEEP = 1.0

def db():
    return psycopg2.connect(DATABASE_URL)

def init_state(page_id):
    logger.info("state_init_start", page_id=page_id)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_page_ingestion_state
            (page_id, confluence_status, neon_status, pinecone_status)
            VALUES (%s, 'pending', 'pending', 'pending')
            ON CONFLICT (page_id) DO NOTHING
            """,
            (page_id,),
        )
    logger.info("state_init_success", page_id=page_id)

def update_state(page_id, field, value, error=None):
    logger.info("state_update_start", page_id=page_id, field=field, value=value)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE kb_page_ingestion_state
            SET {field} = %s,
                last_error = COALESCE(%s, last_error),
                updated_at = now()
            WHERE page_id = %s
            """,
            (value, error, page_id),
        )
    logger.info("state_update_success", page_id=page_id, field=field, value=value)

def retry(op, stage, page_id):
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            result = op()
            logger.info("retry_success", page_id=page_id, stage=stage, attempt=attempt)
            return result
        except Exception as e:
            last_err = str(e)
            logger.warning(
                "retry_attempt_failed",
                page_id=page_id,
                stage=stage,
                attempt=attempt,
                error=last_err,
            )
            time.sleep(RETRY_SLEEP)
    logger.error("retry_exhausted", page_id=page_id, stage=stage, error=last_err)
    raise RuntimeError(last_err)

def fetch_confluence_page(page_id):
    logger.info("confluence_fetch_start", page_id=page_id)

    def op():
        r = requests.get(
            f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}?expand=history",
            auth=AUTH,
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            raise RuntimeError("confluence_fetch_failed")
        data = r.json()
        return (
            data["title"],
            datetime.fromisoformat(data["history"]["createdDate"].replace("Z", "+00:00")),
        )

    result = retry(op, "confluence", page_id)
    logger.info("confluence_fetch_success", page_id=page_id)
    return result

def build_source_url(page_id):
    return f"{CONFLUENCE_BASE_URL}/pages/{page_id}"

def insert_neon(page_id, title, source_url, created_at):
    logger.info("neon_insert_start", page_id=page_id)

    def op():
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_pages
                (page_id, page_title, source_url, created_at, is_stashed)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (page_id) DO NOTHING
                """,
                (page_id, title, source_url, created_at, True),
            )

    retry(op, "neon", page_id)
    logger.info("neon_insert_success", page_id=page_id)

def upsert_pinecone(page_id, title):
    logger.info("pinecone_upsert_start", page_id=page_id)

    def op():
        pc_index.upsert_records(
            namespace="default",
            records=[{"_id": f"page:{page_id}", "page_title": title}],
        )

    retry(op, "pinecone", page_id)
    logger.info("pinecone_upsert_success", page_id=page_id)

@app.post("/")
async def page_created(req: Request):
    body = await req.json()
    page_id = body["page_id"]

    logger.info("page_event_received", page_id=page_id)
    init_state(page_id)

    stage = None

    try:
        stage = "confluence"
        title, created_at = fetch_confluence_page(page_id)
        update_state(page_id, "confluence_status", "success")

        stage = "neon"
        insert_neon(page_id, title, build_source_url(page_id), created_at)
        update_state(page_id, "neon_status", "success")

        stage = "pinecone"
        upsert_pinecone(page_id, title)
        update_state(page_id, "pinecone_status", "success")

        logger.info("page_index_success", page_id=page_id)
        return {"page_id": page_id}

    except Exception as e:
        err = str(e)
        if stage:
            update_state(page_id, f"{stage}_status", "failed", err)
        logger.error("page_index_failed", page_id=page_id, stage=stage, error=err)
        raise HTTPException(status_code=500)

@app.post("/retry/confluence")
def retry_confluence(req: dict):
    page_id = req["page_id"]
    logger.info("retry_endpoint_called", page_id=page_id, stage="confluence")
    try:
        fetch_confluence_page(page_id)
        update_state(page_id, "confluence_status", "success")
        logger.info("retry_endpoint_success", page_id=page_id, stage="confluence")
        return {"page_id": page_id, "stage": "confluence"}
    except Exception as e:
        update_state(page_id, "confluence_status", "failed", str(e))
        logger.error("retry_endpoint_failed", page_id=page_id, stage="confluence", error=str(e))
        raise HTTPException(status_code=500)

@app.post("/retry/neon")
def retry_neon(req: dict):
    page_id = req["page_id"]
    logger.info("retry_endpoint_called", page_id=page_id, stage="neon")
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT page_title, source_url, created_at FROM kb_pages WHERE page_id = %s",
            (page_id,),
        )
        row = cur.fetchone()
    if not row:
        logger.error("retry_neon_missing_page", page_id=page_id)
        raise HTTPException(status_code=404)
    title, source_url, created_at = row
    try:
        insert_neon(page_id, title, source_url, created_at)
        update_state(page_id, "neon_status", "success")
        logger.info("retry_endpoint_success", page_id=page_id, stage="neon")
        return {"page_id": page_id, "stage": "neon"}
    except Exception as e:
        update_state(page_id, "neon_status", "failed", str(e))
        logger.error("retry_endpoint_failed", page_id=page_id, stage="neon", error=str(e))
        raise HTTPException(status_code=500)

@app.post("/retry/pinecone")
def retry_pinecone(req: dict):
    page_id = req["page_id"]
    logger.info("retry_endpoint_called", page_id=page_id, stage="pinecone")
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT page_title FROM kb_pages WHERE page_id = %s",
            (page_id,),
        )
        row = cur.fetchone()
    if not row:
        logger.error("retry_pinecone_missing_page", page_id=page_id)
        raise HTTPException(status_code=404)
    title = row[0]
    try:
        upsert_pinecone(page_id, title)
        update_state(page_id, "pinecone_status", "success")
        logger.info("retry_endpoint_success", page_id=page_id, stage="pinecone")
        return {"page_id": page_id, "stage": "pinecone"}
    except Exception as e:
        update_state(page_id, "pinecone_status", "failed", str(e))
        logger.error("retry_endpoint_failed", page_id=page_id, stage="pinecone", error=str(e))
        raise HTTPException(status_code=500)

@app.get("/health")
def health():
    logger.info("health_check")
    return {"status": "ok"} 
