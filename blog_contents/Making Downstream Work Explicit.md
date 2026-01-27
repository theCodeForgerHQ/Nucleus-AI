Tracking Processing State

Detecting that a page has changed identifies which pages may require downstream processing, but it does not indicate whether that processing has already occurred.

The system also needs to know whether that page has already been processed downstream, and whether it should be picked up again. Without a clear notion of current state, execution becomes either speculative or redundant.

Rather than inferring that state indirectly, the workflow records it explicitly.

Representing Processing State Directly

Processing state is tracked in a dedicated table. The Chunk Status Logs table exists solely to represent whether a page is pending downstream work. Each page has a single record, which is updated as processing progresses.

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

How It Is Used

When a page is created or updated during ingestion, it is registered in this table and marked as stashed. This signals that downstream processing is pending.

Scheduled jobs query only for pages currently marked as stashed. Pages that are unchanged or already processed are ignored.

Once processing completes successfully, the record is updated with a timestamp and the stashed flag is cleared. The page is not considered again until a future change explicitly marks it.

What This Enables

Processing becomes deliberate rather than inferred. Only pages that are explicitly marked are picked up. Unchanged content is never revisited. State remains visible, inspectable, and easy to reason about.
