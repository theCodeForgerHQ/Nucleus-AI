FROM python:3.11-slim

WORKDIR /app

COPY jobs/page-indexer-trigger/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY jobs/common ./jobs/common
COPY jobs/page-indexer-trigger ./jobs/page-indexer-trigger

WORKDIR /app/jobs/page-indexer-trigger

CMD ["python", "main.py"]
