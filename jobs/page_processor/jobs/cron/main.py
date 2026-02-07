import time
import uuid
from llama_index.core.node_parser import SentenceSplitter, SemanticSplitterNodeParser
from llama_index.core.schema import Document
from llama_index.core.embeddings import BaseEmbedding
from common.analytics import (
    record_processing_result,
)
from jobs.page_processor.helpers.embedder.hf_embedder import embed
from jobs.page_processor.helpers.extractors.image_extractor import extract_images
from jobs.page_processor.helpers.extractors.text_processor import extract_tables, html_to_markdown
from jobs.page_processor.helpers.utils import (
    fetch_confluence_page,
    upsert_neon_images,
    upsert_neon_chunks,
    upsert_pinecone_chunks,
    upsert_pinecone_images,
    mark_page_unstashed,
)
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from common.utils import get_pinecone_client, get_db_conn

HF_EMBED_BATCH_SIZE = 32
RETRIES = 3
RETRY_SLEEP = 1.0

class HFEmbedding(BaseEmbedding):
    def _get_text_embedding(self, text):
        out = self._get_text_embeddings([text])
        return out[0] if out else None

    def _get_text_embeddings(self, texts):
        out = []
        for i in range(0, len(texts), HF_EMBED_BATCH_SIZE):
            try:
                out.extend(embed(texts[i:i + HF_EMBED_BATCH_SIZE]))
            except Exception:
                return []
        return out

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_text_embedding(query)

def fetch_stashed_pages(conn):
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT page_id
                FROM kb_pages
                WHERE is_stashed = TRUE
                """
            )
            return cur.fetchall()
    except Exception:
        return None

def fetch_neon_chunk_hashes(conn, page_id):
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_hash
                FROM kb_chunks
                WHERE page_id = %s AND is_active = TRUE
                """,
                (page_id,),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return None

def fetch_neon_image_hashes(conn, page_id):
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT image_hash
                FROM kb_images
                WHERE page_id = %s AND is_active = TRUE
                """,
                (page_id,),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return None

def deactivate_neon_chunks(conn, page_id, chunk_hashes, trace_id):
    if not chunk_hashes:
        return True
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_chunks
                SET is_active = FALSE, trace_id = %s
                WHERE page_id = %s AND chunk_hash = ANY(%s)
                """,
                (trace_id, page_id, chunk_hashes),
            )
        return True
    except Exception:
        return False

def deactivate_neon_images(conn, page_id, image_hashes, trace_id):
    if not image_hashes:
        return True
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE kb_images
                SET is_active = FALSE, trace_id = %s
                WHERE page_id = %s AND image_hash = ANY(%s)
                """,
                (trace_id, page_id, image_hashes),
            )
        return True
    except Exception:
        return False

def process_page(page_id, conn, pc):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        html = fetch_confluence_page(page_id, trace_id)
        
        if not html:
            return False

        try:
            images = extract_images(html) or []
            tables = extract_tables(html) or []
            table_chunks = []
            table_section_paths = []

            for t in tables:
                for fact in t["facts"]:
                    if fact and fact.strip():
                        table_chunks.append(fact.strip())
                        table_section_paths.append(" > ".join(t["section_path"]) if t["section_path"] else "")

        except Exception:
            return False

        try:
            markdown = html_to_markdown(html) or ""
            doc = Document(text=markdown)
            structural = SentenceSplitter(
                chunk_size=300,
                chunk_overlap=50,
                paragraph_separator="\n\n",
            ).get_nodes_from_documents([doc])

            embedder = HFEmbedding()
            semantic = SemanticSplitterNodeParser(
                embed_model=embedder,
                buffer_size=1,
                breakpoint_percentile_threshold=90,
            )

            semantic_nodes = []
            for node in structural:
                semantic_nodes.extend(
                    semantic.get_nodes_from_documents([Document(text=node.text)])
                )

            soup = BeautifulSoup(html, "html.parser")

            text_chunks = []
            section_paths = []

            for n in semantic_nodes:
                text = n.text.strip()
                if len(text) <= 30:
                    continue
                text_chunks.append(text)

                el = soup.find(string=lambda x: x and text[:50] in x)
                section_path = []
                if el:
                    current = el
                    last_level = 7
                    while True:
                        heading = current.find_previous(["h1","h2","h3","h4","h5","h6"])
                        if not heading:
                            break
                        level = int(heading.name[1])
                        if level < last_level:
                            htext = md("".join(str(x) for x in heading.contents)).strip()
                            if htext:
                                section_path.insert(0, htext)
                            last_level = level
                        current = heading
                section_paths.append(" > ".join(section_path))

        except Exception:
            return False

        step_results = {
            "neon_chunks": False,
            "pinecone_chunks": False,
            "neon_images": False,
            "pinecone_images": False,
        }
        
        for _ in range(RETRIES):
            if not step_results["neon_images"]:
                step_results["neon_images"] = upsert_neon_images(conn, page_id, images, trace_id)
        
            if not step_results["neon_chunks"]:
                step_results["neon_chunks"] = upsert_neon_chunks(
                    conn, page_id, text_chunks, section_paths, trace_id
                ) and upsert_neon_chunks(
                    conn, page_id, table_chunks, table_section_paths, trace_id
                )
        
            if not step_results["pinecone_chunks"]:
                step_results["pinecone_chunks"] = upsert_pinecone_chunks(
                    pc, text_chunks + table_chunks, trace_id
                )
        
            if not step_results["pinecone_images"]:
                step_results["pinecone_images"] = upsert_pinecone_images(
                    pc, images, trace_id
                )
        
            if step_results["neon_chunks"] and step_results["pinecone_chunks"] and step_results["neon_images"] and step_results["pinecone_images"]:
                break
        
            time.sleep(RETRY_SLEEP)
        
        success = step_results["neon_chunks"] and step_results["pinecone_chunks"]
        
        if success:
            mark_page_unstashed(conn, page_id)
        
        lengths = [len(c) for c in text_chunks]
        avg_len = sum(lengths) // len(lengths) if lengths else 0
        
        record_processing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="success" if success else "failed",
            text_chunk_count=len(text_chunks),
            table_chunk_count=len(table_chunks),
            image_count=len(images),
            avg_chunk_length=avg_len,
            min_chunk_length=min(lengths) if lengths else 0,
            max_chunk_length=max(lengths) if lengths else 0,
            total_embeddings=len(text_chunks) + len(table_chunks),
            total_latency_ms=int((time.time() - start) * 1000),
        )
        
        return success
    except Exception:
        record_processing_result(
            trace_id=trace_id,
            page_id=page_id,
            final_status="failed",
            text_chunk_count=0,
            table_chunk_count=0,
            image_count=0,
            avg_chunk_length=0,
            min_chunk_length=0,
            max_chunk_length=0,
            total_embeddings=0,
            total_latency_ms=int((time.time() - start) * 1000),
        )
        return False

def main():
    try:
        pc = get_pinecone_client()
        conn = get_db_conn()
        page_ids = fetch_stashed_pages(conn)
    except Exception:
        return False

    if not pc or not conn:
        return False

    if not page_ids:
        return True

    for page_id in page_ids:
        process_page(page_id, conn, pc)

    return True

if __name__ == "__main__":
    main()
