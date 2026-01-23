from fastapi import FastAPI
from pydantic import BaseModel
from model import embed

app = FastAPI()

class EmbedRequest(BaseModel):
    text: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/v1/embeddings")
def create_embedding(req: EmbedRequest):
    vector = embed(req.text)
    return {
        "data": [
            {
                "embedding": vector
            }
        ]
    }
