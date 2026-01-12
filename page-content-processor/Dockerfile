FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests python-dotenv llama-index

COPY . .

CMD ["python", "main.py"]
