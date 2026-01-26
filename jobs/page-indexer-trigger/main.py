import os
import time
import requests
from common.logging import setup_logging
from jobs.common.confluence_pages import fetch_page_ids

PAGE_INDEXER_URL = os.environ["PAGE_INDEXER_URL"]
RETRIES = 3
RETRY_SLEEP = 1.0

logger = setup_logging("page-indexer-trigger")

def call_page_indexer(page_id):
    last_err = None

    for attempt in range(1, RETRIES + 1):
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
            logger.warning(
                "page_indexer_retry_failed",
                page_id=page_id,
                attempt=attempt,
                error=last_err,
            )
            time.sleep(RETRY_SLEEP)

    raise RuntimeError(last_err)

def main():
    logger.info("trigger_start")

    page_ids = fetch_page_ids()
    logger.info("pages_discovered", count=len(page_ids))

    failures = 0

    for page_id in page_ids:
        try:
            call_page_indexer(page_id)
            logger.info("page_index_triggered", page_id=page_id)
        except Exception as e:
            failures += 1
            logger.error("page_index_trigger_failed", page_id=page_id, error=str(e))

    logger.info(
        "trigger_complete",
        total=len(page_ids),
        failures=failures,
    )

    if failures:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
