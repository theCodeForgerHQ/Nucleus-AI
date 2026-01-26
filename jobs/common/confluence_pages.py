import os
import requests
from requests.auth import HTTPBasicAuth

CONFLUENCE_BASE_URL = os.environ["CONFLUENCE_BASE_URL"]
EMAIL = os.environ["CONFLUENCE_AUTH_USER"]
API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]
SPACE_KEY = os.environ["CONFLUENCE_SPACE_KEY"]
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
            "start": start,
        }

        if CONFLUENCE_ANCESTOR_ID:
            params["ancestors"] = CONFLUENCE_ANCESTOR_ID

        r = requests.get(
            f"{CONFLUENCE_BASE_URL}/rest/api/content",
            auth=AUTH,
            headers=HEADERS,
            params=params,
            timeout=15,
        )

        if r.status_code != 200:
            raise RuntimeError("failed_to_fetch_pages")

        data = r.json()
        results = data.get("results", [])

        for page in results:
            if page.get("id"):
                page_ids.append(page["id"])

        if len(results) < limit:
            break

        start += limit

    return page_ids
