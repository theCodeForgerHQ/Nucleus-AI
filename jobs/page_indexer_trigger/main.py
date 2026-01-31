import os
import time
import requests
from jobs.common.confluence_pages import fetch_page_ids

PAGE_INDEXER_URL = os.environ["PAGE_INDEXER_URL"]
RETRIES = 3
RETRY_SLEEP = 1.0

def call_page_indexer(page_id):
    last_err = None

    for _ in range(RETRIES):
        try:
            r = requests.post(
                PAGE_INDEXER_URL,
                json={"page_id": page_id},
                timeout=10,
            )

            if r.status_code != 200:
                raise RuntimeError(f"status_{r.status_code}")

            return

        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_SLEEP)

    raise RuntimeError(last_err)

def main():
    page_ids = fetch_page_ids()
    failures = 0

    for page_id in page_ids:
        try:
            call_page_indexer(page_id)
        except Exception:
            failures += 1

    if failures:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
