import os
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from image_extractor import extract_images
from text_processor import extract_tables, html_to_markdown

load_dotenv()

CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")

CONFLUENCE_AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
CONFLUENCE_HEADERS = {"Accept": "application/json"}

def get_confluence_page_content(page_id):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    resp = requests.get(url, headers=CONFLUENCE_HEADERS, params=params, auth=CONFLUENCE_AUTH)
    resp.raise_for_status()
    return resp.json()['body']['storage']['value']


def main(page_id):
    html = get_confluence_page_content(page_id)
    print("fetched html")

    images = extract_images(html)
    print("images")
    for img in images:
        print(img)

    table_facts = extract_tables(html)
    print("tables")
    for table in table_facts:
        for fact in table:
            print(fact)

    markdown = html_to_markdown(html)
    print("markdown")
    print(markdown)

    doc = Document(text=markdown)

    structural_parser = SentenceSplitter(
        chunk_size=800,
        chunk_overlap=100
    )
    structural_nodes = structural_parser.get_nodes_from_documents([doc])

    print("structural chunks")
    for i, node in enumerate(structural_nodes):
        print(f"chunk {i}")
        print(node.text)

    semantic_parser = SemanticSplitterNodeParser(
        buffer_size=1,
        breakpoint_percentile_threshold=95
    )
    semantic_nodes = semantic_parser.get_nodes_from_documents([doc])

    print("semantic chunks")
    for i, node in enumerate(semantic_nodes):
        print(f"chunk {i}")
        print(node.text)

if __name__ == "__main__":
    main("4096095")
