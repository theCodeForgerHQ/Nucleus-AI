FROM python:3.11-slim

WORKDIR /app

COPY services/page-indexer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY services/page-indexer ./page-indexer

ENV PYTHONPATH=/app

WORKDIR /app/page-indexer

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
