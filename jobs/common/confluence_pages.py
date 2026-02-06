import os
import requests
from requests.auth import HTTPBasicAuth

def get_env(key):
    try:
        return os.environ.get(key)
    except Exception:
        return None

def fetch_page_ids():
    try:
        base_url = get_env("CONFLUENCE_BASE_URL")
        email = get_env("CONFLUENCE_AUTH_USER")
        api_token = get_env("CONFLUENCE_API_TOKEN")
        space_key = get_env("CONFLUENCE_SPACE_KEY")
        ancestor_id = get_env("CONFLUENCE_ANCESTOR_ID")

        if not base_url or not email or not api_token or not space_key:
            return None

        auth = HTTPBasicAuth(email, api_token)
        headers = {"Accept": "application/json"}

        page_ids = []
        start = 0
        limit = 50

        while True:
            params = {
                "type": "page",
                "spaceKey": space_key,
                "limit": limit,
                "start": start,
            }

            if ancestor_id:
                params["ancestors"] = ancestor_id

            r = requests.get(
                f"{base_url}/rest/api/content",
                auth=auth,
                headers=headers,
                params=params,
                timeout=15,
            )

            if r.status_code != 200:
                return page_ids if page_ids else None

            data = r.json()
            results = data.get("results", [])

            for page in results:
                pid = page.get("id")
                if pid:
                    page_ids.append(pid)

            if len(results) < limit:
                return page_ids

            start += limit
    except Exception:
        return None
