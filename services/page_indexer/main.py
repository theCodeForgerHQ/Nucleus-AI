import time
import uuid
import requests
from fastapi import FastAPI, Request, BackgroundTasks
from datetime import datetime
from requests.auth import HTTPBasicAuth
from common.analytics import (
    record_stage_execution,
    record_indexing_result,
)
from common.utils import get_env, get_db_conn, get_pinecone_client
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    backoff_factor=1,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)

app = FastAPI()

def init_state(conn, page_id):
    try:
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

def update_state(conn, page_id, field, value):
    try:
        ALLOWED_FIELDS = {
            "confluence_status",
            "neon_status",
            "pinecone_status",
        }

        if field not in ALLOWED_FIELDS:
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
        return True
    except Exception:
        return False

def fetch_confluence_page(page_id, trace_id):
    start = time.time()
    base_url = get_env("CONFLUENCE_BASE_URL")
    email = get_env("CONFLUENCE_AUTH_USER")
    token = get_env("CONFLUENCE_API_TOKEN")

    if not base_url or not email or not token:
        safe_record_stage(trace_id, "confluence_page_fetch", "failed", start)
        return None
    
    try:
        r = http_session.get(
            f"{base_url}/rest/api/content/{page_id}",
            headers={"Accept": "application/json"},
            params={"expand": "body.storage"},
            auth=HTTPBasicAuth(email, token),
            timeout=20,
        )
    
        r.raise_for_status()
        safe_record_stage(trace_id, "confluence_page_fetch", "success", start)

        data = r.json()
        return (
            data["title"],
            datetime.fromisoformat(data["history"]["createdDate"].replace("Z", "+00:00")),
        )    

    except Exception:
        safe_record_stage(trace_id, "confluence_page_fetch", "failed", start)
        return None

def build_source_url(page_id):
    base_url = get_env("CONFLUENCE_BASE_URL")
    if not base_url:
        return None
    return f"{base_url}/pages/{page_id}"

def insert_neon(conn, page_id, title, source_url, created_at, trace_id):
    start = time.time()
    try:
        with conn, conn.cursor() as cur:
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

def upsert_pinecone(pc, page_id, title, trace_id):
    start = time.time()
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

def process_page(conn, pc, page_id):
    trace_id = str(uuid.uuid4())
    start = time.time()
    init_state(conn, page_id)

    try:
        title, created_at = fetch_confluence_page(page_id, trace_id)
        if not title or not created_at:
            return False
            
        insert_neon(conn, page_id, title, build_source_url(page_id), created_at, trace_id)
        upsert_pinecone(pc, page_id, title, trace_id)

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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.on_event("startup")
def startup():
    app.state.db = get_db_conn()
    app.state.pc = get_pinecone_client()

@app.post("/")
async def page_created(req: Request, bg: BackgroundTasks):
    try:
        body = await req.json()
        page_id = body["page_id"]
        bg.add_task(process_page, app.state.db, app.state.pc, page_id)
        return {"accepted": True, "page_id": page_id}
    except Exception:
        return {"accepted": False}

@app.post("/retry/confluence")
def retry_confluence(req: dict):
    trace_id = str(uuid.uuid4())
    conn = None
    try:
        conn = app.state.db
        if conn is None:
            return {"accepted": False, "reason": "db_unavailable"}

        page_id = req["page_id"]
        title, created_at = fetch_confluence_page(page_id, trace_id)
        update_state(conn, page_id, "confluence_status", "success")
        return {
            "page_id": page_id,
            "title": title,
            "created_at": created_at.isoformat(),
        }

    except Exception:
        if conn is not None:
            update_state(conn, page_id, "confluence_status", "failure")
        return {"accepted": False}
        
@app.post("/retry/neon")
def retry_neon(req: dict):
    trace_id = str(uuid.uuid4())
    conn = None
    try:
        conn = app.state.db
        if conn is None:
            return {"accepted": False, "reason": "db_unavailable"}

        page_id = req["page_id"]
        title = req["title"]
        created_at = datetime.fromisoformat(req["created_at"])

        insert_neon(conn, page_id, title, build_source_url(page_id), created_at, trace_id)
        update_state(conn, page_id, "neon_status", "success")
        return {"page_id": page_id}

    except Exception:
        if conn is not None:
            update_state(conn, page_id, "neon_status", "failed")
        return {"accepted": False}

@app.post("/retry/pinecone")
def retry_pinecone(req: dict):
    trace_id = str(uuid.uuid4())
    conn = None
    try:
        conn = app.state.db
        if conn is None:
            return {"accepted": False, "reason": "db_unavailable"}

        pc = app.state.pc
        page_id = req["page_id"]
        title = req["title"]

        upsert_pinecone(pc, page_id, title, trace_id)
        update_state(conn, page_id, "pinecone_status", "success")
        return {"page_id": page_id}

    except Exception:
        if conn is not None:
            update_state(conn, page_id, "pinecone_status", "failed")
        return {"accepted": False}
    
@app.post("/webhooks/page-created")
async def page_created(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        page_id = payload["page"]["idAsString"]
        background_tasks.add_task(process_page, app.state.db, app.state.pc, page_id)
        return {"accepted": True, "page_id": page_id}
    except Exception:
        return {"accepted": False}

@app.post("/webhooks/page-updated")
async def page_updated_webhook(request: Request, background_tasks: BackgroundTasks):
    trace_id = str(uuid.uuid4())

    try:
        payload = await request.json()
        page_id = payload["page"]["idAsString"]
        title = payload["page"]["title"]

        conn = app.state.db
        pc = app.state.pc

        background_tasks.add_task(page_updated, conn, trace_id, page_id)
        background_tasks.add_task(page_title_updated, conn, pc, trace_id, page_id, title)

        return {"accepted": True, "page_id": page_id}
    except Exception:
        return {"accepted": False}

@app.post("/webhooks/page-deleted")
async def page_deleted(request: Request, background_tasks: BackgroundTasks):
    trace_id = str(uuid.uuid4())
    try:
        payload = await request.json()
        page_id = payload["page"]["idAsString"]

        conn = app.state.db
        background_tasks.add_task(page_removed, conn, trace_id, page_id)

        return {"accepted": True, "page_id": page_id}
    except Exception:
        return {"accepted": False}

@app.post("/webhooks/page-restored")
async def page_restored(request: Request, background_tasks: BackgroundTasks):
    trace_id = str(uuid.uuid4())
    try:
        conn = app.state.db
        payload = await request.json()
        page_id = payload["page"]["idAsString"]

        background_tasks.add_task(page_restored, conn, trace_id, page_id)
        return {"accepted": True, "page_id": page_id}
    except Exception:
        return {"accepted": False}

def page_updated(conn, trace_id, page_id):
    start = time.time()
    try:
        with conn, conn.cursor() as cur:
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
        return True
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_stashed",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        return False

def page_removed(conn, trace_id, page_id):
    start = time.time()
    try:
        with conn, conn.cursor() as cur:
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
        return True
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_deleted",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        return False

def page_restored(conn, trace_id, page_id):
    start = time.time()
    try:
        with conn, conn.cursor() as cur:
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
        return True
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_restored",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        return False

def page_title_updated(conn, pc, trace_id, page_id, new_title):
    start = time.time()
    try:
        with conn, conn.cursor() as cur:
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
            return True

        current_title = result[0]

        if current_title == new_title:
            record_stage_execution(
                trace_id=trace_id,
                pipeline="webhook",
                stage_name="page_title_updated",
                status="success",
                latency_ms=int((time.time() - start) * 1000),
            )
            return True

        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE kb_pages SET page_title = %s WHERE page_id = %s",
                (new_title, page_id),
            )

        upsert_pinecone(pc, page_id, new_title, trace_id)

        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_title_updated",
            status="success",
            latency_ms=int((time.time() - start) * 1000),
        )
        return True
    except Exception:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="webhook",
            stage_name="page_title_updated",
            status="failed",
            latency_ms=int((time.time() - start) * 1000),
        )
        return False
