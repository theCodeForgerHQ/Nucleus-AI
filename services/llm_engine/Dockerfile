FROM python:3.11-slim

WORKDIR /app

COPY services/llm_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG GUARDRAILS_TOKEN

RUN guardrails configure --enable-metrics --enable-remote-inferencing  --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJnb29nbGUtb2F1dGgyfDEwNDE0MzgxNDA0MDkyNTAxNDkyMSIsImFwaUtleUlkIjoiMmM1YjkxOWItN2E2Ny00NjhmLTkxMGYtNDk1OWMyZDgwYjQ5Iiwic2NvcGUiOiJyZWFkOnBhY2thZ2VzIiwicGVybWlzc2lvbnMiOltdLCJpYXQiOjE3Njk1NzA1ODAsImV4cCI6NDkyMzE3MDU4MH0.qYVR0xBpm7D3cL-sMa1xforv45UGdrBLADsk5JLUJZc

RUN guardrails hub install hub://guardrails/valid_length;

COPY common ./common
COPY services/llm_engine ./llm_engine

ENV PYTHONPATH=/app

WORKDIR /app/llm_engine

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8200"]
