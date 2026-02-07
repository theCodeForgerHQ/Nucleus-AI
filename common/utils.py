import Pinecone
import os
import psycopg2

def get_env(key):
    try:
        return os.environ.get(key)
    except Exception:
        return None

def get_db_conn():
    try:
        url = get_env("NEON_DB_URL")
        if not url:
            return None
        return psycopg2.connect(url)
    except Exception:
        return None

def get_pinecone_client():
    api_key = get_env("PINECONE_API_KEY")
    if not api_key:
        return None
    try:
        _pc = Pinecone(api_key=api_key)
        return _pc
    except Exception:
        return None
