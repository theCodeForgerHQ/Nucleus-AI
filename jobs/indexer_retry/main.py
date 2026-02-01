import os
import time
import requests
import psycopg2
from datetime import datetime

DATABASE_URL = os.environ["NEON_DB_URL"]
PAGE_INDEXER_URL = os.environ["PAGE_INDEXER_URL"]

RETRY_SLEEP = 1.0
TIMEOUT = 20

def db():
    return psycopg2.connect(DATABASE_URL)

def fetch_failed_pages():
    with db() as conn, conn.cursor() as cur:
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

def set_all_fatal(page_id, err):
    with db() as conn, conn.cursor() as cur:
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

def call_retry(path, payload):
    r = requests.post(
        f"{PAGE_INDEXER_URL}{path}",
        json=payload,
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"{path}:{r.status_code}")
    return r.json()

def main():
    rows = fetch_failed_pages()
    recovered = 0
    fatal = 0

    for page_id, confluence_status, neon_status, pinecone_status in rows:
        try:
            data = call_retry("/retry/confluence", {"page_id": page_id})
            title = data["title"]
            created_at = data["created_at"]
        except Exception as e:
            set_all_fatal(page_id, str(e))
            fatal += 1
            continue

        if neon_status != "success":
            try:
                call_retry(
                    "/retry/neon",
                    {
                        "page_id": page_id,
                        "title": title,
                        "created_at": created_at,
                    },
                )
            except Exception:
                pass

        if pinecone_status != "success":
            try:
                call_retry(
                    "/retry/pinecone",
                    {
                        "page_id": page_id,
                        "title": title,
                    },
                )
            except Exception:
                pass

        recovered += 1
        time.sleep(RETRY_SLEEP)

    if fatal:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
