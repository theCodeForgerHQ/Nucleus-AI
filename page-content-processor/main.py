import os
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from pydantic import Field
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from llama_index.core.embeddings import BaseEmbedding
from image_extractor import extract_images
from text_processor import extract_tables, html_to_markdown

load_dotenv()

CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL")
EMAIL = os.getenv("CONFLUENCE_AUTH_USER")
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
HF_EMBEDDER_URL = os.getenv("HF_EMBEDDER_URL", "http://localhost:8000/")
print(HF_EMBEDDER_URL)

CONFLUENCE_AUTH = HTTPBasicAuth(EMAIL, API_TOKEN)
CONFLUENCE_HEADERS = {"Accept": "application/json"}


class HFHTTPEmbedding(BaseEmbedding):
    url: str = Field()

    def _get_text_embedding(self, text):
        r = requests.post(self.url, json={"texts": [text]}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"][0]

    def _get_text_embeddings(self, texts):
        r = requests.post(self.url, json={"texts": texts}, timeout=30)
        r.raise_for_status()
        return r.json()["embeddings"]

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_text_embedding(query)


def get_confluence_page_content(page_id):
    url = f"{CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    r = requests.get(url, headers=CONFLUENCE_HEADERS, params=params, auth=CONFLUENCE_AUTH)
    r.raise_for_status()
    return r.json()["body"]["storage"]["value"]


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
        chunk_size=300,
        chunk_overlap=50
    )
    structural_nodes = structural_parser.get_nodes_from_documents([doc])

    print("structural chunks")
    for i, node in enumerate(structural_nodes):
        print(f"chunk {i}")
        print(node.text)

    embedder = HFHTTPEmbedding(url=HF_EMBEDDER_URL)

    semantic_parser = SemanticSplitterNodeParser(
        embed_model=embedder,
        buffer_size=1,
        breakpoint_percentile_threshold=90
    )
    semantic_nodes = []
    for node in structural_nodes:
        semantic_nodes.extend(
            semantic_parser.get_nodes_from_documents(
                [Document(text=node.text)]
            )
        )

    print("semantic chunks")
    for i, node in enumerate(semantic_nodes):
        print(f"chunk {i}")
        print(node.text)

    return {
        "images": images,
        "table_facts": table_facts,
        "structural_chunks": [n.text for n in structural_nodes],
        "semantic_chunks": [n.text for n in semantic_nodes]
    }


if __name__ == "__main__":
    main("4096095")
