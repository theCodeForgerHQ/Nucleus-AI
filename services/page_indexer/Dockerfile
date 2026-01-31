FROM python:3.11-slim

WORKDIR /app

COPY services/page_indexer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY services/page_indexer ./page_indexer
ENV PYTHONPATH=/app

WORKDIR /app/page_indexer

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
