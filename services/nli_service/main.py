from fastapi import FastAPI, HTTPException
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
from pydantic import BaseModel, Field
import asyncio
from concurrent.futures import ThreadPoolExecutor

MODEL_NAME = "MoritzLaurer/deberta-v3-base-mnli"
app = FastAPI()

executor = ThreadPoolExecutor(max_workers=4)

class NLIRequest(BaseModel):
    premise: str = Field(..., max_length=20000)
    hypothesis: str = Field(..., max_length=20000)

def load_model():
    if not hasattr(load_model, "state"):
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
        load_model.state = (tokenizer, model)
    return load_model.state


def run_nli(tokenizer, model, premise, hypothesis):
    inputs = tokenizer(
        premise,
        hypothesis,
        return_tensors="pt",
        truncation=True,
        max_length=512
    )
    with torch.no_grad():
        outputs = model(**inputs)
    probs = torch.softmax(outputs.logits, dim=1)[0]
    labels = ["contradiction", "neutral", "entailment"]
    return {labels[i]: float(probs[i]) for i in range(3)}


@app.post("/nli")
async def nli(request: NLIRequest):
    try:
        # Load model using asyncio.to_thread since load_model performs blocking operations
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(executor, load_model)
        tokenizer, model = state

        result = await loop.run_in_executor(
            executor, run_nli, tokenizer, model, request.premise, request.hypothesis
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
