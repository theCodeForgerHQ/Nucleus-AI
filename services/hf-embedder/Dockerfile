FROM python:3.11-slim

WORKDIR /app

ENV HF_HOME=/models
ENV PYTHONPATH=/app

COPY services/hf-embedder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python - <<EOF
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
EOF

COPY common ./common
COPY services/hf-embedder ./hf-embedder

WORKDIR /app/hf-embedder

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
