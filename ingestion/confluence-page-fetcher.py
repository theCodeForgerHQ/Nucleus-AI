import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

base_url = os.getenv("CONFLUENCE_BASE_URL")
email = os.getenv("CONFLUENCE_AUTH_USER")
api_token = os.getenv("CONFLUENCE_API_TOKEN")

def get_confluence_page_content(page_id):
    url = f"{base_url}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    headers = {"Accept": "application/json"}
    response = requests.get(
        url,
        params=params,
        headers=headers,
        auth=HTTPBasicAuth(email, api_token)
    )
    response.raise_for_status()
    return response.json()
