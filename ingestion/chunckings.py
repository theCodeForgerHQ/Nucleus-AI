from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter


def chunk_text(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    metadata: dict | None = None
):
    """
    Split plain text into semantic chunks using LlamaIndex.

    Args:
        text (str): Clean plain text to chunk
        chunk_size (int): Target chunk size in tokens
        chunk_overlap (int): Overlap between chunks
        metadata (dict): Optional metadata (page_id, title, etc.)

    Returns:
        list[dict]: List of chunks with text + metadata
    """

    document = Document(
        text=text,
        metadata=metadata or {}
    )

    splitter = SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    nodes = splitter.get_nodes_from_documents([document])

    chunks = []
    for idx, node in enumerate(nodes):
        chunks.append({
            "chunk_index": idx,
            "text": node.text,
            "metadata": node.metadata
        })

    return chunks
