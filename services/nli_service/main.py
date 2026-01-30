from fastapi import FastAPI
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

MODEL_NAME = "MoritzLaurer/deberta-v3-base-mnli"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)

app = FastAPI()

@app.post("/nli")
async def nli(request: dict):
    premise = request["premise"]
    hypothesis = request["hypothesis"]

    inputs = tokenizer(
        premise,
        hypothesis,
        return_tensors="pt",
        truncation=True
    )

    with torch.no_grad():
        outputs = model(**inputs)

    probs = torch.softmax(outputs.logits, dim=1)[0]

    labels = ["contradiction", "neutral", "entailment"]
    return {labels[i]: float(probs[i]) for i in range(3)}
