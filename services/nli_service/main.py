from fastapi import FastAPI
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

MODEL_NAME = "MoritzLaurer/deberta-v3-base-mnli"
app = FastAPI()

def load_model():
    try:
        if not hasattr(load_model, "state"):
            tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
            model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
            load_model.state = (tokenizer, model)
        return load_model.state
    except Exception:
        return None


def run_nli(tokenizer, model, premise, hypothesis):
    try:
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
    except Exception:
        return None


@app.post("/nli")
async def nli(request: dict):
    try:
        premise = request.get("premise")
        hypothesis = request.get("hypothesis")
        if premise is None or hypothesis is None:
            return None
        state = load_model()
        if state is None:
            return None
        tokenizer, model = state
        result = run_nli(tokenizer, model, premise, hypothesis)
        if result is None:
            return None
        return result
    except Exception:
        return None
