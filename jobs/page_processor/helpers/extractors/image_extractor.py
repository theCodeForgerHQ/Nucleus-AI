from bs4 import BeautifulSoup, NavigableString

def extract_images(html):
    try:
        soup = BeautifulSoup(html, "html.parser")
        images = []

        for img in soup.find_all("img"):
            src = img.get("src")

            if not src:
                continue

            if src.startswith("//"):
                src = "https:" + src

            elif not src.startswith("http://") and not src.startswith("https://"):
                src = "https://" + src.lstrip("/")

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

            section_path = []
            current = img
            last_level = 7

            while True:
                heading = current.find_previous(["h1","h2","h3","h4","h5","h6"])

                if not heading:
                    break
                level = int(heading.name[1])

                if level < last_level:
                    text = heading.get_text(strip=True)
                    section_path.insert(0, text)
                    last_level = level
                current = heading

            if not caption:
                if section_path:
                    caption = "section:" + " > ".join(section_path)

                else:
                    try:
                        caption = "title:" + src.split("/")[-1].split(".")[0]
                    except Exception:
                        caption = "title:unknown"

            images.append({"src": src, "caption": caption, "section_path": section_path})

            if caption_node:
                try:
                    caption_node.extract()
                except Exception:
                    pass

            parent_figure = img.find_parent("figure")
            try:
                if parent_figure:
                    parent_figure.decompose()

                else:
                    img.decompose()

            except Exception:
                pass

        if not images:
            return []

        return images
    except Exception:
        return None
