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
