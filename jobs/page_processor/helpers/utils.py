import time
import hashlib
import requests

from datetime import datetime, timezone
from requests.auth import HTTPBasicAuth

from common.analytics import record_stage_execution
from common.utils import get_env


def safe_record_stage(trace_id, stage_name, status, start):
    try:
        record_stage_execution(
            trace_id=trace_id,
            pipeline="processing",
            stage_name=stage_name,
            status=status,
            latency_ms=int((time.time() - start) * 1000),
        )
    except Exception:
        return


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_confluence_page(page_id, trace_id):
    start = time.time()

    base_url = get_env("CONFLUENCE_BASE_URL")
    email = get_env("CONFLUENCE_AUTH_USER")
    token = get_env("CONFLUENCE_API_TOKEN")

    if not base_url or not email or not token:
        safe_record_stage(trace_id, "confluence_page_fetch", "failed", start)
        return None

    try:
        r = requests.get(
            f"{base_url}/rest/api/content/{page_id}",
            headers={"Accept": "application/json"},
            params={"expand": "body.storage"},
            auth=HTTPBasicAuth(email, token),
            timeout=20,
        )

        if r.status_code != 200:
            safe_record_stage(trace_id, "confluence_page_fetch", "failed", start)
            return None

        value = (
            r.json()
            .get("body", {})
            .get("storage", {})
            .get("value")
        )

        safe_record_stage(trace_id, "confluence_page_fetch", "success", start)
        return value

    except Exception:
        safe_record_stage(trace_id, "confluence_page_fetch", "failed", start)
        return None


def upsert_neon_images(conn, page_id, images, trace_id):
    start = time.time()

    if not conn:
        safe_record_stage(trace_id, "neon_images", "failed", start)
        return False

    try:
        now = datetime.now(timezone.utc)

        with conn:
            with conn.cursor() as cur:
                for img in images:
                    cur.execute(
                        """
                        INSERT INTO kb_images
                            (image_hash, page_id, image_src, caption, is_active, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (image_hash) DO NOTHING
                        """,
                        (
                            sha256(img["src"] + img["caption"]),
                            page_id,
                            img["src"],
                            img["caption"],
                            True,
                            now,
                        ),
                    )

        safe_record_stage(trace_id, "neon_images", "success", start)
        return True

    except Exception:
        safe_record_stage(trace_id, "neon_images", "failed", start)
        return False


def upsert_neon_chunks(conn, page_id, chunks, section_paths, trace_id):
    start = time.time()

    if not conn:
        safe_record_stage(trace_id, "neon_chunks", "failed", start)
        return False

    try:
        now = datetime.now(timezone.utc)

        with conn:
            with conn.cursor() as cur:
                for text, section in zip(chunks, section_paths):
                    cur.execute(
                        """
                        INSERT INTO kb_chunks
                            (chunk_hash, raw_chunk, is_active, created_at, section_path, page_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_hash) DO NOTHING
                        """,
                        (
                            sha256(text),
                            text,
                            True,
                            now,
                            section,
                            page_id,
                        ),
                    )

        safe_record_stage(trace_id, "neon_chunks", "success", start)
        return True

    except Exception:
        safe_record_stage(trace_id, "neon_chunks", "failed", start)
        return False


def upsert_pinecone_chunks(pc, chunks, trace_id):
    start = time.time()

    if not pc:
        safe_record_stage(trace_id, "pinecone_chunks", "failed", start)
        return False

    try:
        if chunks:
            index = pc.Index("kb-chunks")

            for i in range(0, len(chunks), 90):
                index.upsert_records(
                    namespace="default",
                    records=[
                        {
                            "_id": sha256(text),
                            "raw_chunk": text,
                        }
                        for text in chunks[i:i + 90]
                    ],
                )

        safe_record_stage(trace_id, "pinecone_chunks", "success", start)
        return True

    except Exception:
        safe_record_stage(trace_id, "pinecone_chunks", "failed", start)
        return False


def upsert_pinecone_images(pc, images, trace_id):
    start = time.time()

    if not pc:
        safe_record_stage(trace_id, "pinecone_images", "failed", start)
        return False

    try:
        if images:
            index = pc.Index("kb-images")

            for i in range(0, len(images), 90):
                index.upsert_records(
                    namespace="default",
                    records=[
                        {
                            "_id": sha256(img["src"] + img["caption"]),
                            "caption": img["caption"],
                        }
                        for img in images[i:i + 90]
                    ],
                )

        safe_record_stage(trace_id, "pinecone_images", "success", start)
        return True

    except Exception:
        safe_record_stage(trace_id, "pinecone_images", "failed", start)
        return False


def mark_page_unstashed(conn, page_id):
    if not conn:
        return False

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE kb_pages
                    SET is_stashed = FALSE
                    WHERE page_id = %s
                    """,
                    (page_id,),
                )
        return True

    except Exception:
        return False
