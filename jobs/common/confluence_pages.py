from common.utils import get_env
import requests
from requests.auth import HTTPBasicAuth
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

            try:
                r = http_session.get(
                    f"{base_url}/rest/api/content",
                    auth=auth,
                    headers=headers,
                    params=params,
                    timeout=15,
                )
                r.raise_for_status()
            except Exception:
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
