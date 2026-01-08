import os
import re
import html
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from markdownify import markdownify as md

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

CONFLUENCE_AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
CONFLUENCE_HEADERS = {"Accept": "application/json"}

def get_confluence_page_content(page_id):
    url = f"{BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    resp = requests.get(url, headers=CONFLUENCE_HEADERS, params=params, auth=CONFLUENCE_AUTH)
    resp.raise_for_status()
    return resp.json()['body']['storage']['value']

def html_to_markdown_base(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for tag in soup(["table", "img"]):
        tag.decompose()

    body = soup.body or soup
    markdown = md(str(body), heading_style="ATX")
    return markdown.strip()
