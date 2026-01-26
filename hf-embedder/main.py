from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI()

model = SentenceTransformer("intfloat/e5-large-v2")

class EmbedRequest(BaseModel):
    texts: list[str]

@app.post("/")
def embed(req: EmbedRequest):
    embeddings = model.encode(
        req.texts,
        normalize_embeddings=True
    )
    return {
        "embeddings": embeddings.tolist()
    }
