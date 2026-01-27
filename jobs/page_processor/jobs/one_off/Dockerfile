FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV HF_HOME=/models

COPY jobs/page_processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python - <<EOF
import nltk
nltk.download("punkt")
nltk.download("stopwords")
EOF

RUN python - <<EOF
from sentence_transformers import SentenceTransformer
SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
EOF

COPY common ./common
COPY jobs/common ./jobs/common
COPY jobs/page_processor/helpers ./jobs/page_processor/helpers
COPY jobs/page_processor/jobs ./jobs/page_processor/jobs

WORKDIR /app/jobs/page_processor/jobs/one_off

CMD ["python", "main.py"]
