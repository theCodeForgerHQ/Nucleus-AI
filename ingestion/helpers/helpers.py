import os
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

def extract_images(html):
    soup = BeautifulSoup(html, "html.parser")
    images = []

    for img in soup.find_all("img"):
        src = img.get("src")
        caption = img.get("alt")
        caption_node = None

        if not caption:
            figure = img.find_parent("figure")
            if figure:
                figcap = figure.find("figcaption")
                if figcap:
                    caption = figcap.get_text(strip=True)
                    caption_node = figcap

        if not caption:
            sib = img.next_sibling
            if isinstance(sib, NavigableString) and sib.strip():
                caption = sib.strip()
                caption_node = sib

        if not caption:
            next_p = img.find_next_sibling("p")
            if next_p and next_p.get_text(strip=True):
                caption = next_p.get_text(strip=True)
                caption_node = next_p
            else:
                prev_p = img.find_previous_sibling("p")
                if prev_p and prev_p.get_text(strip=True):
                    caption = prev_p.get_text(strip=True)
                    caption_node = prev_p

        if not caption:
            heading = img.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
            if heading and heading.get_text(strip=True):
                caption = f"section:{heading.get_text(strip=True)}"
        
        if not caption:
            caption = f"title:{src.split('/')[-1].split('.')[0]}"

        images.append({
            "src": src,
            "caption": caption
        })

        if caption_node:
            caption_node.extract()

        parent_figure = img.find_parent("figure")
        if parent_figure:
            parent_figure.decompose()
        else:
            img.decompose()

    return images

def extract_tables(html):
    soup = BeautifulSoup(html, "html.parser")
    tables_out = []

    for table in soup.find_all("table"):
        heading = table.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        section_heading = md("".join(str(x) for x in heading.contents)).strip() if heading else None

        classes = table.get("class", [])
        if any(c in classes for c in ["sidebar", "nomobile", "nowraplinks"]):
            continue

        caption_tag = table.find("caption")
        table_caption = md("".join(str(x) for x in caption_tag.contents)).strip() if caption_tag else None

        thead = table.find("thead")
        column_headers = []

        if thead:
            header_rows = thead.find_all("tr")
            grids = []
            for row in header_rows:
                cells = row.find_all("th")
                texts = [md("".join(str(x) for x in c.contents)).strip() for c in cells]
                grids.append(texts)

            max_len = max(len(r) for r in grids)
            for r in grids:
                r.extend([""] * (max_len - len(r)))

            for col in range(max_len):
                parts = [r[col] for r in grids if r[col]]
                column_headers.append(" ".join(parts))

        facts = []

        for row in table.find_all("tr"):
            if thead and row in thead.find_all("tr"):
                continue

            for img in row.find_all("img"):
                img.decompose()

            if not thead:
                th = row.find("th")
                tds = row.find_all("td")
                if th and tds:
                    key = md("".join(str(x) for x in th.contents)).strip()
                    val = md("".join(str(x) for x in tds[0].contents)).strip()
                    if key and val:
                        facts.append(f"{key}: {val}")
                continue

            cells = row.find_all(["td", "th"])
            if column_headers and cells:
                for h, cell in zip(column_headers, cells):
                    val = md("".join(str(x) for x in cell.contents)).strip()
                    if h and val:
                        fact = "\n".join(
                            x for x in [table_caption, section_heading, f"{h}: {val}"] if x
                        )
                        facts.append(fact)

        if facts:
            tables_out.append(facts)

    return tables_out

def html_to_markdown(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for tag in soup(["table", "img"]):
        tag.decompose()

    body = soup.body or soup
    markdown = md(str(body), heading_style="ATX")
    return markdown.strip()

print(extract_tables(get_confluence_page_content("4882469")))
