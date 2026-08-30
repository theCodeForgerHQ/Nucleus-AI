from pinecone import Pinecone
import os
import psycopg2
import logging

logging.basicConfig(level=logging.ERROR)

def get_env(key):
    try:
        return os.environ.get(key)
    except Exception as e:
        logging.error(f'Error getting environment variable {key}: {e}')
        return None


def get_db_conn():
    try:
        url = get_env("NEON_DB_URL")
        return psycopg2.connect(url)
    except Exception as e:
        logging.error(f'Error connecting to database: {e}')
        return None


def get_pinecone_client():
    try:
        api_key = get_env("PINECONE_API_KEY")
        _pc = Pinecone(api_key=api_key)
        return _pc
    except Exception as e:
        logging.error(f'Error creating Pinecone client: {e}')
        return None