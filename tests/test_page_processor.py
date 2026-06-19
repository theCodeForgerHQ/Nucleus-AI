import pytest
from unittest.mock import MagicMock, patch
from jobs.page_processor.jobs.cron.main import (
    fetch_stashed_pages,
    fetch_neon_chunk_hashes,
    fetch_neon_image_hashes,
    deactivate_neon_chunks,
    deactivate_neon_images,
    process_page,
)

@pytest.fixture
def mock_db_conn():
    return MagicMock()

@pytest.fixture
def mock_pinecone_client():
    return MagicMock()

def test_fetch_stashed_pages_handles_exception(mock_db_conn):
    mock_db_conn.cursor.side_effect = Exception('DB error')
    result = fetch_stashed_pages(mock_db_conn)
    assert result is None


def test_fetch_neon_chunk_hashes_handles_exception(mock_db_conn):
    mock_db_conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception('DB error')
    result = fetch_neon_chunk_hashes(mock_db_conn, 'page_id')
    assert result == []


def test_fetch_neon_image_hashes_handles_exception(mock_db_conn):
    mock_db_conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception('DB error')
    result = fetch_neon_image_hashes(mock_db_conn, 'page_id')
    assert result == []


def test_deactivate_neon_chunks_handles_exception(mock_db_conn):
    mock_db_conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception('DB error')
    result = deactivate_neon_chunks(mock_db_conn, 'page_id', ['hash1'], 'trace_id')
    assert result is False


def test_deactivate_neon_images_handles_exception(mock_db_conn):
    mock_db_conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception('DB error')
    result = deactivate_neon_images(mock_db_conn, 'page_id', ['hash1'], 'trace_id')
    assert result is False


def test_process_page_handles_exceptions(mock_db_conn, mock_pinecone_client):
    # Mocking fetch_confluence_page to return valid HTML
    with patch('jobs.page_processor.jobs.cron.main.fetch_confluence_page', return_value='<html></html>'):
        # Mocking extract_images and extract_tables to raise exceptions
        with patch('jobs.page_processor.jobs.cron.main.extract_images', side_effect=Exception('Image extraction error')):
            result = process_page('page_id', mock_db_conn, mock_pinecone_client)
            assert result is False
        with patch('jobs.page_processor.jobs.cron.main.extract_tables', side_effect=Exception('Table extraction error')):
            result = process_page('page_id', mock_db_conn, mock_pinecone_client)
            assert result is False
