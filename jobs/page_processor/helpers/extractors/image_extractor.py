from bs4 import BeautifulSoup, NavigableString

def extract_images(html):
    soup = BeautifulSoup(html, "html.parser")
    images = []

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue

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
            "caption": caption,
        })

        if caption_node:
            caption_node.extract()

        parent_figure = img.find_parent("figure")
        if parent_figure:
            parent_figure.decompose()
        else:
            img.decompose()

    return images
