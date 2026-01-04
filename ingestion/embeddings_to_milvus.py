import os
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from pymilvus import connections, Collection

HF_API_URL = "https://api-inference.huggingface.co/pipeline/feature-extraction/BAAI/bge-large-en-v1.5"
HF_HEADERS = {
    "Authorization": f"Bearer {os.getenv('HF_API_TOKEN')}",
    "Content-Type": "application/json"
}

BATCH_SIZE = 32
MILVUS_COLLECTION = "confluence_chunks"

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("NEON_HOST"),
        database=os.getenv("NEON_DB"),
        user=os.getenv("NEON_USER"),
        password=os.getenv("NEON_PASSWORD"),
        port=os.getenv("NEON_PORT", 5432)
    )

def ensure_embedding_tracker_table():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS embedded_chunks (
            chunk_hash TEXT PRIMARY KEY,
            embedded_at TIMESTAMP DEFAULT now()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def fetch_unembedded_chunks(limit: int):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.chunk_hash, c.chunk_text
        FROM chunks c
        LEFT JOIN embedded_chunks e
          ON c.chunk_hash = e.chunk_hash
        WHERE e.chunk_hash IS NULL
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def mark_chunks_embedded(chunk_hashes):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO embedded_chunks (chunk_hash) VALUES (%s) ON CONFLICT DO NOTHING",
        [(h,) for h in chunk_hashes]
    )
    conn.commit()
    cur.close()
    conn.close()

def embed_texts(texts):
    response = requests.post(
        HF_API_URL,
        headers=HF_HEADERS,
        json={"inputs": texts, "options": {"wait_for_model": True}},
        timeout=120
    )
    response.raise_for_status()
    embeddings = response.json()

    vectors = []
    for emb in embeddings:
        dim = len(emb[0])
        pooled = [0.0] * dim
        for token in emb:
            for i, v in enumerate(token):
                pooled[i] += v
        vectors.append([v / len(emb) for v in pooled])

    return vectors

def get_milvus_collection():
    connections.connect(
        alias="default",
        host=os.getenv("MILVUS_HOST"),
        port=os.getenv("MILVUS_PORT", "19530")
    )
    return Collection(MILVUS_COLLECTION)

def ingest_embeddings():
    ensure_embedding_tracker_table()
    collection = get_milvus_collection()

    while True:
        rows = fetch_unembedded_chunks(BATCH_SIZE)
        if not rows:
            print("✅ No new chunks to embed.")
            break

        chunk_hashes = [r["chunk_hash"] for r in rows]
        texts = [r["chunk_text"] for r in rows]

        print(f"🔹 Embedding {len(texts)} chunks...")
        vectors = embed_texts(texts)

        collection.insert([
            chunk_hashes,
            vectors
        ])

        mark_chunks_embedded(chunk_hashes)
        print(f"✅ Stored {len(chunk_hashes)} embeddings")

        time.sleep(1)

    collection.flush()

if __name__ == "__main__":
    ingest_embeddings()
