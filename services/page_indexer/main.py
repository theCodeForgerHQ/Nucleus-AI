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

_pc = None

def get_env(key):
    try:
        return os.environ.get(key)
    except Exception:
        return None

def get_db_conn():
    try:
        url = get_env("NEON_DB_URL")
        if not url:
            return None
        return psycopg2.connect(url)
    except Exception:
        return None

def get_pc():
    global _pc
    if _pc:
        return _pc
    api_key = get_env("PINECONE_API_KEY")
    if not api_key:
        return None
    try:
        _pc = Pinecone(api_key=api_key)
        return _pc
    except Exception:
        return None

app = FastAPI()

def init_state(page_id):
    try:
        conn = get_db_conn()
        if not conn:
            return False
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_page_ingestion_state
                (page_id, confluence_status, neon_status, pinecone_status)
                VALUES (%s, 'pending', 'pending', 'pending')
                ON CONFLICT (page_id) DO NOTHING
                """,
                (page_id,),
            )
            return True
    except Exception:
        return False

def update_state(page_id, field, value):
    try:
        conn = get_db_conn()
        if not conn:
            return False
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE kb_page_ingestion_state
                SET {field} = %s,
                    updated_at = now()
                WHERE page_id = %s
                """,
                (value, page_id),
            )
            return True
    except Exception:
        return False

def safe_record_stage(trace_id, stage_name, status, start):
    try:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="indexer",
            stage_name=stage_name,
            status=status,
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception:
        return

def fetch_confluence_page(page_id, trace_id):
    start = time.time()
    base_url = get_env("CONFLUENCE_BASE_URL")
    email = get_env("CONFLUENCE_AUTH_USER")
    token = get_env("CONFLUENCE_API_TOKEN")
    if not base_url or not email or not token:
        safe_record_stage(trace_id, "confluence", "failed", start)
        return None
    try:
        r = requests.get(
            f"{base_url}/rest/api/content/{page_id}",
            headers={"Accept": "application/json"},
            params={"expand": "body.storage"},
            auth=HTTPBasicAuth(email, token),
            timeout=15,
        )
        if r.status_code != 200:
            safe_record_stage(trace_id, "confluence", "failed", start)
            return None
        safe_record_stage(trace_id, "confluence", "success", start)
        return r.json().get("body", {}).get("storage", {}).get("value")
    except Exception:
        safe_record_stage(trace_id, "confluence", "failed", start)
        return None

def build_source_url(page_id):
    base_url = get_env("CONFLUENCE_BASE_URL")
    if not base_url:
        return None
    return f"{base_url}/pages/{page_id}"


def insert_neon(page_id, title, source_url, created_at, trace_id):
    start = time.time()
    conn = get_db_conn()
    if not conn:
        safe_record_stage(trace_id, "neon", "failed", start)
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kb_pages
                    (page_id, page_title, source_url, created_at, is_stashed)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (page_id) DO NOTHING
                    """,
                    (page_id, title, source_url, created_at, True),
                )
        safe_record_stage(trace_id, "neon", "success", start)
        return True
    except Exception:
        safe_record_stage(trace_id, "neon", "failed", start)
        return False

def upsert_pinecone(page_id, title, trace_id):
    start = time.time()
    pc = get_pc()
    if not pc:
        safe_record_stage(trace_id, "pinecone", "failed", start)
        return False
    try:
        pc.Index("kb-pages").upsert_records(
            namespace="default",
            records=[{"_id": f"page:{page_id}", "page_title": title}],
        )
        safe_record_stage(trace_id, "pinecone", "success", start)
        return True
    except Exception:
        safe_record_stage(trace_id, "pinecone", "failed", start)
        return False

def process_page(page_id):
    trace_id = str(uuid.uuid4())
    start = time.time()
    init_state(page_id)

    try:
        try:
            title, created_at = fetch_confluence_page(page_id, trace_id)
            update_state(page_id, "confluence_status", "success")
        except Exception:
            update_state(page_id, "confluence_status", "failed")
            return False
        
        try:
            insert_neon(page_id, title, build_source_url(page_id), created_at, trace_id)
            update_state(page_id, "neon_status", "success")
        except Exception:
            update_state(page_id, "neon_status", "failed")
            pass

        try:
            upsert_pinecone(page_id, title, trace_id)
            update_state(page_id, "pinecone_status", "success")
        except Exception:
            update_state(page_id, "pinecone_status", "failed")
            pass

        record_indexing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="true",
            total_latency_ms=int((time.time() - start) * 1000),
        )

        return True

    except Exception:
        record_indexing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="failed",
            total_latency_ms=int((time.time() - start) * 1000),
        )
        return False

@app.post("/")
async def page_created(req: Request, bg: BackgroundTasks):
    try:
        body = await req.json()
        page_id = body["page_id"]
        bg.add_task(process_page, page_id)
        return {"accepted": True, "page_id": page_id}
    except Exception:
        return None

@app.post("/retry/confluence")
def retry_confluence(req: dict):
    try:
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
        except Exception:
            update_state(page_id, "confluence_status", "failed")
            return None
    except Exception:
        return None

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
async def page_created(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    page_id = payload["page"]["idAsString"]

    background_tasks.add_task(process_page, page_id)
    return {"status": "ok"}


@app.post("/webhooks/page-updated")
async def page_updated_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    page_id = payload["page"]["idAsString"]
    title = payload["page"]["title"]
    trace_id = str(uuid.uuid4())

    background_tasks.add_task(page_updated, trace_id, page_id)
    background_tasks.add_task(page_title_updated, trace_id, page_id, title)
    return {"status": "ok"}


@app.post("/webhooks/page-deleted")
async def page_deleted(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    page_id = payload["page"]["idAsString"]
    trace_id = str(uuid.uuid4())

    background_tasks.add_task(page_removed, trace_id, page_id)
    return {"status": "ok"}


@app.post("/webhooks/page-restored")
async def page_restored(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    page_id = payload["page"]["idAsString"]
    trace_id = str(uuid.uuid4())

    background_tasks.add_task(page_restored, trace_id, page_id)
    return {"status": "ok"}


def page_updated(trace_id: str, page_id: str):
    start = time.time()
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_pages
                SET is_stashed = TRUE
                WHERE page_id = %s
                """,
                (page_id,),
            )
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_stashed",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception as e:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_stashed",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise RuntimeError(
            f"Failed to stash page in kb_pages (page_id={page_id})"
        ) from e


def page_removed(trace_id: str, page_id: str):
    start = time.time()
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_chunks
                SET is_active = FALSE
                WHERE page_id = %s;

                UPDATE kb_images
                SET is_active = FALSE
                WHERE page_id = %s;
                """,
                (page_id, page_id),
            )
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_deleted",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception as e:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_deleted",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise RuntimeError(
            f"Failed to deactivate chunks/images (page_id={page_id})"
        ) from e


def page_restored(trace_id: str, page_id: str):
    start = time.time()
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_chunks
                SET is_active = TRUE
                WHERE page_id = %s;

                UPDATE kb_images
                SET is_active = TRUE
                WHERE page_id = %s;
                """,
                (page_id, page_id),
            )
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_restored",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception as e:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_restored",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise RuntimeError(
            f"Failed to restore chunks/images (page_id={page_id})"
        ) from e


def page_title_updated(trace_id: str, page_id: str, new_title: str):
    start = time.time()
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT page_title FROM kb_pages WHERE page_id = %s",
                (page_id,),
            )
            result = cur.fetchone()

        if not result:
            record_stage_execution(
                trace_id=trace_id,
                pipeline="webhook",
                stage_name="page_title_updated",
                status="success",
                latency_ms=int((time.time() - start) * 1000),
            )
            return

        current_title = result[0]

        if current_title == new_title:
            record_stage_execution(
                trace_id=trace_id,
                pipeline="webhook",
                stage_name="page_title_updated",
                status="success",
                latency_ms=int((time.time() - start) * 1000),
            )
            return

        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE kb_pages SET page_title = %s WHERE page_id = %s",
                (new_title, page_id),
            )

        upsert_pinecone(page_id, new_title, trace_id)

        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_title_updated",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception as e:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_title_updated",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        raise RuntimeError(
            f"Pinecone or DB update failed (page_id={page_id}, trace_id={trace_id})"
        ) from e
