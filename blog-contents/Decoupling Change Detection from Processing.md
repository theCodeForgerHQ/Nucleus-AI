Change Detection Without Waste

After hardening the ingestion pipeline through real executions, the next constraint surfaced naturally: change propagation.

Once content is reliably ingested, normalised, and persisted, the question shifts from can we ingest? to when should we ingest again?
In an enterprise environment, unnecessary work is not just inefficient—it is destabilising.

This iteration focuses on a deceptively simple problem: reacting to content changes without reprocessing everything or triggering uncontrolled execution.

The Naïve Extremes

Two obvious approaches present themselves when external content can change.

Webhooks Everywhere

The first option is to rely entirely on webhooks.
Any time a page changes, a webhook fires, and the pipeline reacts immediately.

This approach optimises for immediacy, but it breaks down quickly under real conditions.

Edits are not atomic. A single logical update can generate dozens of low-level changes: word edits, formatting adjustments, metadata updates. Each change triggers a webhook. Each webhook triggers execution. Execution explodes into redundant calls, duplicated processing, and unnecessary writes.

The system becomes reactive but not intelligent. Load scales with edit behaviour, not with meaningful change.

Cron Only

The opposite extreme is to ignore events entirely and rely on scheduled execution.

A cron job runs periodically, scans everything, and reprocesses all content regardless of whether it changed.

This is predictable, but wasteful. Unchanged pages are fetched, transformed, and written repeatedly. Execution time grows linearly with corpus size. As content scales, cadence must slow, and responsiveness suffers.

In this model, stability is achieved by brute force.

Neither extreme is acceptable.

Separating Detection from Processing

The key realisation was that detecting change does not require processing content.

Most systems conflate the two. A page changes, so content is fetched, parsed, transformed, and persisted immediately. But those steps are expensive and unnecessary at the moment of detection.

Instead, the pipeline now treats change detection as its own concern.

Webhooks are still used—but only to answer a single question:

Which page changed?

No content is extracted. No transformation occurs. The webhook’s sole responsibility is to record that a specific page is now dirty.

This keeps webhook execution lightweight, bounded, and safe. Even if a page generates multiple edit events, the system converges to a single fact: this page has changed since last processing.

Deferred, Targeted Processing

Actual ingestion happens later, under controlled conditions.

A scheduled cron job runs at a defined cadence. Instead of scanning the entire corpus, it queries only the set of pages marked as changed. Each of those pages is then processed through the full pipeline: fetch, normalise, transform, and persist.

Once processing completes successfully, the change marker is cleared. The page returns to a clean state.

This creates a closed loop:

Webhook records that a page changed.

Cron decides when to process.

Processing updates content deterministically.

State is reset, preventing redundant future work.

No page is processed twice without reason. No unchanged content is touched.

Why This Matters

This design introduces several enterprise-grade properties.

Execution load is decoupled from edit frequency.
A burst of edits does not cause a burst of processing.

Processing is idempotent and intentional.
Every run has a clear reason to exist.

Failure handling remains page-scoped and recoverable.
A failed update does not poison future runs.

Most importantly, the system gains temporal control.
Change detection is immediate. Processing is deliberate.

This is not about optimisation for its own sake. It is about ensuring that ingestion behaviour remains predictable as content volume, edit frequency, and organisational reliance all increase.

From Reactive to Governed

At this stage, the pipeline no longer reacts blindly to events, nor does it rely on periodic brute-force reconciliation. It operates with awareness.

Webhooks provide signal without noise. Cron provides structure without waste. Together, they form a governed ingestion loop that scales with content, not chaos.

This is the difference between automation that responds and systems that endure.
