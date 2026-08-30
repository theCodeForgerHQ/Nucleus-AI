from pinecone import Pinecone
import os
import psycopg2
import logging

logging.basicConfig(level=logging.ERROR)

def get_env(key):
    try:
        return os.environ[key]
    except KeyError:
        logging.error(f'Environment variable {key} not found.')
        return None


def get_db_conn():
    try:
        url = get_env('NEON_DB_URL')
        if url is None:
            raise ValueError('Database URL is not set.')
        return psycopg2.connect(url)
    except (psycopg2.OperationalError, ValueError) as e:
        logging.error(f'Database connection error: {e}')
        return None


def get_pinecone_client():
    try:
        api_key = get_env('PINECONE_API_KEY')
        if api_key is None:
            raise ValueError('Pinecone API key is not set.')
        _pc = Pinecone(api_key=api_key)
        return _pc
    except ValueError as e:
        logging.error(f'Pinecone client error: {e}')
        return None