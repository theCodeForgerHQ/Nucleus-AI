from llama_index.core import Document
from llama_index.core.node_parser import SemanticSplitterNodeParser
from llama_index.embeddings.gemini import GeminiEmbedding


def semantic_chunk_text(
    text: str,
    metadata: dict | None = None,
    breakpoint_percentile_threshold: int = 95,
    buffer_size: int = 1,
):

    document = Document(
        text=text,
        metadata=metadata or {}
    )

    embed_model = GeminiEmbedding(
        model="models/embedding-001"  
    )

    splitter = SemanticSplitterNodeParser(
        embed_model=embed_model,
        buffer_size=buffer_size,
        breakpoint_percentile_threshold=breakpoint_percentile_threshold,
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
