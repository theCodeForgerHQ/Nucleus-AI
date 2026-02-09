from common.utils import get_env
import time
import requests
from jobs.common.confluence_pages import fetch_page_ids
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

def call_page_indexer_once(page_indexer_url, page_id):
    try:
        r = http_session.post(
            page_indexer_url,
            json={"page_id": page_id},
            timeout=20,
        )

        r.raise_for_status()
        return True
    except Exception:
        return False

def main():
    page_indexer_url = get_env("PAGE_INDEXER_URL")
    if not page_indexer_url:
        return False

    try:
        page_ids = fetch_page_ids()

        for page_id in page_ids:
            for _ in range(3):
                if call_page_indexer_once(page_indexer_url, page_id):
                    break
                time.sleep(1.0)

        return True

    except Exception:
        return False

if __name__ == "__main__":
    main()
