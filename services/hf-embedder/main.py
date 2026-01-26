from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from common.logging import setup_logging
import time

app = FastAPI()
logger = setup_logging("hf-embedder")

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

class EmbedRequest(BaseModel):
    texts: list[str]

@app.post("/")
def embed(req: EmbedRequest):
    count = len(req.texts)
    logger.info("embedding_request_received", count=count)

    start = time.time()

    try:
        embeddings = model.encode(
            req.texts,
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

        return {
            "embeddings": embeddings.tolist()
        }

    except Exception as e:
        logger.error(
            "embedding_request_failed",
            count=count,
            error=str(e),
        )
        raise

@app.get("/health")
def health():
    logger.info("health_check")
    return {"status": "ok"} 
