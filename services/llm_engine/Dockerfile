FROM python:3.11-slim

WORKDIR /app

COPY common ./common
COPY services/llm_engine/requirements.txt .
RUN pip install -r requirements.txt

RUN --mount=type=secret,id=guardrails_token \
    guardrails configure \
      --enable-metrics \
      --enable-remote-inferencing \
      --token "$(cat /run/secrets/guardrails_token)"

RUN guardrails hub install hub://guardrails/valid_length;

COPY common ./common
COPY services/llm_engine ./llm_engine

ENV PYTHONPATH=/app

WORKDIR /app/llm_engine

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
