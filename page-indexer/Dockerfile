FROM python:3.11-slim

WORKDIR /app

COPY main.py .

RUN pip install fastapi uvicorn psycopg2-binary requests pinecone pymilvus

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
