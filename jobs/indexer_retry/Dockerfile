FROM python:3.11-slim

WORKDIR /app

COPY jobs/indexer_retry/requirements.txt .
RUN pip install -r requirements.txt

COPY jobs/indexer_retry ./indexer_retry

ENV PYTHONPATH=/app

WORKDIR /app/indexer_retry
CMD ["python", "main.py"]
