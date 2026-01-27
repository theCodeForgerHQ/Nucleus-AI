from sentence_transformers import SentenceTransformer
from common.logging import setup_logging
import time

logger = setup_logging("hf-embedder")

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def embed(texts: list[str]) -> list[list[float]]:
    count = len(texts)
    logger.info("embedding_request_received", count=count)

    start = time.time()

    try:
        embeddings = model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        duration = time.time() - start

        logger.info(
            "embedding_request_success",
            count=count,
            duration_ms=int(duration * 1000),
        )

        return embeddings.tolist()

    except Exception as e:
        logger.error(
            "embedding_request_failed",
            count=count,
            error=str(e),
        )
        raise
