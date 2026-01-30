FROM python:3.11-slim

WORKDIR /app

COPY services/nli_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY services/nli_service ./nli_service

ENV PYTHONPATH=/app

WORKDIR /app/nli_service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

