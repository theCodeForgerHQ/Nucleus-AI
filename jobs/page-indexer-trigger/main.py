import os
import requests
from requests.auth import HTTPBasicAuth

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]
SPACE_KEY = os.environ["CONFLUENCE_SPACE_KEY"]
PAGE_INDEXER_URL = os.environ["PAGE_INDEXER_URL"]
CONFLUENCE_ANCESTOR_ID = os.getenv("CONFLUENCE_ANCESTOR_ID")

AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json"}

def fetch_page_ids():
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

        if CONFLUENCE_ANCESTOR_ID:
            params["ancestors"] = CONFLUENCE_ANCESTOR_ID

        url = f"{CONFLUENCE_BASE_URL}/rest/api/content"
        try:
            r = requests.get(url, auth=AUTH, headers=HEADERS, params=params, timeout=15)
        except Exception as e:
            return page_ids, f"request_failed {repr(e)}"

        if r.status_code != 200:
            return page_ids, "failed_to_fetch_pages"

        data = r.json()
        results = data.get("results", [])

        for page in results:
            page_id = page.get("id")
            if page_id:
                page_ids.append(page_id)

        if len(results) < limit:
            break

        start += limit

    return page_ids, None

def call_page_indexer(page_id):
    try:
        r = requests.post(
            PAGE_INDEXER_URL,
            json={"page_id": page_id},
            timeout=10
        )
    except Exception as e:
        return f"page_indexer_request_failed {repr(e)}"

    if r.status_code != 200:
        return "page_indexer_failed"

    return None

def main():
    errors = []

    page_ids, err = fetch_page_ids()
    if err:
        errors.append(err)

    for page_id in page_ids:
        err = call_page_indexer(page_id)
        if err:
            errors.append(f"failed page {page_id} {err}")

    return errors

if __name__ == "__main__":
    main()
