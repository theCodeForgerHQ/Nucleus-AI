from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder
import asyncio
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="Local Reranker Service")

reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
executor = ThreadPoolExecutor(max_workers=4)

class RerankRequest(BaseModel):
    query: str = Field(..., max_length=1000)
    texts: list[str] = Field(..., max_length=50, description="List of texts, max 50 items")

class RerankResponse(BaseModel):
    scores: list[float]

def predict_scores(pairs):
    return reranker.predict(pairs)

@app.post("/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest):
    try:
        pairs = [(req.query, text[:2000]) for text in req.texts]
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(executor, predict_scores, pairs)
        return {"scores": [float(s) for s in scores]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
