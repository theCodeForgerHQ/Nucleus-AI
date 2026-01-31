from datetime import datetime, timezone
import clickhouse_connect

import threading
_client = threading.local()

def _get_client():
    if not hasattr(_client, "conn"):
        _client.conn = clickhouse_connect.get_client(
            host="clickhouse",
            port=8123,
            database="analytics",
        )
    return _client.conn

def init_analytics_schema():
    """
    Kept for backward compatibility.
    Schema is created by ClickHouse init scripts.
    """
    pass


def get_conn():
    """
    Kept for backward compatibility.
    Not meant for downstream usage.
    """
    return _get_client()


def record_indexing_result(
    trace_id,
    page_id,
    final_status,
    total_latency_ms,
):
    _get_client().insert(
        "indexing_page_result",
        [[
            trace_id,
            page_id,
            final_status,
            total_latency_ms,
            datetime.now(timezone.utc),
        ]],
        column_names=[
            "trace_id",
            "page_id",
            "final_status",
            "total_latency_ms",
            "indexed_at",
        ],
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
    _get_client().insert(
        "processing_page_result",
        [[
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
        ]],
        column_names=[
            "trace_id",
            "page_id",
            "final_status",
            "text_chunk_count",
            "table_chunk_count",
            "image_count",
            "avg_chunk_length",
            "min_chunk_length",
            "max_chunk_length",
            "total_embeddings",
            "total_latency_ms",
            "processed_at",
        ],
    )


def record_query_result(
    trace_id,
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
    _get_client().insert(
        "query_result",
        [[
            trace_id,
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
        ]],
        column_names=[
            "trace_id",
            "query",
            "final_status",
            "top_k_chunks",
            "context_chars",
            "answer_chars",
            "contradiction_score",
            "ragas_faithfulness",
            "ragas_answer_relevancy",
            "total_latency_ms",
            "answered_at",
        ],
    )


def record_stage_execution(
    trace_id,
    pipeline,
    stage_name,
    status,
    latency_ms,
):
    _get_client().insert(
        "stage_execution",
        [[
            trace_id,
            pipeline,
            stage_name,
            status,
            latency_ms,
            datetime.now(timezone.utc),
        ]],
        column_names=[
            "trace_id",
            "pipeline",
            "stage_name",
            "status",
            "latency_ms",
            "executed_at",
        ],
    )
