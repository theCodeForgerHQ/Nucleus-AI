FROM python:3.11-slim

WORKDIR /app

COPY services/reranker_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/reranker_service ./reranker_service

ENV PYTHONPATH=/app

WORKDIR /app/reranker_service

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
