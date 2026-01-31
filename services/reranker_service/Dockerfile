FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY services/reranker_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/reranker_service ./reranker_service

ENV PYTHONPATH=/app

WORKDIR /app/reranker_service

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
