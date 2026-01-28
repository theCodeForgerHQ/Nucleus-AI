import duckdb
from datetime import datetime

DB_PATH = "metrics.duckdb"

def _conn():
    return duckdb.connect(DB_PATH)

def record_indexing_result(
    page_id,
    final_status,
    total_latency_ms,
):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO indexing_page_result
            VALUES (?, ?, ?, ?)
            """,
            (
                page_id,
                final_status,
                total_latency_ms,
                datetime.utcnow(),
            ),
        )

def record_processing_result(
    page_id,
    final_status,
    text_chunk_count,
    table_chunk_count,
    image_count,
    avg_chunk_length,
    min_chunk_length,
    max_chunk_length,
    total_embeddings,
    total_latency_ms,
):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO processing_page_result
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                final_status,
                text_chunk_count,
                table_chunk_count,
                image_count,
                avg_chunk_length,
                min_chunk_length,
                max_chunk_length,
                total_embeddings,
                total_latency_ms,
                datetime.utcnow(),
            ),
        )

def record_stage_execution(
    page_id,
    pipeline,
    stage_name,
    status,
    latency_ms,
):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO stage_execution
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                pipeline,
                stage_name,
                status,
                latency_ms,
                datetime.utcnow(),
            ),
        )
