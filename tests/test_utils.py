import os
import pytest
import logging
from common.utils import get_env, get_db_conn, get_pinecone_client

# Mock logging to capture log messages
class TestLogging:
    def setup_method(self):
        self.log_messages = []
        logging.getLogger().handlers[0].stream = self

    def write(self, message):
        self.log_messages.append(message.strip())

    def flush(self):
        pass

# Test for get_env function
def test_get_env_key_not_found(monkeypatch, caplog):
    monkeypatch.delenv('TEST_ENV_VAR', raising=False)
    result = get_env('TEST_ENV_VAR')
    assert result is None
    assert 'Environment variable TEST_ENV_VAR not found.' in caplog.text

# Test for get_db_conn function when NEON_DB_URL is not set
def test_get_db_conn_no_url(monkeypatch, caplog):
    monkeypatch.delenv('NEON_DB_URL', raising=False)
    result = get_db_conn()
    assert result is None
    assert 'Database URL is not set.' in caplog.text

# Test for get_db_conn function with invalid URL
def test_get_db_conn_invalid_url(monkeypatch, caplog):
    monkeypatch.setenv('NEON_DB_URL', 'invalid_url')
    result = get_db_conn()
    assert result is None
    assert 'Database connection error:' in caplog.text

# Test for get_pinecone_client function when PINECONE_API_KEY is not set
def test_get_pinecone_client_no_api_key(monkeypatch, caplog):
    monkeypatch.delenv('PINECONE_API_KEY', raising=False)
    result = get_pinecone_client()
    assert result is None
    assert 'Pinecone API key is not set.' in caplog.text

# Test for get_pinecone_client function with invalid API key
def test_get_pinecone_client_invalid_key(monkeypatch, caplog):
    monkeypatch.setenv('PINECONE_API_KEY', 'invalid_key')
    result = get_pinecone_client()
    assert result is None
    assert 'Pinecone client error:' in caplog.text