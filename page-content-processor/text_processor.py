from bs4 import BeautifulSoup
from markdownify import markdownify as md

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
    markdown = md(str(body), heading_style="ATX", strip=["a"])

    paragraphs = []
    for block in markdown.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = []
        for line in block.splitlines():
            if line.strip("# ").strip():
                lines.append(line)
        if lines:
            paragraphs.append("\n".join(lines))

    return "\n\n".join(paragraphs).strip()
