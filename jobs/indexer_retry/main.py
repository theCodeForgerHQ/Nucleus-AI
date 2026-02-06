import os
import requests
import psycopg2

TIMEOUT = 20

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

def fetch_failed_pages():
    try:
        conn = get_db_conn()
        if not conn:
            return None
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT page_id, confluence_status, neon_status, pinecone_status
                FROM kb_page_ingestion_state
                WHERE (
                    confluence_status != 'success'
                    OR neon_status != 'success'
                    OR pinecone_status != 'success'
                )
                AND confluence_status != 'fatal'
                AND neon_status != 'fatal'
                AND pinecone_status != 'fatal'
                """
            )
            return cur.fetchall()
    except Exception:
        return None

def set_all_fatal(page_id, err):
    try:
        conn = get_db_conn()
        if not conn:
            return None
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_page_ingestion_state
                SET confluence_status = 'fatal',
                    neon_status = 'fatal',
                    pinecone_status = 'fatal',
                    last_error = %s,
                    updated_at = now()
                WHERE page_id = %s
                """,
                (err, page_id),
            )
        return True
    except Exception:
        return None

def call_indexer(path, payload):
    try:
        base = get_env("PAGE_INDEXER_URL")
        if not base:
            return None
        r = requests.post(
            f"{base}{path}",
            json=payload,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def main():
    try:
        rows = fetch_failed_pages()
        if rows is None:
            return
        for row in rows:
            if not row:
                continue
            page_id, confluence_status, neon_status, pinecone_status = row
            data = call_indexer("/retry/confluence", {"page_id": page_id})
            if not data:
                set_all_fatal(page_id, "confluence retry failed")
                continue
            title = data.get("title")
            created_at = data.get("created_at")
            if neon_status != "success":
                call_indexer(
                    "/retry/neon",
                    {
                        "page_id": page_id,
                        "title": title,
                        "created_at": created_at,
                    },
                )
            if pinecone_status != "success":
                call_indexer(
                    "/retry/pinecone",
                    {
                        "page_id": page_id,
                        "title": title,
                    },
                )
        return
    except Exception:
        return

if __name__ == "__main__":
    main()
