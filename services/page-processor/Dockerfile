FROM python:3.11-slim

WORKDIR /app

COPY services/page-processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY services/page-processor ./page-processor

ENV PYTHONPATH=/app

WORKDIR /app/page-processor

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
