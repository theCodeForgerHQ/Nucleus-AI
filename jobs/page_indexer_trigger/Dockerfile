FROM python:3.11-slim

WORKDIR /app

COPY jobs/page_indexer_trigger/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY jobs/common ./jobs/common
COPY jobs/page_indexer_trigger ./jobs/page_indexer_trigger

ENV PYTHONPATH=/app

WORKDIR /app/jobs/page_indexer_trigger
CMD ["python", "main.py"]
