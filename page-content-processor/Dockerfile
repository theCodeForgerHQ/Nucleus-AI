FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi uvicorn requests python-dotenv psycopg2-binary \
    llama-index pinecone-client \
    beautifulsoup4 markdownify

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
