import requests
from common.utils import get_db_conn, get_env
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

def fetch_failed_pages(conn):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT page_id, neon_status, pinecone_status
                FROM kb_page_ingestion_state
                WHERE (
                    neon_status != 'success'
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

def set_all_fatal(page_id, err, conn):
    try:
        with conn.cursor() as cur:
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
        return False

def call_indexer(path, payload):
    try:
        base = get_env("PAGE_INDEXER_URL")

        if not base:
            return None

        r = http_session.post(
            f"{base}{path}",
            json=payload,
            timeout=20,
        )

        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def main():
    try:
        conn = get_db_conn()

        if not conn:
            return False
    
        rows = fetch_failed_pages(conn)
        if rows is None:
            return False
        if not rows:
            return True

        for row in rows:
            if not row:
                continue

            page_id, neon_status, pinecone_status = row
            data = call_indexer("/retry/confluence", {"page_id": page_id})

            if not data:
                set_all_fatal(page_id, "confluence retry failed", conn)
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

        return True
    except Exception:
        return False
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    main()
