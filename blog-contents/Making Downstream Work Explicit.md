Tracking Processing State

In the previous iteration, execution was made observable and selective. Change detection was separated from processing, and runs were driven by explicit signals rather than blanket re-execution.

That refinement surfaced a quieter problem.

Knowing that a page changed was not enough. The system also needed to know whether that page had already been processed downstream, and whether it should be picked up again. Execution logs alone could not answer that question reliably.

To address this, a second table was introduced.

Why a Second Table Was Necessary

The original ingestion logs are append-only. Each execution produces records, and those records are never modified. This is intentional and useful for auditability, but it makes them unsuitable for representing current processing state.

Determining whether a page should be picked up again would require scanning multiple rows across runs and interpreting their meaning. That approach does not scale cleanly and introduces ambiguity.

Instead of inferring state, the workflow now records it explicitly.

Representing Processing State Directly

The Chunk Status Logs table exists solely to track whether a page is pending downstream work.

Each page appears once. That record is updated over time as processing progresses. The table does not store execution history or content. It stores only what is necessary to decide whether work remains.

{
  "name": "Chunk Status Logs",
  "primaryField": "ID",
  "fields": [
    { "name": "ID", "type": "autonumber", "primary": true },
    { "name": "isStashed", "type": "checkbox" },
    { "name": "Last Chunked At", "type": "date" },
    { "name": "Page ID", "type": "singleLineText" },
    { "name": "Page Title", "type": "singleLineText" }
  ]
}

The fields are intentionally minimal. Each one serves a single operational purpose.

How It Is Used

When a page is successfully created or updated during ingestion, it is registered in this table and marked as stashed. This indicates that the page is eligible for downstream processing.

Scheduled jobs query only for pages that are currently stashed. Pages that are unchanged or already processed are ignored.

Once processing completes, the record is updated with a timestamp and the stashed flag is cleared. The page is no longer picked up until a future change explicitly marks it again.

What This Enables

This approach separates concerns cleanly.

Execution logs remain a record of what happened. The chunk status table represents what still needs to happen. Each serves a distinct role.

As a result, downstream processing becomes targeted and predictable. No work is inferred. No pages are processed unnecessarily. State is explicit and externally inspectable.

This addition does not introduce new behaviour. It makes existing behaviour deliberate.
