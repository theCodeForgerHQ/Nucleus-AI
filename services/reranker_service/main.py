from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

app = FastAPI(title="Local Reranker Service")

reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

class RerankRequest(BaseModel):
    query: str
    texts: list[str]

class RerankResponse(BaseModel):
    scores: list[float]

@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    try:
        pairs = [(req.query, text) for text in req.texts]
        scores = reranker.predict(pairs)
        return {"scores": [float(s) for s in scores]}
    except Exception:
        return {"scores": []}
