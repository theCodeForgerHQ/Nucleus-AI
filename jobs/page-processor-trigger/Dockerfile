FROM python:3.11-slim

WORKDIR /app

COPY jobs/page-processor-trigger/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY jobs/common ./jobs/common
COPY jobs/page-processor-trigger ./jobs/page-processor-trigger

ENV PYTHONPATH=/app

WORKDIR /app/jobs/page-processor-trigger

CMD ["python", "main.py"]
