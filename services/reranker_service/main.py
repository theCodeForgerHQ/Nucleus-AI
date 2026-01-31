from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reranker")

app = FastAPI(title="Local Reranker Service")

logger.info("Loading CrossEncoder model...")
reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
logger.info("Model loaded successfully")

class RerankRequest(BaseModel):
    query: str
    texts: list[str]

class RerankResponse(BaseModel):
    scores: list[float]

@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    logger.info(f"Reranking {len(req.texts)} candidates")
    pairs = [(req.query, text) for text in req.texts]
    scores = reranker.predict(pairs)
    return {"scores": [float(s) for s in scores]}
