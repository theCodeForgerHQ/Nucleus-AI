import os
import time
import uuid
import requests
import psycopg2
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from datetime import datetime
from requests.auth import HTTPBasicAuth
from pinecone import Pinecone
from common.analytics import (
    record_stage_execution,
    record_indexing_result,
    init_analytics_schema
)

init_analytics_schema()

DATABASE_URL = os.environ["NEON_DB_URL"]
PINECONE_API_KEY = os.environ["PINECONE_API_KEY"]
CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

pc = Pinecone(api_key=PINECONE_API_KEY, timeout=10.0)
pc_index = pc.Index("kb-pages")

app = FastAPI()

RETRIES = 3
RETRY_SLEEP = 1.0

def db():
    return psycopg2.connect(DATABASE_URL)

def init_state(page_id):
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

def update_state(page_id, field, value, error=None):
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

def retry(op):
    last_err = None
    for _ in range(RETRIES):
        try:
            return op()
        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_SLEEP)
    raise RuntimeError(last_err)

def fetch_confluence_page(page_id, trace_id):
    start = time.time()

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

    result = retry(op)

    record_stage_execution(
        trace_id=trace_id,
        pipeline="indexing",
        stage_name="confluence",
        status="success",
        latency_ms=int((time.time() - start) * 1000),
    )

    return result

def build_source_url(page_id):
    return f"{CONFLUENCE_BASE_URL}/pages/{page_id}"

def insert_neon(page_id, title, source_url, created_at, trace_id):
    start = time.time()

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

    retry(op)

    record_stage_execution(
        trace_id=trace_id,
        pipeline="indexing",
        stage_name="neon",
        status="success",
        latency_ms=int((time.time() - start) * 1000),
    )

def upsert_pinecone(page_id, title, trace_id):
    start = time.time()

    def op():
        pc_index.upsert_records(
            namespace="default",
            records=[{"_id": f"page:{page_id}", "page_title": title}],
        )

    retry(op)

    record_stage_execution(
        trace_id=trace_id,
        pipeline="indexing",
        stage_name="pinecone",
        status="success",
        latency_ms=int((time.time() - start) * 1000),
    )

def process_page(page_id: str):
    trace_id = str(uuid.uuid4())
    start = time.time()
    stage = None
    stage_start = None
    init_state(page_id)

    try:
        stage = "confluence"
        stage_start = time.time()
        title, created_at = fetch_confluence_page(page_id, trace_id)
        update_state(page_id, "confluence_status", "success")

        stage = "neon"
        stage_start = time.time()
        insert_neon(page_id, title, build_source_url(page_id), created_at, trace_id)
        update_state(page_id, "neon_status", "success")

        stage = "pinecone"
        stage_start = time.time()
        upsert_pinecone(page_id, title, trace_id)
        update_state(page_id, "pinecone_status", "success")

        record_indexing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="success",
            total_latency_ms=int((time.time() - start) * 1000),
        )

    except Exception as e:
        err = str(e)
        if stage:
            update_state(page_id, f"{stage}_status", "failed", err)
            record_stage_execution(
                trace_id=trace_id,
                pipeline="indexing",
                stage_name=stage,
                status="failed",
                latency_ms=int((time.time() - stage_start) * 1000),
            )

        record_indexing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="failed",
            total_latency_ms=int((time.time() - start) * 1000),
        )

@app.post("/")
async def page_created(req: Request, bg: BackgroundTasks):
    body = await req.json()
    page_id = body["page_id"]
    bg.add_task(process_page, page_id)
    return {"accepted": True, "page_id": page_id}

@app.post("/retry/confluence")
def retry_confluence(req: dict):
    page_id = req["page_id"]
    trace_id = str(uuid.uuid4())
    try:
        title, created_at = fetch_confluence_page(page_id, trace_id)
        update_state(page_id, "confluence_status", "success")
        return {
            "page_id": page_id,
            "title": title,
            "created_at": created_at.isoformat(),
        }
    except Exception as e:
        update_state(page_id, "confluence_status", "failed", str(e))
        raise HTTPException(status_code=500)

@app.post("/retry/neon")
def retry_neon(req: dict):
    page_id = req["page_id"]
    title = req["title"]
    created_at = datetime.fromisoformat(req["created_at"])
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        insert_neon(page_id, title, build_source_url(page_id), created_at, trace_id)
        update_state(page_id, "neon_status", "success")
        return {"page_id": page_id}
    except Exception as e:
        update_state(page_id, "neon_status", "failed", str(e))
        record_stage_execution(
            trace_id=trace_id,
            pipeline="indexing",
            stage_name="neon",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise HTTPException(status_code=500)

@app.post("/retry/pinecone")
def retry_pinecone(req: dict):
    page_id = req["page_id"]
    title = req["title"]
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        upsert_pinecone(page_id, title, trace_id)
        update_state(page_id, "pinecone_status", "success")
        return {"page_id": page_id}
    except Exception as e:
        update_state(page_id, "pinecone_status", "failed", str(e))
        record_stage_execution(
            trace_id=trace_id,
            pipeline="indexing",
            stage_name="pinecone",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise HTTPException(status_code=500)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/webhooks/page-created")
async def page_created(request: Request):
    payload = await request.json()
    print("Page Created Payload:", payload)
    return {"status": "ok"}

@app.post("/webhooks/page-updated")
async def page_updated(request: Request):
    payload = await request.json()
    print("Page Updated Payload:", payload)
    return {"status": "ok"}

@app.post("/webhooks/page-deleted")
async def page_deleted(request: Request):
    payload = await request.json()
    print("Page Deleted Payload:", payload)
    return {"status": "ok"}

@app.post("/webhooks/page-restored")
async def page_restored(request: Request):
    payload = await request.json()
    print("Page Restored Payload:", payload)
    return {"status": "ok"}

def page_updated(page_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE kb_pages
            SET is_stashed = TRUE,
                updated_at = now()
            WHERE page_id = %s
            """,
            (page_id,),
        )
    return {"accepted": True, "page_id": page_id}

def page_removed(page_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE kb_pages
            SET is_active = FALSE,
                updated_at = now()
            WHERE page_id = %s
            """,
            (page_id,),
        )
    return {"accepted": True, "page_id": page_id}


