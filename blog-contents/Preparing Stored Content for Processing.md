Extracting Plain Text From Stored Pages

Once ingestion, change detection, and processing state were stabilised, the next requirement emerged naturally: downstream consumption.

Up to this point, the system focused on moving structured content into Confluence reliably. Pages were normalised, persisted, and tracked. That solved storage and governance. It did not yet solve use.

Downstream tasks—such as chunking, indexing, or semantic processing—do not operate well on rich HTML. They require clean, predictable plain text. That meant the pipeline needed a way to retrieve stored pages and extract their content in a controlled, repeatable form.

This work addresses that gap.

Treating Confluence as the Source of Truth

Rather than reusing intermediate ingestion artefacts, downstream processing pulls content directly from Confluence. This ensures that all later stages operate on the authoritative stored version, not a transient representation from an earlier run.

Each page is fetched using its page identifier, and only the storage-format body is retrieved. No metadata expansion, no rendering logic, no interpretation of editor state. The goal is narrow: obtain exactly what was persisted.

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

This keeps downstream behaviour aligned with what users actually see and edit.

Converting Storage HTML to Plain Text

Confluence storage format is HTML-like but not suitable for direct text processing. It contains structural tags, macros, and escaped entities that introduce noise if consumed as-is.

The extraction step therefore performs a minimal, deliberate transformation:

HTML is parsed structurally, not via string manipulation.

Text is extracted with preserved line boundaries to avoid semantic collapse.

Encoded entities are normalised.

Excess whitespace is collapsed to produce stable output.

def clean_html_to_text(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text(separator="\n")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

The result is not a presentation format. It is a processing format: consistent, readable, and suitable for chunking or indexing without further cleanup.

No assumptions are made about layout or markup beyond what is necessary to extract text safely.
