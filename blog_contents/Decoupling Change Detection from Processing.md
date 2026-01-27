Change Detection Without Waste

After hardening the ingestion pipeline, the next step is to make that content usable downstream.

For retrieval-augmented generation, ingestion alone is not sufficient. Content must be chunked, embedded, and persisted in a form that downstream systems can query efficiently. Those embeddings, however, are only valid as long as the underlying content remains unchanged.

This creates an immediate dependency between content change and downstream representations.

When a page changes, its existing chunks and embeddings are no longer reliable. They must eventually be regenerated. The challenge is not how to chunk or embed, but when to do so.

In an enterprise environment, unnecessary re-chunking and re-embedding is not just inefficient—it is destabilising. Execution load grows with edit behaviour, costs become unpredictable, and downstream systems lose temporal control.

This iteration focuses on a deceptively simple problem: reacting to content changes without re-chunking and re-embedding everything, and without triggering uncontrolled downstream execution.

The Naïve Extremes

Two obvious approaches present themselves when external content can change.

Webhooks Everywhere

The first option is to rely entirely on webhooks. Any time a page changes, a webhook fires, and downstream processing reacts immediately. This approach optimises for immediacy, but it breaks down quickly under real conditions.

Edits are not atomic. A single logical update can generate dozens of low-level changes: word edits, formatting adjustments, metadata updates. Each change triggers a webhook. Each webhook triggers chunking and embedding. Execution explodes into redundant calls, duplicated work, and unnecessary writes.

The system becomes reactive but not intelligent. Load scales with edit behaviour, not with meaningful change.

Cron Only

The opposite extreme is to ignore events entirely and rely on scheduled processing. A cron job runs periodically, scans everything, and re-chunks and re-embeds all content regardless of whether it changed.

This is predictable, but wasteful. Unchanged pages are repeatedly processed. Execution time grows linearly with corpus size. As content scales, cadence must slow, and responsiveness suffers. In this model, stability is achieved by brute force.

Neither extreme is acceptable.

Separating Detection from Processing

The key realisation was that detecting change does not require downstream processing.

Most systems conflate the two. A page changes, so content is immediately re-chunked and re-embedded. But those steps are expensive and unnecessary at the moment of detection.

Instead, the pipeline treats change detection as its own concern.

Webhooks are still used—but only to answer a single question:

Which page changed?

No chunking occurs. No embeddings are generated. The webhook’s sole responsibility is to record that a specific page is now out of date with respect to downstream representations.

This keeps webhook execution lightweight, bounded, and safe. Even if a page generates multiple edit events, the system converges to a single fact: this page requires downstream refresh.

Deferred, Targeted Processing

Chunking and embedding happen later, under controlled conditions.

A scheduled cron job runs at a defined cadence. Instead of scanning the entire corpus, it queries only the set of pages marked as changed. Each of those pages is then processed through the downstream pipeline: chunking, embedding, and persistence into the vector store.

Once processing completes successfully, the change marker is cleared. The page returns to a clean state.

This creates a closed loop:

Webhook records that a page changed.

Cron decides when to re-chunk and re-embed.

Downstream representations are updated deterministically.

State is reset, preventing redundant future work.

No page is processed twice without reason. No unchanged content is touched.

From Reactive to Governed

At this stage, the system no longer reacts blindly to content edits, nor does it rely on periodic brute-force recomputation. It operates with awareness.

Webhooks provide signal without noise. Cron provides structure without waste. Together, they form a governed downstream processing loop that scales with content, not chaos.

This is the difference between pipelines that react and systems that endure.
