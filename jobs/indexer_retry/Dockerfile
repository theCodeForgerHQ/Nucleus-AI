FROM python:3.11-slim

WORKDIR /app

COPY jobs/indexer_retry/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY indexer_retry ./indexer_retry

ENV PYTHONPATH=/app

WORKDIR /app/indexer_retry
CMD ["python", "main.py"]
