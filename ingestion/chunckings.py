from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter


def sentence_chunk_text(
    text: str,
    metadata: dict | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
):

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
