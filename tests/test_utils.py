import os
import pytest
import logging
from common.utils import get_env, get_db_conn, get_pinecone_client

# Mock logging to capture log messages
class TestLogging:
    @pytest.fixture(autouse=True)
    def caplog(self, caplog):
        self.caplog = caplog

    def test_get_env_logging(self):
        os.environ['TEST_KEY'] = 'test_value'
        assert get_env('TEST_KEY') == 'test_value'
        assert get_env('INVALID_KEY') is None
        assert 'Error getting environment variable INVALID_KEY' in self.caplog.text

    def test_get_db_conn_logging(self, monkeypatch):
        # Test valid connection
        monkeypatch.setenv('NEON_DB_URL', 'valid_db_url')
        assert get_db_conn() is not None

        # Test invalid connection
        monkeypatch.setenv('NEON_DB_URL', 'invalid_db_url')
        with pytest.raises(Exception):
            get_db_conn()
        assert 'Error connecting to database' in self.caplog.text

    def test_get_pinecone_client_logging(self, monkeypatch):
        # Test valid Pinecone client creation
        monkeypatch.setenv('PINECONE_API_KEY', 'valid_api_key')
        assert get_pinecone_client() is not None

        # Test invalid Pinecone client creation
        monkeypatch.setenv('PINECONE_API_KEY', 'invalid_api_key')
        with pytest.raises(Exception):
            get_pinecone_client()
        assert 'Error creating Pinecone client' in self.caplog.text