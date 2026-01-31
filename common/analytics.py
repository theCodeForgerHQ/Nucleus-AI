import duckdb
from datetime import datetime, timezone

DB_PATH = "/data/metrics.duckdb"

def init_analytics_schema():
    with duckdb.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS stage_execution (
                trace_id TEXT,
                pipeline TEXT,
                stage_name TEXT,
                status TEXT,
                latency_ms INTEGER,
                executed_at TIMESTAMP
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS indexing_page_result (
                trace_id TEXT,
                page_id TEXT,
                final_status TEXT,
                total_latency_ms INTEGER,
                indexed_at TIMESTAMP
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_page_result (
                trace_id TEXT,
                page_id TEXT,
                final_status TEXT,
                text_chunk_count INTEGER,
                table_chunk_count INTEGER,
                image_count INTEGER,
                avg_chunk_length INTEGER,
                min_chunk_length INTEGER,
                max_chunk_length INTEGER,
                total_embeddings INTEGER,
                total_latency_ms INTEGER,
                processed_at TIMESTAMP
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS query_result (
                trace_id TEXT,
                page_id TEXT,
                query TEXT,
                final_status TEXT,
                top_k_chunks INTEGER,
                context_chars INTEGER,
                answer_chars INTEGER,
                contradiction_score DOUBLE,
                ragas_faithfulness DOUBLE,
                ragas_answer_relevancy DOUBLE,
                total_latency_ms INTEGER,
                answered_at TIMESTAMP
            )
            """
        )

def _conn():
    return duckdb.connect(DB_PATH)

def record_indexing_result(
    trace_id,
    page_id,
    final_status,
    total_latency_ms,
):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO indexing_page_result
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                page_id,
                final_status,
                total_latency_ms,
                datetime.now(timezone.utc),
            ),
        )

def record_processing_result(
    trace_id,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
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
                datetime.now(timezone.utc),
            ),
        )

def record_query_result(
    trace_id,
    page_id,
    query,
    final_status,
    top_k_chunks,
    context_chars,
    answer_chars,
    contradiction_score,
    ragas_faithfulness,
    ragas_answer_relevancy,
    total_latency_ms,
):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO query_result
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                page_id,
                query,
                final_status,
                top_k_chunks,
                context_chars,
                answer_chars,
                contradiction_score,
                ragas_faithfulness,
                ragas_answer_relevancy,
                total_latency_ms,
                datetime.now(timezone.utc),
            ),
        )

def record_stage_execution(
    trace_id,
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
                trace_id,
                pipeline,
                stage_name,
                status,
                latency_ms,
                datetime.now(timezone.utc),
            ),
        )
