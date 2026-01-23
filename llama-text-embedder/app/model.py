from transformers import AutoTokenizer, AutoModel
import torch

MODEL_ID = "nvidia/llama-nemotron-embed-1b-v2"
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
tokenizer = AutoTokenizer.from_pretrained(
    MODEL_ID,
    trust_remote_code=True
)

model = AutoModel.from_pretrained(
    MODEL_ID,
    trust_remote_code=True,
    torch_dtype=torch.float16 if device.type == "cuda" else torch.float32
).to(device)
model.eval()

def embed(text: str):
    with torch.no_grad():
        inputs = tokenizer(
            text,
            return_tensors="pt",
            padding=True,
            truncation=True
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}
        outputs = model(**inputs)
        embedding = outputs.last_hidden_state.mean(dim=1)

        return embedding[0].cpu().tolist()
