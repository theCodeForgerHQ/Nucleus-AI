CREATE DATABASE IF NOT EXISTS analytics;
USE analytics;

CREATE TABLE IF NOT EXISTS stage_execution
(
    trace_id String,
    pipeline String,
    stage_name String,
    status String,
    latency_ms Int32,
    executed_at DateTime
)
ENGINE = MergeTree
ORDER BY (executed_at, trace_id);

CREATE TABLE IF NOT EXISTS indexing_page_result
(
    trace_id String,
    page_id String,
    final_status String,
    total_latency_ms Int32,
    indexed_at DateTime
)
ENGINE = MergeTree
ORDER BY (indexed_at, trace_id);

CREATE TABLE IF NOT EXISTS processing_page_result
(
    trace_id String,
    page_id String,
    final_status String,
    text_chunk_count Int32,
    table_chunk_count Int32,
    image_count Int32,
    avg_chunk_length Int32,
    min_chunk_length Int32,
    max_chunk_length Int32,
    total_embeddings Int32,
    total_latency_ms Int32,
    processed_at DateTime
)
ENGINE = MergeTree
ORDER BY (processed_at, trace_id);

CREATE TABLE IF NOT EXISTS query_result
(
    trace_id String,
    query String,
    final_status String,
    top_k_chunks Int32,
    context_chars Int32,
    answer_chars Int32,
    contradiction_score Nullable(Float64),
    ragas_faithfulness Nullable(Float64),
    ragas_answer_relevancy Nullable(Float64),
    total_latency_ms Int32,
    answered_at DateTime
)
ENGINE = MergeTree
ORDER BY (answered_at, trace_id);
