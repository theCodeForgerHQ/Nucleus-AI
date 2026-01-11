import os
import requests
from requests.auth import HTTPBasicAuth

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]
SPACE_KEY = os.environ["CONFLUENCE_SPACE_KEY"]
PAGE_INDEXER_URL = os.environ["PAGE_INDEXER_URL"]
ANCESTOR_ID = os.getenv("ANCESTOR_ID")

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

def fetch_page_ids():
    print("fetching page ids")
    page_ids = []
    start = 0
    limit = 50

    while True:
        params = {
            "type": "page",
            "spaceKey": SPACE_KEY,
            "limit": limit,
            "start": start
        }

        if ANCESTOR_ID:
            params["ancestors"] = ANCESTOR_ID

        url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
        print(f"calling {url} start={start}")

        r = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)

        print(f"status {r.status_code}")

        if r.status_code != 200:
            print(r.text)
            raise RuntimeError("failed_to_fetch_pages")

        data = r.json()
        results = data.get("results", [])

        for page in results:
            page_id = page.get("id")
            if page_id:
                page_ids.append(page_id)

        if len(results) < limit:
            break

        start += limit

    print(f"total pages found {len(page_ids)}")
    return page_ids

def call_page_indexer(page_id):
    print(f"calling page-indexer for {page_id}")
    r = requests.post(
        PAGE_INDEXER_URL,
        json={"page_id": page_id},
        timeout=10
    )
    print(f"page-indexer status {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        raise RuntimeError("page_indexer_failed")

def main():
    try:
        page_ids = fetch_page_ids()
        for page_id in page_ids:
            try:
                call_page_indexer(page_id)
            except Exception as e:
                print(f"failed page {page_id} {repr(e)}")
        print("trigger completed")
    except Exception as e:
        print("fatal error", repr(e))
        raise

if __name__ == "__main__":
    main()
