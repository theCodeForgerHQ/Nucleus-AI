# Nucleus AI — Complete End-to-End Interview Guide

> Everything in this document is derived from the actual code in this repo.
> Read it top to bottom once, then use Part 9 (Q&A) and Part 10 (weaknesses) as drill material.

---

# PART 0 — The 60-Second Pitch

> "Nucleus AI is a production-shaped enterprise knowledge system. It takes Confluence
> as the source of truth, pulls every page, splits text / tables / images into
> semantically coherent units, indexes them into Pinecone plus Postgres, and serves
> grounded answers over a five-stage online pipeline: intent routing → hybrid
> retrieval → cross-encoder reranking → constrained generation → NLI-based
> contradiction validation. It streams its own reasoning steps to the UI over SSE.
> Freshness is handled by Confluence webhooks that only *mark* pages dirty, with a
> cron job doing hash-diffed delta reprocessing later. Every stage writes a
> trace-correlated row into ClickHouse, surfaced in Metabase."

**The one-sentence design thesis:** *separate detection from processing, separate
recall from precision, and separate generation from trust.* Those three separations
explain almost every decision in the codebase.

---

# PART 1 — The Problem & Why This Shape

## 1.1 The business problem
Enterprise knowledge rots. It lives in Confluence/Notion/Drive, gets edited
constantly, and nobody knows which version is current. "Ask someone who remembers"
becomes the real retrieval system. A naive chatbot over those docs fails in three
specific ways:

| Failure | Cause | What Nucleus does |
|---|---|---|
| Confidently wrong answers | LLM hallucinates beyond retrieved context | NLI contradiction check + "answer only from context" prompt |
| Stale answers | Index not updated after edits | Webhook stash + cron hash-delta + `is_active` soft deletes |
| Irrelevant answers | Vector similarity ≠ relevance | Cross-encoder rerank + score threshold gate |

## 1.2 Why a multi-service architecture and not one FastAPI app
This is the single most likely "why" question. The honest answer has four parts:

1. **Different resource profiles.** The reranker and NLI service load PyTorch models
   into memory and are CPU-bound. The LLM engine is almost entirely I/O-bound
   (waiting on Groq, Pinecone, Postgres). Putting them in one process means one
   PyTorch inference blocks the event loop and starves concurrent requests.
   Separating them lets each scale independently — you can run 1 LLM engine and
   4 reranker replicas.
2. **Different deploy cadence.** Prompt changes ship hourly. Model swaps ship
   monthly. Coupling them means every prompt tweak rebuilds a 2GB torch image.
3. **Different failure semantics.** If the reranker dies, retrieval can still
   degrade to fused-score-only. If they share a process, the whole thing dies.
4. **Model warm-up cost.** Models are baked into the Docker image at **build time**
   (see the `RUN python - <<EOF` blocks in the Dockerfiles) so container start
   doesn't pay a HuggingFace download. That only makes sense per-service.

**Counter-argument you should volunteer:** for this traffic level, a monolith would
be simpler and cheaper, and the network hop adds 5–15ms per call. The split is
justified by *operational* reasons, not performance ones at current scale.

---

# PART 2 — Full Architecture Map

## 2.1 The components

### Long-running services (`services/`)
| Service | Port | Responsibility | Key dependency |
|---|---|---|---|
| `page-indexer` | 8000 | Receives page IDs / Confluence webhooks. Writes page **metadata** to Postgres + Pinecone. Exposes `/retry/*` endpoints. | Confluence REST, Neon, Pinecone |
| `llm-engine` | 8000 | The online RAG runtime. `POST /query` (LangGraph) and `POST /query/stream` (SSE). | Pinecone, Neon, reranker, NLI, Groq |
| `reranker-service` | 8000 | `POST /rerank` → cross-encoder relevance scores | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `nli-service` | 8000 | `POST /nli` → `{contradiction, neutral, entailment}` probabilities | `MoritzLaurer/deberta-v3-base-mnli` |
| `frontend` | 3000→3001 | Next.js chat UI + server-side proxy to llm-engine | — |
| `clickhouse` | 8123/9000 | Analytics/telemetry store | — |
| `metabase` | 3000 | Dashboards over ClickHouse | — |

### One-shot jobs (`jobs/`) — run via `docker compose --profile trigger run --rm`
| Job | Responsibility |
|---|---|
| `page_indexer_trigger` | Lists all Confluence page IDs in the space, POSTs each to page-indexer. Bulk bootstrap. |
| `indexer_retry` | Reads `kb_page_ingestion_state`, replays failed stages via `/retry/*`, marks `fatal` when unrecoverable. |
| `page_processor/jobs/one_off` | **Full** processing: fetch HTML → extract → chunk → embed → upsert. For initial load. |
| `page_processor/jobs/cron` | **Delta** processing: only `is_stashed = TRUE` pages, hash-diff add/remove. For steady state. |

### Shared code (`common/`, `jobs/common/`)
- `common/utils.py` — env access, Postgres connection, Pinecone client factory.
- `common/analytics.py` — the four ClickHouse writers + thread-local client.
- `jobs/common/confluence_pages.py` — paginated Confluence page-ID listing.

## 2.2 The data stores and *why three of them*

This is a classic interview trap: "why not just one database?" The answer is
**access pattern**, and each store earns its place:

| Store | Shape | Why it, specifically |
|---|---|---|
| **Pinecone** (`kb-pages`, `kb-chunks`, `kb-images`) | Approximate nearest-neighbour over dense vectors | Vector similarity at low latency. Postgres+pgvector would work but you'd be managing HNSW index builds, memory, and recall tuning yourself. Pinecone also does **server-side embedding** (see 5.3), removing an entire model from the request path. |
| **Neon / Postgres** (`kb_pages`, `kb_chunks`, `kb_images`, `kb_page_ingestion_state`) | Relational, transactional, OLTP | Vector DBs are bad at "give me the full text + section + active flag for these 20 IDs," at per-row state machines, and at joins. Postgres is the **system of record**; Pinecone only holds IDs + vectors. |
| **ClickHouse** (`stage_execution`, `indexing_page_result`, `processing_page_result`, `query_result`) | Columnar OLAP, `MergeTree` | Telemetry is append-only, high-volume, and queried as aggregates ("p95 latency by stage, last 7 days"). Columnar storage reads only the columns a query touches, and `ORDER BY (executed_at, trace_id)` gives time-range pruning. Doing this in Postgres would bloat the OLTP database that serves the hot retrieval path — you'd be contending on the same buffer pool as your user-facing queries. |

**Key architectural idea — the split-brain index:** Pinecone stores *only* `_id` and
the embedded field. All metadata (raw text, section path, page_id, `is_active`) lives
in Postgres. Retrieval is therefore two-phase: Pinecone returns IDs + scores, then a
single `WHERE chunk_hash IN (...)` hydrates them from Postgres.

**Why do it that way?**
- Vector-DB metadata filtering is limited, costs money per stored byte, and is
  painful to update (you'd re-upsert a vector just to flip a boolean).
- Flipping `is_active = false` in Postgres is a single cheap `UPDATE`.
- You can change your Postgres schema freely without touching the index.

**Cost:** an extra round trip, and the two stores can drift out of sync (a chunk in
Pinecone with no Postgres row). The code handles that defensively — the fusion loop
does `if not meta: continue`, silently dropping orphans.

---

# PART 3 — The Data Model

## 3.1 Postgres (system of record)

> ⚠️ Note: the DDL for these tables is **not in the repo** — only the ClickHouse
> schema is (`clickhouse/init/logs.sql`). The Postgres tables were created manually
> in Neon. Inferred shape from the queries:

```
kb_pages
  page_id      TEXT PRIMARY KEY     -- Confluence page ID
  page_title   TEXT
  source_url   TEXT                 -- {base}/pages/{page_id}
  created_at   TIMESTAMPTZ
  is_stashed   BOOLEAN              -- TRUE = needs downstream processing

kb_chunks
  chunk_hash   TEXT PRIMARY KEY     -- sha256(chunk text)  ← content-addressed
  raw_chunk    TEXT
  section_path TEXT                 -- "Heading > Subheading"
  page_id      TEXT
  is_active    BOOLEAN              -- soft delete
  created_at   TIMESTAMPTZ
  trace_id     TEXT                 -- set when deactivated

kb_images
  image_hash   TEXT PRIMARY KEY     -- sha256(src + caption)
  page_id      TEXT
  image_src    TEXT
  caption      TEXT
  is_active    BOOLEAN
  created_at   TIMESTAMPTZ
  trace_id     TEXT

kb_page_ingestion_state                  -- per-page stage state machine
  page_id            TEXT PRIMARY KEY
  confluence_status  TEXT   -- pending | success | failed | fatal
  neon_status        TEXT
  pinecone_status    TEXT
  last_error         TEXT
  updated_at         TIMESTAMPTZ
```

### Why `chunk_hash = sha256(text)` as the primary key — this is a big one
This is **content-addressed storage**, the same idea as Git objects or Docker layers.
Consequences, all of them deliberate:

1. **Idempotency for free.** Re-running the processor on an unchanged page produces
   identical hashes, so `INSERT ... ON CONFLICT (chunk_hash) DO NOTHING` is a no-op.
   You can safely re-run the job any number of times.
2. **Change detection for free.** A chunk whose text changed gets a *different* hash.
   Set-difference between old and new hashes gives you exactly the adds and removes
   without diffing text (see 4.6).
3. **Cross-store join key.** The same hash is the Pinecone `_id` and the Postgres PK.
   No ID-mapping table needed.
4. **Automatic dedup.** Identical boilerplate repeated on 50 pages stores one row and
   one vector.

**The trade-off you must be able to state:** because the hash is the PK and it's
global (not scoped per page), a chunk shared by two pages collapses to a single row
owned by whichever page inserted it first — the `page_id` column records only one
owner. So *provenance for duplicated content is lossy*. A composite key
`(page_id, chunk_hash)` would fix attribution but lose dedup. This project chose
dedup. Know which one you chose and why.

Also: because the hash is over text only, an unchanged chunk that *moved to a
different section* keeps the same hash, so its stale `section_path` is never updated.

### Why `is_active` (soft delete) instead of `DELETE`
- **Auditability** — you can see what the KB used to say and when it changed
  (`trace_id` on the row ties it to the exact processing run).
- **Reversibility** — the `page-restored` webhook just flips the flag back to TRUE.
  A hard delete would require full reprocessing.
- **Pinecone stays consistent** — vectors are *never* deleted in this codebase. The
  `is_active` flag is the only thing that changes. This has a real consequence:
  deleted content is still retrievable from the vector index, and the system handles
  it by **labelling** rather than filtering (see 5.7). That's an unusual choice worth
  defending.

**Cost:** tables grow forever, every query must remember `is_active`, and Pinecone
accumulates dead vectors that waste recall slots in the top-20. Needs a reaper job.

## 3.2 Pinecone indexes

| Index | `_id` | Embedded field | Used for |
|---|---|---|---|
| `kb-pages` | `page:{page_id}` | `page_title` | Page-level relevance signal in fusion |
| `kb-chunks` | `sha256(text)` | `raw_chunk` | Primary chunk recall |
| `kb-images` | `sha256(src+caption)` | `caption` | Related-image retrieval |

All use `namespace="default"`.

**Images are retrieved by their caption text, not by pixels.** No CLIP, no vision
model. This is a pragmatic call: caption/alt-text/figcaption/section-heading text is
usually a good description, and text embeddings are ~1000× cheaper than running a
multimodal encoder. The `image_extractor.py` has a 5-level caption fallback chain
(`alt` → `<figcaption>` → adjacent text node → next `<p>` → previous `<p>` →
`"section:<path>"` → `"title:<filename>"`) precisely because this text is the *only*
retrieval signal the image has.

## 3.3 ClickHouse (telemetry)

```
stage_execution         (trace_id, pipeline, stage_name, status, latency_ms, executed_at)
indexing_page_result    (trace_id, page_id, final_status, total_latency_ms, indexed_at)
processing_page_result  (trace_id, page_id, final_status, text_chunk_count,
                         table_chunk_count, image_count, avg/min/max_chunk_length,
                         total_embeddings, total_latency_ms, processed_at)
query_result            (trace_id, query, final_status, top_k_chunks, context_chars,
                         answer_chars, contradiction_score, total_latency_ms, answered_at)
```

**The design pattern here is "one row per stage + one row per outcome."**
`stage_execution` is the fine-grained timeline; the three `*_result` tables are the
roll-up. Joining on `trace_id` reconstructs a full request.

- `pipeline` is one of `indexer | processing | query | webhook` — so one table serves
  all four pipelines, and dashboards filter on it.
- `ENGINE = MergeTree ORDER BY (executed_at, trace_id)` — the sort key *is* the index
  in ClickHouse. Time-first ordering means "last 24h" queries skip almost all data
  granules. `trace_id` second makes per-trace lookup within a time window fast.
- `Nullable(Float64)` for `contradiction_score` because the general-reply branch has
  no score. Nullable costs an extra byte-per-row null map — a deliberate accept.

**Questions this schema can answer** (have one ready): which stage is the p95
bottleneck; what % of queries end `contradicted` vs `success` vs `web_fallback`;
whether chunk-length distribution drifted after a chunking change; which pages fail
processing repeatedly.

---

# PART 4 — The Offline Path (Ingestion & Processing), Stage by Stage

## 4.0 Stage zero: seeding Confluence with n8n (`n8n/`)
Before the Python system exists, there has to be content. An n8n workflow
(`Wikipedia to Confluence Ingestion.json`) ingests a Wikipedia category tree into
Confluence as a realistic corpus (Alphabet/Google pages — which is why the prompts
mention Alphabet).

Flow: `Manual Trigger → Configuration → Wikipedia categorymembers API → Split Out →
Pages Loop (splitInBatches) → Category Check (ns == 0?) → [recurse into
subcategories | fetch page HTML → HTML Extractor → HTML Cleanup (code) →
Search-Page → Page Exists? → Delete-Page → Create-Page] → Airtable logging`

Design decisions worth knowing (they're stated in `blog-contents/`):
- **Manual trigger, not cron** — this is a one-time population job, not a sync. Don't
  add scheduling to something that shouldn't repeat.
- **All runtime params in one `Configuration` node** — config isolated from logic, so
  the same workflow retargets to another category/space without conditionals.
- **Namespace-based traversal** — `ns == 0` means article, anything else is a
  category and goes back into expansion. Handles arbitrary nesting without
  hard-coded depth.
- **Fetch rendered HTML, not the summary API** — fidelity over convenience. Preserves
  tables, math, nested markup. Accepts more cleanup work downstream.
- **Replace-or-create (delete then create)** — guarantees **idempotency at the
  workflow level**: every successful run converges to the same Confluence state
  regardless of what was there before. The alternative (update-in-place) means
  merge semantics and partial-state bugs.
- **Item-level isolation + per-node error paths** — a malformed page fails locally,
  not globally. Partial success is a valid, recorded outcome.
- **Airtable logs** (`Confluence Ingestion Logs`: pageTitle, success, failureNode,
  message) — execution becomes *data*, not an inference from reading n8n run logs.
  This is the first appearance of the observability theme.
- Custom cleanup: Wikipedia `<math>` / `mwe-math-element` → Confluence
  `<ac:structured-macro ac:name="math">`; relative `/wiki/` links → absolute URLs;
  strip edit-section markers, navboxes, References/See-also/External-links/Notes.

**Why n8n rather than a Python script?** The job is API orchestration with retries,
batching, and branching — exactly what a visual workflow tool is good at, and the
run history/error routing comes free. A Python script would need all of that
hand-built. Reasonable interviewer pushback: "that's untestable and unversionable" —
the honest answer is the JSON is committed, but yes, it has no unit tests.

## 4.1 Trigger: bulk page discovery (`jobs/page_indexer_trigger/main.py`)
```
fetch_page_ids()                       # jobs/common/confluence_pages.py
  → GET /rest/api/content?type=page&spaceKey=...&limit=50&start=N   (paginated)
  → loop until len(results) < limit
for each page_id:
  POST {PAGE_INDEXER_URL} {"page_id": ...}   # up to 3 attempts, 1s sleep between
```
- **Pagination via `start`/`limit`=50** — avoids an unbounded response.
- **Partial-result semantics**: on HTTP failure it returns `page_ids if page_ids else
  None` — i.e. it keeps whatever it already collected. Partial progress beats zero.
- **Two layers of retry**: `urllib3.Retry(total=3, backoff_factor=1,
  status_forcelist=[429,500,502,503,504])` at the HTTP-adapter level *plus* an outer
  3-attempt loop. The adapter handles transport/status retries transparently; the
  outer loop handles "the request completed but the result was unusable."
- **Why `status_forcelist` only contains 429 + 5xx**: those are *transient*. Retrying
  a 400/401/404 is pointless — the request is wrong, not unlucky. This is exactly the
  distinction between retryable and non-retryable errors, and it's a great thing to
  articulate.
- **Why `backoff_factor=1`** → sleeps of 0s, 2s, 4s (urllib3 formula
  `backoff_factor * 2^(retry-1)`). Exponential backoff prevents a retry storm from
  being the thing that keeps the dependency down (the "thundering herd" /
  retry-amplification problem).

## 4.2 `page-indexer`: metadata-only ingestion (`services/page_indexer/main.py`)

```
POST /  {"page_id": "..."}  → 202-ish {"accepted": true}  immediately
                            → BackgroundTasks.add_task(process_page, ...)

process_page(pc, page_id):
  trace_id = uuid4()
  init_state(conn, page_id)                      # state machine row, all 'pending'
  title, created_at = fetch_confluence_page(...)  # GET content/{id}?expand=body.storage
  insert_neon(page_id, title, source_url, created_at, is_stashed=TRUE)
  upsert_pinecone(kb-pages, _id=f"page:{page_id}", page_title=title)
  record_indexing_result(trace_id, page_id, 'success', latency)
```

**Why only metadata here, and not chunking?**
This is the central separation-of-concerns decision in the whole offline path.
Chunking + embedding is slow (seconds to minutes per page) and expensive. The indexer
must stay fast because it also serves **webhooks**, which have delivery timeouts. So
the indexer's job is narrow: *record that this page exists and needs work*
(`is_stashed = TRUE`). The heavy lifting is deferred to a job that runs on its own
schedule.

**Why `BackgroundTasks` and an immediate ACK?** Confluence webhooks (and the trigger
job) must not block on Confluence API latency. Returning `{"accepted": true}` first
and doing work after is the **accept-then-process** pattern. It converts a
synchronous dependency chain into an async one.
*Weakness to volunteer:* `BackgroundTasks` runs **in-process with no durability**. If
the container dies mid-task, that work is silently lost — there is no queue, no
at-least-once delivery, no dead-letter. The `kb_page_ingestion_state` table + the
retry job are the compensating control: state says `pending`, so the retry job picks
it up. That's a hand-rolled reconciliation loop standing in for a real broker
(SQS/Celery/RabbitMQ). Correct answer to "how would you fix this": put page IDs on a
durable queue with visibility timeouts and a DLQ.

### The per-page state machine
```
kb_page_ingestion_state: confluence_status | neon_status | pinecone_status
     pending → success
             → failed  (retryable)
             → fatal   (give up, stop retrying)
```
**Why track each stage separately instead of one overall status?** Because recovery
is *targeted*. If Neon succeeded and Pinecone failed, you only replay Pinecone. A
single status field forces you to redo everything, which is both wasteful and risky.

**Why a `fatal` terminal state?** Without it, a permanently broken page (deleted in
Confluence, malformed, permissions revoked) is retried forever — an infinite
poison-pill loop that burns quota and pollutes metrics. `fatal` is the manual
equivalent of a dead-letter queue. The retry job's `WHERE` clause explicitly excludes
fatal rows.

### SQL-injection defence worth pointing out
`update_state` interpolates a column name into the SQL string (`SET {field} = %s`),
which is unavoidable because **identifiers cannot be parameterised** in SQL — only
values can. The code guards it with an explicit allow-list:
```python
ALLOWED_FIELDS = {"confluence_status", "neon_status", "pinecone_status"}
if field not in ALLOWED_FIELDS: return False
```
That is the correct pattern, and it's a strong thing to mention unprompted. Similarly
`fetch_chunks_from_neon` builds `IN (%s,%s,...)` placeholders dynamically but still
passes values as parameters — the *structure* is dynamic, the *data* never is.

### The webhook handlers — freshness without reprocessing
| Webhook | Handler | Action |
|---|---|---|
| `page-created` | `process_page` | full metadata ingest |
| `page-updated` | `page_updated` + `page_title_updated` | set `is_stashed = TRUE`; if title actually changed, update Postgres + re-upsert `kb-pages` |
| `page-deleted` | `page_removed` | `is_active = FALSE` on all chunks + images of that page |
| `page-restored` | `page_restored` | `is_active = TRUE` again |

`page_title_updated` does a **read-before-write**: fetch the current title, compare,
and return early if unchanged. Saves a Postgres write and a Pinecone upsert on the
very common case of an edit that doesn't touch the title. Small, cheap, and exactly
the kind of detail that reads as production experience.

Registered via `rest/webhooks/1.0/webhook` with ngrok exposing localhost
(`command_refs.txt`, `confluence_webhooks_guide.txt`).

## 4.3 `indexer_retry`: the reconciliation loop (`jobs/indexer_retry/main.py`)
```sql
SELECT page_id, neon_status, pinecone_status
FROM kb_page_ingestion_state
WHERE (neon_status != 'success' OR pinecone_status != 'success')
  AND confluence_status != 'fatal'
  AND neon_status != 'fatal' AND pinecone_status != 'fatal'
```
Then, per row: always re-fetch from Confluence first (`/retry/confluence`) because
title + created_at are needed as *inputs* to the other two stages; if that fails →
`set_all_fatal(page_id, "confluence retry failed")`. Otherwise replay only the
non-success stages.

**Why re-fetch Confluence every time, even when `confluence_status = success`?** The
retry endpoints are stateless — they need `title`/`created_at` passed in, and those
weren't persisted anywhere recoverable. So the fetch is the cheapest way to
re-materialise the inputs. A better design would persist stage *outputs* alongside
stage *status* so replay needs no re-fetch.

**Why one `fatal` for all three stages on a Confluence failure?** Because Confluence
is the upstream dependency — if the page can't be read, nothing downstream is
possible. Failing the whole row forward is correct, not lazy.

## 4.4 Content extraction (`jobs/page_processor/helpers/extractors/`)

Content is pulled from Confluence **storage format** (`?expand=body.storage`) — the
raw persisted XHTML, *not* the rendered view. Why: storage format is what the user
actually saved, it's stable across Confluence UI changes, and it avoids
render-time/macro-expansion variance. The blog calls this "treating Confluence as the
source of truth" rather than reusing intermediate ingestion artefacts.

The HTML is then decomposed along **three different axes**, because the three content
types need three different treatments:

### (a) Images — `extract_images(html)`
- Normalise `src` (`//cdn/..` → `https://cdn/..`; relative → `https://`).
- Caption fallback chain (5 levels, described in 3.2).
- **Section path**: walk `find_previous(h1..h6)` upward, keeping only headings with a
  *strictly smaller* level than the last one kept (`if level < last_level`). That
  reconstructs the true breadcrumb `H1 > H2 > H3` and skips sibling headings at the
  same level. This same algorithm appears in all three extractors.
- **Then it destructively removes itself**: `caption_node.extract()` and
  `figure.decompose()` / `img.decompose()`.

### (b) Tables — `extract_tables(html)`
- Skips `sidebar`/`nomobile`/`nowraplinks` classes (Wikipedia infobox noise).
- Handles multi-row `<thead>` by **padding rows to equal width and concatenating
  column headers vertically** — so a two-row header becomes one combined header
  string per column.
- Emits one "fact" **per cell**, formatted as:
  ```
  <table caption>
  <section > path>
  <column header>: <cell value>
  ```
- Handles the header-less `<th>`/`<td>` key-value table shape separately.

**Why explode a table into per-cell facts instead of embedding the table as
markdown?** This is one of the best design decisions in the project and a great
answer to "how do you handle tables in RAG?":
- Embedding a whole table produces one vector that is the *average* of many unrelated
  facts — it matches nothing well. This is the "semantic dilution" problem.
- A query like "where is Alphabet headquartered?" needs to match exactly one cell.
  Per-cell facts give that cell its own vector.
- Re-injecting the caption + section path + column header into every fact makes each
  chunk **self-contained**. "Mountain View, California" is useless alone;
  "Alphabet Inc. > Infobox > Headquarters: Mountain View, California" is retrievable
  and answerable. This is contextual enrichment / context-prepending.

**Costs:** chunk count explodes (a 10×8 table → 80 chunks), relationships *between*
cells in a row are lost, and aggregate questions ("how many subsidiaries are listed?")
become unanswerable. For a mitigation, you'd add row-level chunks alongside cell-level.

### (c) Prose — `html_to_markdown(html)`
- `decompose()` `script`/`style`/`noscript` **and** `table`/`img` — because those were
  already handled by the two extractors above. **No content is processed twice.**
- Convert to Markdown via `markdownify` with `heading_style="ATX"`, `strip=["a"]`.
- Drop blocks that are only heading punctuation.

**Why Markdown as the intermediate representation rather than plain text?** Markdown
preserves structure (`##` headings, `-` lists, `|` tables) in a form that (1) LLMs are
heavily trained on and parse naturally, and (2) splitters can use as boundaries.
`soup.get_text()` would flatten everything into an undifferentiated wall — you'd lose
the paragraph boundaries that the splitter depends on. Note the blog shows an *earlier*
version that did exactly `get_text()` + `re.sub(r"\s+"," ")`; the move to markdownify
is the documented evolution.

## 4.5 Chunking — the two-stage splitter (the most technical part of the offline path)

```python
structural = SentenceSplitter(
    chunk_size=300, chunk_overlap=50, paragraph_separator="\n\n"
).get_nodes_from_documents([Document(text=markdown)])

semantic = SemanticSplitterNodeParser(
    embed_model=HFEmbedding(),      # all-MiniLM-L6-v2, normalized
    buffer_size=1,
    breakpoint_percentile_threshold=90,
)
for node in structural:
    semantic_nodes.extend(semantic.get_nodes_from_documents([Document(text=node.text)]))
```

### The chunking spectrum — know all four options
| Strategy | How | Pro | Con |
|---|---|---|---|
| **Fixed-size** | every N chars/tokens | trivial, uniform | slices mid-sentence, destroys meaning |
| **Recursive character** | split on `\n\n`, then `\n`, then `.`, then chars | respects structure, fast, no model | boundaries are syntactic, not semantic |
| **Semantic** | embed each sentence, cut where consecutive-sentence cosine distance spikes | boundaries follow *topic* shifts | O(sentences) embedding calls; can run away on long docs |
| **Agentic / LLM-based** | ask an LLM where to cut | best quality | slow, expensive, non-deterministic |

### Why this project chains structural → semantic (the actual insight)
Running `SemanticSplitterNodeParser` directly on a whole 10,000-word page has two
problems: it's O(n) embedding calls over the entire document, and percentile-based
breakpoints computed over a huge distance distribution produce unpredictable chunk
sizes (the 90th percentile of a long doc is a very different cut than of a short one).

So: **`SentenceSplitter` first imposes a hard size ceiling** (~300 tokens, 50
overlap) using cheap syntactic rules. **Then the semantic splitter refines inside each
bounded window.** You get semantic boundaries *and* a predictable size distribution,
and the embedding cost is bounded per window rather than per document.

> Phrase it as: "structural splitting for bounded cost and predictable size; semantic
> splitting for coherent boundaries. Cheap filter first, expensive refinement second —
> the same cascade idea as retrieve-then-rerank in the online path."

### Parameters, each with a reason
- `chunk_size=300` tokens — big enough to hold a complete idea, small enough that the
  embedding isn't diluted by multiple topics, and small enough that ~10 chunks fit in
  the LLM context budget.
- `chunk_overlap=50` (~17%) — a sentence straddling a boundary appears in both chunks,
  so a fact split across the cut is still retrievable from at least one side. Cost is
  ~17% storage duplication and some near-duplicate retrieval hits.
- `buffer_size=1` — compare each sentence against its immediate neighbour. Larger
  buffers smooth over short topical digressions; 1 is the most sensitive setting.
- `breakpoint_percentile_threshold=90` — cut at the top 10% of distance spikes. Higher
  → fewer, larger chunks; lower → more, smaller. It's percentile-based rather than an
  absolute cosine threshold so it **adapts per document** instead of needing a
  magic constant that works for all writing styles.
- `len(text) <= 30` filter — drops fragments too short to be meaningful; they'd pollute
  recall with high-similarity noise.

### Section-path backfill — and its honest weakness
```python
el = soup.find(string=lambda x: x and text[:50] in x)
# then walk find_previous(h1..h6) upward from el
```
After semantic chunking, the chunk has lost its position in the document, so the code
locates it again by **substring-matching the chunk's first 50 characters back into the
original soup** and walking up to the headings.

Be ready to criticise this yourself — an interviewer will:
- It's **O(chunks × nodes)** — a full tree scan per chunk.
- It's **fragile**: markdownify changed the text (entities decoded, tags stripped), so
  the markdown-derived substring often won't match the HTML node text, and
  `section_path` silently becomes `""`.
- Identical opening text in two places matches the wrong one.

**The right fix:** carry the section path *forward* as metadata during splitting
(attach the heading context to each `Document`/node before splitting, so chunks inherit
it) instead of recovering it afterwards. That is a strong, concrete improvement to
offer.

## 4.6 Writing to the stores (`jobs/page_processor/helpers/utils.py`)

```
upsert_neon_chunks   → INSERT ... ON CONFLICT (chunk_hash) DO NOTHING
upsert_neon_images   → INSERT ... ON CONFLICT (image_hash) DO NOTHING
upsert_pinecone_chunks → index.upsert_records(batches of 90, _id=sha256(text))
upsert_pinecone_images → index.upsert_records(batches of 90, _id=sha256(src+caption))
mark_page_unstashed  → UPDATE kb_pages SET is_stashed = FALSE
```
- **`ON CONFLICT DO NOTHING`** = idempotent upsert. Combined with content hashing,
  re-running is free. (`DO UPDATE` would be needed if you wanted to refresh
  `section_path` on unchanged text — currently you can't.)
- **Batches of 90** — stays under Pinecone's 96-record-per-upsert limit with headroom.
- **Order matters**: `mark_page_unstashed` runs **only if all four writes succeeded**.
  If anything failed, the page stays stashed and the next cron run retries it. The
  stash flag *is* the checkpoint. This is at-least-once processing with an idempotent
  sink — the standard way to get effectively-once semantics without distributed
  transactions.

### The per-step retry loop (in both `one_off` and `cron`)
```python
step_results = {"neon_chunks": False, "pinecone_chunks": False,
                "neon_images": False, "pinecone_images": False}
for _ in range(RETRIES):            # 3
    if not step_results["neon_images"]:   step_results[...] = upsert_neon_images(...)
    if not step_results["neon_chunks"]:   ...
    if not step_results["pinecone_chunks"]: ...
    if not step_results["pinecone_images"]: ...
    if all(step_results.values()): break
    time.sleep(RETRY_SLEEP)          # 1.0s
```
**Why a dict of booleans and `if not already_done`?** So retries are **surgical** — a
successful Neon write is never redone just because Pinecone flaked. This is the same
"track each stage separately" principle as `kb_page_ingestion_state`, applied at the
job level. Without it you'd re-upsert everything on every attempt: more cost, more
load on the dependency that is already failing.

## 4.7 One-off vs cron: the two processing modes

They share ~90% of their code (extraction, chunking, retry loop, metrics). The
difference is **which pages they select** and **what they do with existing rows**.

| | `one_off` | `cron` |
|---|---|---|
| Page selection | `fetch_page_ids()` — **all** pages from Confluence | `SELECT page_id FROM kb_pages WHERE is_stashed = TRUE` |
| Write mode | insert everything (ON CONFLICT DO NOTHING) | hash set-diff: insert adds, deactivate removes |
| Deactivation | none | `is_active = FALSE` for `old − new` |
| Connection | one connection for the whole run | **a fresh connection per page** |
| Use | initial bootstrap | steady state |

### The delta algorithm (the heart of `cron`)
```python
old_chunk_hashes = set(SELECT chunk_hash FROM kb_chunks WHERE page_id=? AND is_active)
new_chunk_items  = [(sha256(c), c, path) for each new chunk]
new_chunk_hashes = {h for h,_,_ in new_chunk_items}

add_chunks            = [items whose hash ∉ old]       # insert these
remove_chunk_hashes   = list(old − new)                # deactivate these
# (old ∩ new) is untouched — no write, no re-embed
```
**Why this matters economically:** a typo fix on a 100-chunk page changes exactly one
hash. You insert 1 chunk and deactivate 1 chunk. You do **not** re-embed 100 chunks.
Re-embedding everything on every edit is the single biggest cost sink in naive RAG
pipelines, and this eliminates it.

### Why per-page connections in cron but not one-off
Cron is long-running and unattended. Holding one Postgres connection across hundreds
of pages risks hitting Neon's idle timeout (Neon is serverless and aggressively
scales to zero / drops idle connections) and means one broken connection kills the
whole run. A fresh connection per page bounds the blast radius to one page at the cost
of per-page connection setup. One-off is run interactively and accepts the simpler
model. *If asked to improve:* use a connection pool (`psycopg2.pool` /
`SQLAlchemy QueuePool` / PgBouncer) to get both.

## 4.8 The freshness strategy — webhooks + cron (this is the project's best idea)

The blog `Decoupling Change Detection from Processing.md` lays out the reasoning
explicitly, and it's the most interview-ready piece of thinking in the repo.

**Option A — webhooks do everything.** Page changes → webhook fires → re-chunk and
re-embed immediately.
*Why it fails:* **edits are not atomic.** One logical update generates dozens of
low-level events (word edits, formatting, metadata). Each would trigger a full
chunk+embed cycle. Execution load scales with *typing behaviour*, not with meaningful
change. Costs become unpredictable; the same page is processed 30 times in 5 minutes.
"Reactive but not intelligent."

**Option B — cron does everything.** Periodically rescan and reprocess the whole
corpus.
*Why it fails:* unchanged pages are reprocessed forever. Cost is O(corpus) per run,
not O(change). As the corpus grows you must slow the cadence, so freshness degrades
exactly as the system becomes more valuable. "Stability by brute force."

**The chosen design — separate *detection* from *processing*:**
```
Webhook  ──► answers ONE question: "which page changed?"
             sets is_stashed = TRUE.  No chunking. No embedding. O(1) work.
             Multiple events for one edit CONVERGE on the same single fact (idempotent).

Cron     ──► decides WHEN. Queries only is_stashed = TRUE pages.
             Hash-diffs, processes the delta, then clears is_stashed.
```
Properties this buys you:
- **Natural debouncing.** 30 webhook events → one boolean set to TRUE → one
  reprocessing run. The flag is idempotent, so event storms are absorbed for free.
- **Load is proportional to *distinct changed pages*, not to edit volume or corpus
  size.** Both naive extremes get this wrong in opposite directions.
- **Temporal control.** You choose the cadence; the trade-off between freshness and
  cost becomes a tunable dial instead of an emergent property.
- **Bounded webhook latency.** Confluence's delivery timeout is never at risk.
- **A closed loop with explicit state:** webhook marks → cron processes → state
  cleared → page is not reconsidered until the next real change.

> The soundbite: **"Webhooks provide signal without noise; cron provides structure
> without waste."** And: *the webhook answers "what changed?", the cron answers "when
> do we pay for it?"*

This is a general pattern worth naming: **event-driven detection with deferred,
batched, idempotent processing** — the same shape as a materialised-view refresh queue,
Kafka log compaction, or a CDC pipeline with a staging table.

## 4.9 Metrics emitted per page
`record_processing_result` writes text_chunk_count, table_chunk_count, image_count,
avg/min/max chunk length, total_embeddings, latency, final_status.

**Why chunk-length distribution specifically?** It's the leading indicator for
retrieval quality. If avg chunk length suddenly drops, your extractor broke (probably
an HTML change upstream) and retrieval will degrade *days before anyone notices bad
answers*. Tracking the distribution turns a silent quality regression into a visible
metric movement. That answer alone signals production maturity.

---

# PART 5 — The Online Path (Query), Stage by Stage

Two endpoints exist. **The frontend only uses `/query/stream`.**

| | `POST /query` | `POST /query/stream` |
|---|---|---|
| Orchestration | LangGraph `StateGraph` | hand-written imperative generator |
| Transport | single JSON response | SSE (`text/event-stream`) |
| Observability to user | none | per-step `step`/`branch`/`steps`/`result`/`done` events |
| Used by UI | no | **yes** |

Be upfront about this duplication — see 10.1.

## 5.1 Request shape and the proxy hop
```
Browser → POST /api/query/stream (Next.js route handler)
        → POST http://llm-engine:8000/query/stream
        ← ReadableStream piped straight back
```
**Why proxy through Next.js instead of calling the Python service directly?**
1. **The LLM engine is never exposed to the internet.** It lives on the Docker network
   and has no auth of its own; `INTERNAL_LLM_ENGINE_URL` is a service-internal name.
   The browser can't reach it, by design.
2. **No CORS.** Same-origin requests from the browser.
3. **A seam for cross-cutting concerns** — auth, rate limiting, request logging, and
   tenant resolution all belong at the edge, and this is where you'd add them.
4. `export const dynamic = "force-dynamic"` disables Next's route caching — mandatory
   for a streaming endpoint.
5. The route returns `new Response(backendRes.body, ...)` — it pipes the stream
   without buffering, so first-byte latency is preserved.

Input validation at the boundary: `query: str = Field(..., max_length=1000)` and
`history: List[Dict[str,str]] = Field(default_factory=list, max_items=20)`. Pydantic
rejects oversized input before any model runs — cheap DoS protection and a bound on
prompt-injection surface and token cost. (Commit history literally says "fix dos and
threading".)

## 5.2 Stage 1 — Intent routing
```python
GROQ_INTENT_MODEL, temperature=0, max_tokens=5
prompt: "'knowledge': questions ONLY about Alphabet, Google, and their funded
          companies, products, or updates.
          'general': greetings, identity questions, small talk, or questions about
          completely unrelated topics and other companies (like Amazon, Apple).
          Respond with exactly one word."
intent = re.sub(r"[^a-z]", "", response.strip().lower())
if intent not in ("knowledge", "general"): return None   # → defaults to "general"
```
**Why route at all?** Running full retrieval on "hi" is ~2 seconds and several API
calls to produce a greeting, and — worse — it will retrieve *something* (vector search
always returns its top-k) and the LLM may try to answer from irrelevant context.
Routing makes cheap requests cheap and keeps the KB path for KB questions.

**Why a separate, smaller model with `temperature=0` and `max_tokens=5`?**
Classification is a deterministic task: you want the same label every time
(temperature 0) and you only need one token, so a small fast model minimises the
latency this adds to *every* request. Using the big generation model here would tax
every query for no quality gain.

**Why defensive parsing (`re.sub`, allow-list, `None` → "general")?** LLMs don't
honour output contracts reliably — you get `"knowledge."`, `"**general**"`,
`'"knowledge"'`. Stripping non-letters and validating against a closed set converts a
soft contract into a hard one. And the failure default is `general`, the *cheaper and
safer* branch: a misroute to general produces a polite non-answer, whereas a misroute
to knowledge burns the full pipeline. **Always default to the cheap, safe branch.**

**Alternatives you should name:**
- *Zero-shot classifier* (e.g. `bart-large-mnli`) — cheaper and faster, but another
  model to host; the LLM is already in the path.
- *Embedding similarity to intent centroids* — fastest, but needs labelled examples
  and degrades on paraphrase.
- *Keyword rules* — free and brittle.
- *Retrieve-first-and-check-scores* (skip the classifier; if top score is low, treat
  as general) — saves a call and is arguably more robust; the threshold gate in 5.4
  already does something close. A legitimate "what would you change" answer.

**The real weakness:** the intent prompt hard-codes the domain ("Alphabet/Google",
"not Amazon/Apple"). The router is coupled to the corpus. For a reusable product this
should be derived from the indexed corpus or configured per tenant, not written into
a prompt string. (Commit `c9eac83` is literally "Refine intent classification and
general response prompts" — prompt-tuning this was iterative, which is honest and
worth saying.)

## 5.3 Stage 2 — Retrieval (recall)

```python
TOP_K_CHUNKS = 20;  TOP_K_PAGES = 20;  FINAL_TOP_K = 10
W_CHUNK = 0.7;      W_PAGE = 0.3;      ALPHA = 0.7;   THRESHOLD = 0.6

chunk_scores = search_with_text(kb-chunks, query, 20)     # {_id: score}
page_scores  = search_with_text(kb-pages,  query, 20) or {}
chunk_meta   = fetch_chunks_from_neon(conn, list(chunk_scores))   # hydrate

fused = [{..., "fused_score": 0.7*chunk_score + 0.3*page_score} for each chunk]
top_fused = sorted(fused, -fused_score)[:15]
```

### How the embedding actually happens — a detail you must get right
```python
index.search(namespace="default", query={"inputs": {"text": text}, "top_k": k})
index.upsert_records(namespace="default", records=[{"_id": h, "raw_chunk": text}])
```
These are Pinecone's **integrated-inference** APIs. You send **raw text**; Pinecone
embeds it **server-side** with the model attached to the index, at both write and
query time.

**Why that's a significant choice:**
- The query path has **no embedding model in it at all** — no torch in `llm-engine`,
  smaller image, no cold start, less memory.
- **Train/serve symmetry is guaranteed.** The #1 silent RAG bug is embedding documents
  with model A and queries with model B (or different versions / different
  normalisation). Pinecone owning both ends makes the mismatch structurally
  impossible.
- You give up model choice and portability, and you're locked into Pinecone's
  catalogue and pricing.

**Clarify this when asked "what embedding model do you use?":** the local
`all-MiniLM-L6-v2` (`hf_embedder.py`, 384-dim, `normalize_embeddings=True`) is used
**only to compute semantic-chunking breakpoints offline**. It never produces a stored
or queried vector. Retrieval embeddings are Pinecone's. Getting this distinction right
is a credibility marker; getting it wrong looks like you didn't understand your own
system.

(`normalize_embeddings=True` → unit vectors → dot product equals cosine similarity,
which is what the semantic splitter's distance metric wants.)

### What "hybrid" means *here* — be precise
Most people hear "hybrid retrieval" and think **dense + sparse** (embeddings + BM25 /
SPLADE), fused with Reciprocal Rank Fusion. **That is not what this does.**

This is **dense chunk-level + dense page-level fusion**: two semantic searches at two
granularities, combined by weighted sum. Say it that way, and then show you know the
other kind:

> "We fuse chunk-level and page-level dense signals, not dense and lexical. The gap is
> real: we have no keyword channel, so exact-match queries — error codes, SKUs,
> specific identifiers, rare proper nouns — are weak, because dense embeddings are bad
> at exact token matching. Adding BM25 (Pinecone sparse vectors, or Postgres
> `tsvector`) combined via RRF is the first retrieval improvement I'd make."

### Why fuse page-level signal in at all?
Chunk similarity is **local**: a chunk can look relevant in isolation while living on
a completely unrelated page. Page-title similarity is **global** topical context.
Mixing them lets a strong document-level signal rescue a moderately-scoring chunk, and
penalises a chunk that matched on an incidental phrase. It's a crude version of
hierarchical / parent-document retrieval.

### Why weighted sum (0.7/0.3) rather than RRF?
- Weighted sum keeps **score magnitudes**, which matters because a threshold is applied
  later. RRF throws magnitudes away and uses only ranks (`1/(k+rank)`), so you can't
  threshold on "how relevant is this, absolutely."
- Weighted sum requires the two score scales to be **comparable**. Here both are
  Pinecone cosine scores, so that holds. If you were fusing cosine with BM25 (which is
  unbounded), you'd *have* to use RRF or normalise first. Knowing *when* weighted sum
  is valid and when it isn't is the real answer.
- 0.7/0.3 says: chunk-level is the primary signal, page-level is a modifier. Tuned by
  hand, not learned — be honest, and say the right fix is a small labelled eval set.

### ⚠️ A real bug in the fusion — find it before your interviewer does
`kb-pages` stores IDs as `f"page:{page_id}"`, so `page_scores` keys look like
`"page:12345"`. But the fusion does:
```python
page_score = page_scores.get(str(meta["page_id"]), 0.0)   # looks up "12345"
```
The key never matches, so **`page_score` is always 0.0** and `fused_score` is just
`0.7 × chunk_score`. The page-level channel contributes nothing today. The fix is one
line (`f"page:{meta['page_id']}"`).

Owning this is strictly better than being caught by it. It also demonstrates the
deeper lesson: *silent degradation*. Nothing errors, nothing logs — retrieval just
gets quietly worse. Volunteer the mitigation: a retrieval eval set in CI, plus
asserting the overlap between `page_scores` keys and chunk `page_id`s.

### Why hydrate from Postgres instead of storing text in Pinecone metadata?
Covered in 2.2 — the decisive reason is `is_active`. A boolean that flips on every
delete/restore must live where a cheap `UPDATE` can reach it.

## 5.4 Stage 3 — Reranking (precision)

```python
rerank_scores = call_reranker(query, [f["text"] for f in top_fused])   # 15 texts
ALPHA = 0.7
final_score = 0.7 * rerank_score + 0.3 * fused_score
top_chunks  = sorted(desc)[:10]
filtered    = [c for c in top_chunks if c["final_score"] >= 0.6]
if not filtered: → retrieval failed → web fallback
```
Service side: `CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")`, pairs
`(query, text[:2000])`, run in a `ThreadPoolExecutor(max_workers=4)` via
`loop.run_in_executor`, input capped at 50 texts by Pydantic.

### Bi-encoder vs cross-encoder — the core concept, explain it like this
**Bi-encoder (what Pinecone does):** query and document are embedded *independently*
into one vector each; similarity is a dot product.
→ Documents can be embedded **once, offline**, and search is an ANN lookup over
millions of vectors in milliseconds. But the model never sees the query and the
document together, so it can't reason about their interaction. It compresses a whole
document into ~768 floats and hopes the relevant part survived.

**Cross-encoder (the reranker):** query and document are concatenated into
`[CLS] query [SEP] document [SEP]` and pushed through the transformer **together**,
emitting a single relevance score.
→ Full cross-attention between every query token and every document token. Far more
accurate (typically +10–20% NDCG@10 over a bi-encoder). But you cannot precompute
anything: **one forward pass per (query, document) pair**. Scoring a million documents
per query is impossible.

### Therefore: the cascade / funnel
```
millions of chunks ──bi-encoder ANN──► 20 ──fusion──► 15 ──cross-encoder──► 10 ──threshold──► ≤10
     cheap, high recall, low precision        expensive, high precision
```
This is **the** canonical two-stage retrieval architecture (the same shape as ad
ranking, search ranking, recommendation funnels). Say it as: *"recall first, precision
second — use the cheap model to shrink the candidate set, then spend the expensive
model only on survivors."* Note it's the same cascade idea as structural→semantic
chunking in 4.5. Recognising a repeated pattern across your own system reads very well.

- **`MiniLM-L-6-v2`**: 6 layers, ~22M params, distilled. Trained on MS MARCO (real
  Bing queries + human relevance labels) — so it's trained on exactly this task.
  Chosen because it runs on CPU in tens of milliseconds for 15 pairs. A larger
  cross-encoder (`L-12`, or a monoT5/bge-reranker) would score better and cost more.
- **`text[:2000]`**: cross-encoders truncate at 512 tokens anyway; capping chars
  bounds tokenisation work and prevents one huge chunk from dominating latency.
- **Why `run_in_executor`?** PyTorch inference is **synchronous, CPU-bound, and
  GIL-releasing**. Called directly inside an `async def` it would block FastAPI's event
  loop, serialising all concurrent requests behind one inference. Offloading to a
  thread pool keeps the loop free. `max_workers=4` bounds CPU contention — more threads
  on a few cores just thrash. This is the single most important FastAPI
  performance/correctness idea in the repo, and it's in both model services.

### Why keep 30% of the fused score (`ALPHA = 0.7`)?
Pure reranker ordering discards the retrieval signal entirely. Cross-encoders are
strong but have their own failure modes (they over-reward lexical overlap on short
texts). Blending keeps a vote from the bi-encoder, so a chunk needs to look good to
*both* models to rank top. It's a cheap ensemble.

### ⚠️ Known scale problem — know this one
`CrossEncoder.predict` for `ms-marco-MiniLM-L-6-v2` returns **unbounded logits**
(roughly −11…+11), not probabilities in [0,1]. The code then computes
`0.7 * logit + 0.3 * cosine_in_[0,1]` and compares to `THRESHOLD = 0.6`.

So the two terms are on **incommensurable scales**, and the 0.6 gate is effectively
"was the cross-encoder logit meaningfully positive." It *works* as a coarse filter —
irrelevant pairs get strongly negative logits, so they're reliably excluded — but it's
not principled, and the threshold isn't interpretable.

**The fix:** apply a sigmoid to the logit (`activation_fn=torch.nn.Sigmoid()` on the
CrossEncoder, or sigmoid in the client) so both terms live in [0,1], *then* the
weighted sum and a 0.6 threshold mean something. Being able to say this precisely is
a strong signal of depth. (Commit `a5806c7` "add pre reranked threshold" shows this
area was iterated on — useful context: thresholding was tuned empirically.)

### Why threshold at all, instead of always passing the top-10?
**Because the correct answer to some questions is "I don't know."** Vector search
*always* returns k results — there is no empty result set. Without an absolute
relevance floor, a question the KB cannot answer still produces 10 chunks of
irrelevant context, and the LLM will dutifully synthesise something from them. The
threshold is what converts "always answers" into "answers when it has grounds to," and
it's what triggers the web fallback instead of a hallucination.

## 5.5 Stage 4 — Generation, with images in parallel

```python
with ThreadPoolExecutor() as executor:
    llm_future   = executor.submit(call_groq_llm, query, context, history)
    image_future = executor.submit(get_images, images_index, query, context)
    answer = llm_future.result()
    images = image_future.result()
```
**Why parallel?** Both are independent network-bound calls (~1–2s for Groq, ~200ms for
Pinecone + Postgres). Serially that's the sum; in parallel it's the max. Pure latency
win with no added complexity, because there is no data dependency between them. The
general principle: *fan out independent I/O, join at the end.*

### The generation prompt and why each clause exists
```
"You are an internal company knowledge assistant for Alphabet/Google.
 Answer only using the provided context. Be clear and concise.
 If the answer is not explicitly in the context, reply:
 'I couldn't find that in the knowledge base.'
 After your answer, add a natural conversational closing ... (a) offer to dive
 deeper, or (b) one relevant follow-up question ... Never just drop a question
 abruptly."
```
- *"Answer only using the provided context"* — **grounding constraint**, the primary
  hallucination control. It instructs the model to behave as a reader, not a
  knowledge source.
- *An explicit refusal string* — gives the model a **licensed way to fail**. Without a
  sanctioned escape hatch, models fill the gap with plausible invention. It's also a
  machine-detectable signal downstream.
- *`temperature=0.2`* — low, because factual grounded answering wants determinism and
  reproducibility; not 0.0 because some fluency is wanted in the conversational
  closing. Contrast the general-reply branch at **0.7** (chit-chat wants variety).
  *Knowing that temperature should differ per task is the point.*
- *`max_tokens=800`* — bounds cost and latency, and keeps answers inside the
  Guardrails 4000-char ceiling.
- *The conversational-closing instruction* — pure product/UX: a bare fact feels like a
  database; a follow-up offer feels like an assistant and drives engagement.

**History handling:** `messages = [system] + history + [user(context + query)]`. The
frontend rebuilds history from completed blocks (`buildHistory()` in `page.tsx`), so
the server is **stateless** — no session store, trivially horizontally scalable.
*Cost:* every turn resends the full history (tokens grow quadratically over a long
conversation), the client can tamper with history, and there's no server-side
truncation beyond `max_items=20`. Also note the retrieval query is the **raw user
turn** — there is no query rewriting, so a follow-up like *"and who founded it?"*
retrieves poorly because the pronoun has no referent in the embedding. **Adding
history-aware query rewriting (condense the conversation into a standalone question
before retrieval) is the highest-value fix to the online path** — have this ready.

### Context assembly — the active/inactive labelling trick
```python
f"[Page ID: {page_id}]\nThis is an ACTIVE chunk. It belongs to the section
 '{section}'. The content is:\n{text}"
# or "This is an INACTIVE chunk. ..." when is_active is False
```
Each chunk is wrapped with its page ID, its section path, and an **ACTIVE / INACTIVE
marker**, joined by `\n\n`.

**Why include inactive (deleted/superseded) chunks at all?** This is the most
*unusual* decision in the project and makes a great discussion. The reasoning: a
deleted chunk may still be the best available evidence, and historical content can be
legitimately useful ("what *used* to be the policy?"). Rather than hard-filter it, the
system surfaces it and **delegates the judgement to the LLM via a label**.
The structured context also gives the LLM a natural way to cite (`[Page ID: ...]`).

**Why that's also risky — say this too:** the prompt never actually tells the model
what ACTIVE/INACTIVE *means* or how to weigh them. So the label is present but
*unused*; the model will likely treat both equally and may answer from stale content
without warning. **The fix is one prompt line**: "Prefer ACTIVE chunks. Use INACTIVE
content only if nothing active answers the question, and say explicitly that it may be
outdated." Alternatively, filter inactive chunks in SQL and only fall back to them
when nothing active passes threshold. This is a perfect "what would you improve"
answer: tiny change, meaningful correctness gain.

## 5.6 Stage 5 — Validation (the trust layer)

Two independent checks, both *after* generation.

### (a) NLI contradiction detection
```
POST nli-service /nli  {premise: context, hypothesis: answer}
model: MoritzLaurer/deberta-v3-base-mnli
tokenizer(premise, hypothesis, truncation=True, max_length=512)
softmax(logits) → {"contradiction": p0, "neutral": p1, "entailment": p2}
if contradiction >= 0.7 → return "LLM response contradicted the knowledge base."
```

**What NLI is.** Natural Language Inference: given a *premise* and a *hypothesis*,
classify their relationship as **entailment** (premise supports it), **contradiction**
(premise refutes it), or **neutral** (unrelated / not determinable). MNLI is the
standard training corpus (~433k human-annotated pairs).

**Why use it here.** By setting *premise = retrieved context* and *hypothesis =
generated answer*, the question becomes: **"does the knowledge base actually support
what the model just said?"** That is an automated, model-based **groundedness /
faithfulness check** — the last line of defence against hallucination, applied to the
output rather than trusted to the prompt.

**Why DeBERTa-v3 specifically.** DeBERTa improves on BERT/RoBERTa with
*disentangled attention* (content and position attended separately) and an enhanced
mask decoder; `deberta-v3-base-mnli` is a strong, small, well-known MNLI checkpoint
that runs on CPU. Only ~184M params — cheap enough to put in the synchronous request
path.

**Why threshold on *contradiction* ≥ 0.7, not *entailment* ≥ something?**
This is subtle and worth explaining, because it looks like the "wrong" choice:
- Thresholding on **high entailment** would be a *stricter* faithfulness test, but the
  answer here is multi-sentence and contains a conversational closing ("Would you like
  to explore...") that the context cannot possibly entail. Entailment probability
  would be low for *good* answers → mass false rejections.
- Thresholding on **high contradiction** only blocks answers that **actively conflict**
  with the context. It's permissive about unsupported-but-not-contradicted claims, but
  it has a far lower false-positive rate and catches the worst failure mode: the model
  asserting the opposite of the source.
- **0.7** is a deliberately conservative gate: you only block when the model is quite
  sure there's a conflict. A user-facing system that wrongly refuses good answers is
  worse than one that occasionally lets a vague answer through.

> Frame it as an explicit precision/recall trade-off on the *refusal* decision, and
> say you'd tune it against a labelled set and a dashboard of
> `final_status = contradicted` rates. Which is exactly why `contradiction_score`
> is a column in `query_result`.

**⚠️ Weaknesses to volunteer:**
1. **`max_length=512` truncation.** The premise is the *full* concatenated context
   (often several thousand characters). The tokenizer truncates the pair at 512 tokens,
   so **the NLI model only sees roughly the first chunk or two**. A contradiction
   against chunk #7 is invisible. The fix: run NLI **per chunk** against the answer (or
   per answer-sentence × chunk) and aggregate (max contradiction), instead of one
   giant pair. That's the single most important correctness fix in the validation layer.
2. **Document-level, not claim-level.** A single contradiction score for a
   multi-sentence answer is coarse; one wrong sentence inside four right ones may not
   push the aggregate over 0.7. Claim-level decomposition (split the answer into
   atomic claims, verify each) is the state of the art.
3. **Lazy loading in the request path.** `load_model()` caches on a function attribute,
   so the *first* request pays the model-load cost. The Dockerfile pre-downloads
   weights at build time, so it's a disk read not a network fetch — but a startup
   warm-up (or `@app.on_event("startup")`) would remove the cold-start spike entirely.

**Alternatives — know them:**
- **LLM-as-judge** (ask GPT/Claude "is this grounded?") — better reasoning, but adds a
  full LLM call of latency and cost per query, is non-deterministic, and the judge can
  hallucinate too.
- **RAGAS / faithfulness frameworks** — great for *offline* eval, too slow for the
  online path.
- **Citation enforcement** (require span-level citations and verify they exist) —
  strong and cheap, but constrains output format.
- **Second-pass self-critique** — same LLM re-reads its answer. Weak, correlated
  errors.
- **No check at all** — what most demos do, and precisely what separates this project
  from a notebook.

### (b) Guardrails output validation
```python
length_guard = Guard().use(ValidLength(min=20, max=4000))
length_guard.validate(answer).validation_passed   # else "Invalid LLM output."
```
Catches degenerate output: empty strings, a stray token, a runaway repetition loop.
**Why a framework for something `len()` could do?** Because `Guard` is an *extension
point*. Guardrails Hub has validators for toxicity, PII, competitor mentions, JSON
schema, profanity, prompt-injection. Adopting the framework now means adding
`ToxicLanguage` later is one line rather than a refactor. Honest assessment: today it's
over-engineered for a length check (it adds a build-time `guardrails configure`, a
remote-inferencing token, and a hub install to the Dockerfile) — but the architectural
seam is the point.

**BuildKit secret handling, worth noting:**
```dockerfile
RUN --mount=type=secret,id=guardrails_token \
    guardrails configure --token "$(cat /run/secrets/guardrails_token)"
```
The token is mounted into the build **without being baked into an image layer**.
Compare `ARG`/`ENV`, which persist in image history and leak via `docker history`. This
is the correct way to use a credential at build time.

## 5.7 The fallback chain — degradation design
Trace every path to a user-visible outcome:

| Failure point | Behaviour | `final_status` in ClickHouse |
|---|---|---|
| Postgres unavailable | `"Database unavailable. Please try again later."` | — |
| Intent classification fails | default to `general` branch | — |
| Retrieval fails / nothing ≥ 0.6 | **DuckDuckGo web search → Groq summarises → labelled as web-sourced** | `web_fallback` |
| Generation returns None | web fallback | `web_fallback` |
| Contradiction ≥ 0.7 | `"LLM response contradicted the knowledge base."` | `contradicted` |
| Length validation fails | `"Invalid LLM output."` | `no_answer` |
| Any uncaught exception | generic retry message | — |
| Success | answer + sources + images | `success` |

**The design principle: every failure mode has a defined, user-visible, *recorded*
outcome.** Nothing 500s. Nothing hangs. And each distinct failure gets its own
`final_status` so the dashboard can tell *which* failure is trending.

**The web fallback, and why it's prompted the way it is:**
```python
search = DuckDuckGoSearchRun()
web_results = search.run(query)
prompt = f"""The user asked: {query}
The following information was found on the web: {web_results}
Please provide a concise summary ... State clearly that this information is from
the web and not the internal knowledge base."""
```
- **DuckDuckGo** because it needs no API key and no quota — pragmatic for a project.
  Production would use Brave/Serper/Bing/Tavily for reliability and ToS safety.
- **The LLM summarises the results** rather than dumping links — consistent UX.
- **Explicit provenance labelling** is essential: the user must know this answer is
  *not* from the internal KB and is therefore not authoritative. Mixing trusted and
  untrusted sources without labelling is how a knowledge tool loses credibility.
- **Note the validation layer is skipped for web answers** (`if not web_findings and
  context:`) — correct, because the KB context isn't the premise for a web-derived
  claim. NLI against the wrong premise would produce meaningless scores.
- **Security caveat to volunteer:** web content is untrusted input going into a prompt
  → **indirect prompt-injection** surface. A page could contain "ignore previous
  instructions." Mitigations: wrap the results in clear delimiters, instruct the model
  to treat them as data, and never let that path trigger tools.
- **Also**: when retrieval fails, `sources` and `images` are returned empty — the UI
  shows no citations, which honestly reflects that there are none.

**⚠️ A dead code path:** the generation-stage web fallback checks
`if "not found in knowledge base" in answer.lower()`, but the system prompt tells the
model to say **"I couldn't find that in the knowledge base."** The strings don't match,
so that branch never fires. (The *retrieval-failure* fallback does fire, so users
still get web results — the bug just means a KB-miss that slips past the threshold
produces the refusal text instead of a web search.) The lesson to articulate:
**coupling control flow to natural-language string matching is inherently fragile.**
Better: have the LLM emit a structured signal (a sentinel token, or JSON
`{"found": false}`) and branch on that.

## 5.8 SSE streaming and the "show your thinking" UX

Event protocol (`sse_event(type, data)` → `event: <type>\ndata: <json>\n\n`):
| Event | Payload | Meaning |
|---|---|---|
| `step` | `{id, label, status: pending/in_progress/done/failed, result?}` | one pipeline stage changed state |
| `branch` | `{name: "Knowledge Base" / "General Reply"}` | which route was taken |
| `steps` | `{steps: [{id, label}]}` | register upcoming steps so the UI can show them pending |
| `result` | `{query, answer, sources, images}` | the final payload |
| `done` | `{}` | stream complete |

**Why SSE over the alternatives?**
| Option | Verdict |
|---|---|
| **SSE** | Chosen. One-directional server→client, plain HTTP, works through proxies, auto-reconnect in the browser's `EventSource`, trivial to proxy (`new Response(backendRes.body)`). |
| **WebSocket** | Bidirectional, but you don't need upstream messages; costs a protocol upgrade, stateful connections, and extra infra (sticky sessions, LB config). Over-engineered here. |
| **Polling** | Needs server-side job state + an ID, wastes requests, adds latency floor. |
| **One long POST** | What `/query` does: the user stares at a spinner for 3–6 seconds with no feedback. |

**Why stream *stage* events rather than answer tokens?** Deliberate product thinking.
The honest bottleneck isn't token generation — it's retrieval + rerank + NLI, which all
happen *before* the first token exists. Streaming "Classifying intent → Searching &
reranking → Generating answer → Validating response" makes the wait **legible**: the
user sees progress, sees that the system searched rather than guessed, and
(importantly for an internal-knowledge tool) gains **trust through transparency**. It
also doubles as live debugging — a failed step is visible in the UI.
*Note:* this means the answer itself is **not** token-streamed; the UI fakes
progressive reveal with a 18ms-per-2-chars typewriter in `TerminalBlock.tsx`. Be honest
about that if asked. Real token streaming would require passing `stream=True` to Groq
and relaying deltas.

**Transport details that matter:**
- `X-Accel-Buffering: no` — tells nginx **not** to buffer the response. Without it a
  reverse proxy accumulates the whole stream and delivers it at once, silently
  destroying streaming. A classic production gotcha.
- `Cache-Control: no-cache`, `Connection: keep-alive`.
- `media_type="text/event-stream"`.
- `StreamingResponse(generate())` over a **sync generator** → FastAPI runs it in a
  threadpool, so the blocking `requests`/psycopg2 calls inside don't block the loop.
- `finally: conn.close()` inside the generator — the connection's lifetime is tied to
  the stream, so an aborted client still releases it.

**Client-side parsing (`frontend/lib/api.ts`)** — the details show real streaming
competence:
- `res.body.getReader()` + `TextDecoder` with `{stream: true}` (handles multi-byte
  UTF-8 split across chunk boundaries).
- A **buffer** split on `\n\n`, with `parts.pop()` retained — because a TCP chunk can
  end mid-event. Forgetting this is the #1 SSE client bug.
- **Idle timeout that resets on every chunk** (`clearTimeout` then re-arm, 60s). A
  single fixed 60s timeout would kill a legitimately slow stream; an idle timeout only
  kills a *stalled* one.
- `AbortSignal.any([...])` to merge the caller's signal with the timeout's.
- `malformed event → skip` rather than throwing away the whole stream.
- `if (!gotResult)` after the loop → surfaces "Connection lost" when the stream ended
  without a `result`. **Never leave the UI in a permanent loading state.**

**Frontend state model (`app/page.tsx`)** — `Block[]` where each block is
`{id, prompt, response, isLoading, thinkingSteps, branch, thinkingDurationMs}`.
Worth knowing: `step` events **upsert by `id`** (find-then-merge, else push), so
pending-then-in_progress-then-done updates the same row instead of duplicating; the
whole conversation persists to `localStorage` (survives refresh, zero backend state);
`abortRef` cancels an in-flight request when a new one starts; auto-scroll only engages
when the user is within 150px of the bottom (so it doesn't fight someone reading
scrollback).

---

# PART 6 — Observability

## 6.1 Trace IDs
One `uuid4()` per page/request, generated at the entry point and threaded through every
stage. Every ClickHouse row carries it. Joining on `trace_id` reconstructs the complete
life of one request across tables.

**Why not use the request path / timestamps?** Because stages are concurrent and
multi-service; wall-clock correlation is guesswork at any real volume. An explicit
correlation ID is the only reliable way. (This is distributed tracing's core idea —
OpenTelemetry's `trace_id` is the same concept, with span parentage added. Honest
gap: no spans, no parent/child relationships, no propagation across HTTP calls — the
reranker and NLI services don't receive the trace_id at all, so their internal time
is invisible.)

## 6.2 `safe_record_stage` — telemetry must never break the pipeline
Every metric write is wrapped so a ClickHouse outage can't fail a user request:
```python
def safe_record_stage(...):
    try:   record_stage_execution(...); return True
    except Exception: return False
```
**The principle: observability is strictly best-effort.** Dropping a metric is
acceptable; failing a user's query to record a metric is not. The same `try/except:
pass` wrapping appears around every `record_query_result` call in the streaming path.

## 6.3 Thread-local ClickHouse clients
```python
_client = threading.local()
def _get_client():
    if not hasattr(_client, "conn"):
        _client.conn = clickhouse_connect.get_client(host="clickhouse", port=8123,
                                                     database="analytics")
    return _client.conn
```
**Why thread-local?** The service runs stages in `ThreadPoolExecutor`s and FastAPI
threadpools. `clickhouse_connect` clients are **not thread-safe** — sharing one across
threads causes interleaved writes and protocol corruption. Creating one *per call*
would pay connection setup on every metric write. Thread-local gives one reused client
per thread: safe *and* cheap. (This is the "threading" half of the `08a30a0 fix dos
and threading` commit.) Same pattern as a per-thread DB session in SQLAlchemy.

## 6.4 Metabase
Reads ClickHouse directly; H2 for its own internal metadata (fine for a single-node
demo, would be Postgres in production). Chosen because it's zero-code SQL dashboards —
Grafana would be the alternative (better for time-series alerting, worse for ad-hoc
exploration by non-engineers).

## 6.5 LangSmith + PromptLayer
- `@traceable` decorators on the LangGraph nodes → LangSmith captures inputs/outputs
  per node for prompt debugging and replay.
- `promptlayer_client.openai.OpenAI` wraps the Groq client → every LLM call is logged
  with its prompt, completion, latency, and token usage.

**Why three observability systems?** They answer different questions and that's the
justification: **ClickHouse** = system health and aggregate SLOs (p95 latency, failure
rates); **LangSmith** = graph/trace-level debugging of a single bad answer;
**PromptLayer** = prompt versioning and LLM-call cost/latency analytics. Volunteer the
critique too: three vendors is operational sprawl, and OpenTelemetry → one backend
would be cleaner.

---

# PART 7 — Infrastructure & Build

## 7.1 Docker Compose layout
- **`x-public-dns: &public_dns`** YAML anchor merged into every service via
  `<<: *public_dns`, pinning DNS to 8.8.8.8 / 1.1.1.1. Why: Docker's embedded DNS can
  fail to resolve external hosts in some host configurations (corporate DNS, VPNs);
  containers must reach Confluence, Pinecone, Groq, HuggingFace. The anchor is DRY —
  one definition, seven uses.
- **`profiles: [trigger]`** on the three job containers. Why: `docker compose up` must
  start only the *long-running* services. Jobs are one-shot and must be invoked
  explicitly (`docker compose --profile trigger run --rm page-indexer-trigger`), with
  `restart: "no"` so a completed job isn't resurrected. Without profiles, `up` would
  fire a full re-index every time you started the stack.
- **`restart: unless-stopped`** on services — survives crashes and host reboots, but
  respects a deliberate `docker stop`.
- **Internal vs published ports.** Every Python service listens on **8000 inside** its
  container and only `clickhouse`, `metabase`, and `frontend` publish ports to the
  host. `llm-engine`, `reranker-service`, `nli-service`, `page-indexer` are reachable
  **only on the Docker network**, by service name (`http://nli-service:8000/nli`).
  That's network-level isolation: a service with no auth is never exposed. Service
  discovery is just Docker's embedded DNS — no Consul, no service mesh needed at this
  scale.
  *(Note the README lists host ports 8200/8090/8080/8000 — that reflects a
  `docker run`/dev mapping, not the compose file as committed.)*
- **`ulimits: nofile: 262144`** on ClickHouse — it opens a very large number of files
  (one or more per column per part). The default 1024 causes "too many open files"
  under load. Knowing *why* ClickHouse specifically needs this is a nice detail.
- **`./clickhouse/init:/docker-entrypoint-initdb.d`** — `logs.sql` runs automatically
  on first container start, so the analytics schema is **declarative and
  version-controlled**. (Contrast the Postgres schema, which isn't — see 10.1.)
- **`secrets:`** with a file source for the guardrails token.
- **`depends_on: [llm-engine]`** on frontend — *start* ordering only, not readiness.
  Compose doesn't wait for health unless you add `condition: service_healthy`. Worth
  naming as a gap.
- **Named volumes** `clickhouse-data`, `metabase-data` for persistence across
  `down`/`up`.

## 7.2 Dockerfile patterns

**Build context is the repo root, dockerfile path is per-service.** Why: every image
needs `COPY common ./common`, which must be inside the build context. With
`context: services/llm_engine` you couldn't reach shared code. This is the standard
monorepo Docker pattern. `ENV PYTHONPATH=/app` then makes `from common.utils import ...`
resolve.

**Layer ordering — requirements before source:**
```dockerfile
COPY services/x/requirements.txt .
RUN pip install -r requirements.txt     # cached unless requirements change
COPY services/x ./x                     # changes on every edit
```
Docker caches layers by content. Putting the slow, rarely-changing step first means a
code edit rebuilds in seconds instead of re-installing torch. **This is the single most
important Dockerfile optimisation and you should state it as such.**

**Baking models into the image at build time:**
```dockerfile
RUN python - <<EOF
from transformers import AutoTokenizer, AutoModelForSequenceClassification
AutoTokenizer.from_pretrained("MoritzLaurer/deberta-v3-base-mnli")
AutoModelForSequenceClassification.from_pretrained(...)
EOF
```
Why: without this, the first request after every container start downloads ~500MB from
HuggingFace — slow, and a hard dependency on HF being up and reachable at *runtime*.
Baking it in makes the image bigger but startup fast, deterministic, offline-capable,
and immune to upstream model changes. The processor images do the same for
`all-MiniLM-L6-v2` plus `nltk punkt`/`stopwords` (needed by the sentence splitter),
with `HF_HOME=/models` to fix the cache location.
**The general principle: move work from runtime to build time whenever it's
deterministic.**

**`python:3.11-slim`** everywhere — small base, no build toolchain bloat;
`psycopg2-binary` (prebuilt wheels) avoids needing `gcc`/`libpq-dev`.
**`pip install --no-cache-dir`** — don't ship pip's download cache in the layer.

**Honest gaps:** no multi-stage builds (the torch images are large); no non-root
`USER`; no healthchecks; frontend runs `npm run dev` in the container rather than
`next build && next start` — fine for a demo, wrong for production (dev mode is slow,
unminified, and does on-demand compilation).

## 7.3 Makefile
Thin wrappers (`up`, `down`, `logs`, `indexer-trigger`, `processor-oneoff`,
`indexer-retry`) with `DOCKER_BUILDKIT=1` exported (required for
`--mount=type=secret`). **Why a Makefile:** the real commands are long and easy to get
wrong (`docker compose --profile trigger run --rm page-processor-oneoff`). Encoding
them makes operational actions discoverable and repeatable — self-documenting ops. Also
note `k8s-*` targets are declared in `.PHONY` but not implemented — leftover intent.

---

# PART 8 — Technology Choices: Why X Over Y

| Decision | Chosen | Alternatives | Why this one |
|---|---|---|---|
| Orchestration | **LangGraph** | plain Python, LangChain LCEL chains, CrewAI/AutoGen | Needs **conditional branching** (intent → two different paths) and shared typed state across stages. LCEL chains are linear/DAG-ish and awkward for branching; LangGraph is an explicit state machine with `add_conditional_edges`, so control flow is data you can inspect, visualise and extend. Multi-agent frameworks are for autonomous agents — overkill for a fixed pipeline. *Honest caveat:* this pipeline is nearly linear, so plain functions would be simpler; the graph earns its place when you add loops (self-correcting retrieval, query rewriting, multi-hop). |
| API framework | **FastAPI** | Flask, Django, Express | Native async (essential when most time is I/O wait), Pydantic validation at the boundary for free, `StreamingResponse` for SSE, automatic OpenAPI docs, `BackgroundTasks`. Flask is sync-first (needs gunicorn workers for concurrency); Django is far too heavy for 4 JSON endpoints. |
| LLM provider | **Groq** | OpenAI, Anthropic, Bedrock, self-hosted vLLM | Groq's LPU inference is dramatically faster tokens/sec, which matters because generation sits in a user-facing chain already carrying retrieval + rerank + NLI latency. It's **OpenAI-API-compatible**, so it's one `base_url` change to switch providers — vendor lock-in avoided by design. Self-hosting means GPUs and ops. |
| Vector DB | **Pinecone** | pgvector, Weaviate, Qdrant, Milvus, Chroma, FAISS | Fully managed (no HNSW tuning, no index-rebuild ops), and crucially **server-side integrated embedding**, which removes an entire model from the query path and guarantees index/query model symmetry. pgvector would avoid a whole system (one fewer store, transactional consistency with metadata) and is the strongest alternative — the trade is you own the index lifecycle. FAISS/Chroma aren't durable multi-client services. |
| Relational DB | **Neon Postgres** | RDS, Supabase, self-hosted PG | Serverless Postgres: scale-to-zero, cheap for bursty workloads, branchable databases. Plain Postgres semantics, no lock-in. *Caveat:* aggressive idle-connection handling is exactly why cron opens a connection per page. |
| Analytics DB | **ClickHouse** | Postgres table, Prometheus, Datadog, BigQuery | Columnar OLAP designed for exactly this: append-heavy, aggregate-read telemetry. Keeps analytical load off the OLTP database serving retrieval. Prometheus is for numeric time-series with labels — it can't store `query` text or do per-trace joins. Datadog costs money per host/custom metric. |
| Reranker | **local cross-encoder** | Cohere Rerank API, Jina, no rerank | Local = no per-query API cost, no network latency to a third party, no data leaving the network (matters for *internal* docs), fully deterministic. Cohere Rerank is more accurate but adds cost + latency + a data-residency question. |
| Groundedness check | **DeBERTa MNLI** | LLM-as-judge, RAGAS, nothing | Small (184M), fast, CPU-friendly, purpose-trained, deterministic, runs in the online path. An LLM judge doubles LLM cost/latency per query and is itself fallible. |
| Embeddings | **Pinecone integrated** + MiniLM for chunking | OpenAI `text-embedding-3`, BGE, local E5 | Integrated inference removes a model and a failure mode from the request path. MiniLM is local for chunking because that's a throwaway offline signal where quality matters less and per-call API cost would be absurd (one call per sentence window). |
| Frontend | **Next.js App Router** | plain React/Vite, SvelteKit | Server-side route handlers give a **proxy layer with no extra service**: the LLM engine stays private and CORS disappears. Streaming responses are first-class. |
| Content seeding | **n8n** | Python script, Airflow | Visual API orchestration with built-in retries, batching, branching and run history. Right tool for a one-time ETL of API calls. |
| Change detection | **webhook stash + cron delta** | webhook-driven reprocessing, full cron rescan, CDC | See 4.8 — the only option whose cost scales with *meaningful change* rather than with edit volume or corpus size. |
| Streaming | **SSE** | WebSocket, polling | Unidirectional need, plain HTTP, proxy-friendly, trivial to relay through Next.js. |
| Chunking | **structural → semantic** | fixed-size, recursive-character, agentic | Bounded cost and predictable size from stage 1; topic-coherent boundaries from stage 2. |
| Dedup/identity | **sha256 content hash as PK** | autoincrement ID + separate hash column, composite `(page_id, hash)` | Idempotency, change detection, and the cross-store join key all fall out of one decision. Trade-off: lossy provenance for duplicated chunks. |
| Delete semantics | **`is_active` soft delete** | hard DELETE + Pinecone delete | Auditability, one-flag restore, cheap `UPDATE` instead of vector deletion. Cost: unbounded growth, dead vectors occupying recall slots. |

---

# PART 9 — Interview Q&A Drill

## 9.1 System design

**"Walk me through what happens when a user asks a question."**
> Browser POSTs to the Next.js route handler, which proxies to the LLM engine's
> `/query/stream` — the engine isn't internet-reachable. The engine generates a
> `trace_id`, opens a Postgres connection, and emits SSE events as it goes.
> First, intent classification: a small Groq model at temperature 0 labels the query
> `knowledge` or `general`; `general` goes straight to a conversational reply and we're
> done in one LLM call. For `knowledge`: two parallel Pinecone searches (top-20 chunks,
> top-20 pages) with server-side embedding, then hydrate the chunk IDs from Postgres to
> get raw text, section path, and the `is_active` flag. Fuse chunk and page scores
> 0.7/0.3, keep the top 15, send them to the cross-encoder reranker, blend
> `0.7×rerank + 0.3×fused`, take the top 10, and drop anything under 0.6. If nothing
> survives, we fall through to a DuckDuckGo web search and clearly label the answer as
> web-sourced. Otherwise we build a context string where each chunk carries its page ID,
> section path, and an ACTIVE/INACTIVE marker, and fan out two parallel calls: Groq
> generation with "answer only from context," and image retrieval against the captions
> index. Then validation: NLI between the context (premise) and the answer (hypothesis)
> — contradiction ≥ 0.7 blocks the answer — plus a Guardrails length check. We stream
> the result with sources and images, and write one `query_result` row plus one
> `stage_execution` row per stage into ClickHouse, all tagged with the trace ID.

**"How does the system stay fresh when documents change?"**
> Give the 4.8 answer: the two naive extremes and why each fails, then detection/
> processing separation, then the hash delta. Lead with *"edits are not atomic"* — it's
> the insight the whole design rests on.

**"How would you scale this to 10 million documents / 10k QPS?"**
> - **Ingestion**: replace in-process `BackgroundTasks` with a real queue (SQS/Kafka +
>   Celery workers). Shard the processor by page ID and run it as a horizontally scaled
>   consumer instead of a sequential loop. Batch the Postgres writes (`execute_values`)
>   instead of per-row inserts — that's currently the biggest ingestion bottleneck.
> - **Retrieval**: Pinecone scales horizontally already. Add namespaces per
>   tenant/space to shrink the search space. The reranker becomes the bottleneck
>   (one forward pass per candidate): move it to GPU, batch across requests, cut
>   candidates from 15 to 10, or distil a smaller model.
> - **Caching**: a semantic cache on the query embedding (if a near-identical question
>   was answered recently, return it) kills a large share of duplicate work; plus a
>   plain response cache keyed on normalised query.
> - **Postgres**: replace per-request `psycopg2.connect` with PgBouncer or a pool, add
>   read replicas for the hydration query, index `kb_chunks(page_id, is_active)`.
> - **Stateless services** behind a load balancer; the LLM engine already holds no
>   per-user state (history comes from the client), so this is free.
> - **Cost**: the LLM dominates. Route easy questions to a smaller model, cache
>   aggressively, and cap context size.

**"What's the single biggest weakness?"**
> Pick one and go deep — the NLI 512-token truncation, the absent commits, the
> page-score key bug, or no eval harness. Owning a specific flaw with a specific fix
> beats "it could use more tests."

**"Why three databases? Isn't that over-engineering?"**
> Use the 2.2 table. Emphasise access patterns: ANN search, transactional point
> lookups, and analytical aggregates are three genuinely different workloads. Then
> concede: *pgvector would collapse two of them into one and I'd seriously consider it
> for v2 — the main thing I'd lose is Pinecone's server-side embedding.*

## 9.2 RAG & ML

**"Why rerank if the vector search already ranks?"**
> Bi-encoder vs cross-encoder (5.4), then the cascade. Mention the concrete cost
> argument: a cross-encoder over a million chunks is a million forward passes per
> query; over 15 it's milliseconds.

**"How do you prevent hallucination?"**
> Four layers, and say explicitly that no single one is sufficient:
> 1. **Constrained prompt** — "answer only using the provided context," plus a licensed
>    refusal string so the model has a sanctioned way to say no.
> 2. **Retrieval threshold** — if nothing scores ≥ 0.6, we don't let it answer from the
>    KB at all. Vector search always returns *something*; the threshold is what creates
>    the possibility of "I don't know."
> 3. **NLI contradiction check** — automated groundedness verification on the output.
> 4. **Citations** — every answer returns the exact source chunks with page IDs and
>    section paths, so the user can verify. Verifiability is itself a control.
> Then the honest limit: *this catches contradiction, not omission. An unsupported but
> non-contradictory claim passes. Claim-level verification and per-chunk NLI are the
> next step.*

**"Explain your chunking strategy and why."**
> 4.5. Lead with the two-stage rationale (bounded cost + predictable size, then
> coherent boundaries), then the parameters, then the table-explosion decision — that
> one is distinctive and shows you thought about non-prose content.

**"How do you handle tables and images in RAG?"**
> Tables: per-cell facts with caption + section + column header re-injected so each
> chunk is self-contained, because embedding a whole table averages unrelated facts
> into one useless vector. Images: retrieved via text embeddings of captions, with a
> five-level caption fallback chain; no CLIP, because caption text is a good enough
> signal at ~1000× less cost. Note the explicit three-way decomposition where each
> extractor `decompose()`s what it consumed, so nothing is processed twice.

**"How would you evaluate this system?"**
> This is the question the project is weakest on, so answer it well:
> - **Retrieval**: a golden set of query→relevant-chunk pairs; measure Recall@k, MRR,
>   NDCG@10. Run it in CI so a chunking or fusion change can't silently regress recall
>   (exactly the failure mode of the page-score bug). *Note the UI already ships five
>   "golden questions" — that's the seed of an eval set.*
> - **Generation**: faithfulness and answer-relevance (RAGAS), plus human review on a
>   sample.
> - **End-to-end**: the ClickHouse `query_result` distribution — `success` vs
>   `contradicted` vs `web_fallback` vs `no_answer` — is already a live quality signal.
> - **Online**: thumbs up/down in the UI, correlated with `contradiction_score` and
>   `top_k_chunks`, to find where confidence and correctness diverge.
> - **Regression**: snapshot answers for the golden set and diff on every prompt change.

**"What is NLI and why use it over an LLM judge?"** → 5.6.

**"Your 'hybrid' retrieval — hybrid in what sense?"** → 5.3. Be precise that it's
chunk+page dense fusion, *not* dense+sparse, and name BM25/RRF as the gap.

**"What embedding model do you use?"** → Pinecone's server-side model for retrieval;
local MiniLM only for chunking breakpoints. Don't conflate them.

## 9.3 Data & reliability

**"What happens if Pinecone succeeds but Postgres fails?"**
> You get an orphaned vector: retrievable from the index with no metadata row. The
> fusion loop handles it defensively (`if not meta: continue`), so it's dropped rather
> than crashing. Durably, `kb_page_ingestion_state` records `neon_status != success`
> and the retry job replays just that stage. *The real fix is a two-phase pattern:
> write Postgres first as the system of record, then index — and reconcile periodically
> by diffing index IDs against table IDs.*

**"Is this idempotent? Prove it."**
> Three mechanisms: content-hash primary keys mean the same text always produces the
> same row ID; `ON CONFLICT DO NOTHING` makes re-insertion a no-op; and `is_stashed` is
> only cleared after all four writes succeed, so a crash mid-run just leaves the page
> stashed for the next run. Net effect: at-least-once delivery into idempotent sinks =
> effectively-once, without distributed transactions. The n8n layer does the same thing
> differently — delete-then-create so every run converges on identical Confluence state.

**"How do you handle retries without hammering a failing dependency?"**
> Exponential backoff via `urllib3.Retry(backoff_factor=1)` → 0s/2s/4s; a
> `status_forcelist` limited to 429 and 5xx so non-retryable 4xx fail fast; a bounded
> attempt count; per-step retry flags so a successful write is never redone; and a
> `fatal` terminal state so poison pills stop being retried forever. *What's missing: a
> circuit breaker and jitter.* Without jitter, synchronised retries produce a
> thundering herd; without a breaker, a long outage means every request still pays
> three timeouts.

**"Why ClickHouse and not just a Postgres table?"**
> OLTP vs OLAP. Telemetry is append-only and read as aggregates; columnar storage reads
> only the touched columns and `ORDER BY (executed_at, trace_id)` prunes by time range.
> Keeping it out of Postgres also protects the hot retrieval path — you don't want
> dashboard scans competing for the same buffer pool as your `WHERE chunk_hash IN (...)`
> hydration query.

**"Why soft deletes?"** → 3.1. Audit trail, one-flag restore, cheap update, Pinecone
unchanged. Then the cost: unbounded growth and dead vectors in recall — needs a reaper.

**"How do you debug a bad answer in production?"**
> Find the `trace_id` from `query_result` (the `query` text is stored). Join
> `stage_execution` to see which stage was slow or failed. The row tells you
> `top_k_chunks`, `context_chars`, and `contradiction_score`, which immediately
> separates a *retrieval* problem (few chunks, small context) from a *generation*
> problem (good context, bad answer) from a *validation* problem (high contradiction).
> Then LangSmith for the node-level trace and PromptLayer for the exact prompt and
> completion. And the UI's thinking panel already shows which steps ran and which
> failed, so users can report a precise failure.

## 9.4 Code-level / gotchas
These are the "did you actually write this?" questions.

**"Why `run_in_executor` in the reranker and NLI services?"** → 5.4: PyTorch inference
is sync and CPU-bound; calling it inside `async def` blocks the event loop and
serialises every concurrent request. The thread pool keeps the loop free;
`max_workers=4` bounds CPU contention.

**"Why thread-local ClickHouse clients?"** → 6.3: client isn't thread-safe, per-call
creation is wasteful.

**"Why `BackgroundTasks` and an immediate ACK?"** → 4.2, including the durability
weakness and the state-table compensation.

**"Why is the column name interpolated into that SQL string?"** → 4.2: identifiers
can't be parameterised; guarded by an explicit allow-list.

**"Why `X-Accel-Buffering: no`?"** → 5.8: stops nginx buffering the SSE stream.

**"Why does the SSE client keep a leftover buffer?"** → 5.8: a TCP chunk can end
mid-event; `parts.pop()` retains the partial.

**"Why does cron open a new Postgres connection per page?"** → 4.7: Neon drops idle
connections; bounds failure blast radius to one page. Pooling is the better answer.

**"Why batch Pinecone upserts at 90?"** → under the 96-record API limit, with headroom.

**"Why `temperature=0.2` for answers but `0.7` for chit-chat?"** → factual grounded
answering wants determinism; conversational replies want variety. Per-task temperature.

**"Why BuildKit secrets instead of ARG?"** → 5.6: `ARG`/`ENV` persist in image layers
and leak via `docker history`; a mounted secret doesn't.

**"Why `profiles: [trigger]`?"** → 7.1: keeps one-shot jobs out of `docker compose up`.

---

# PART 10 — Known Weaknesses & What You'd Fix

Interviewers trust candidates who criticise their own work accurately. Have these
ready, ranked. **Lead with the first three.**

## 10.1 Real bugs in the committed code

1. **Missing `commit()` — writes are silently rolled back.**
   `psycopg2` connects with `autocommit = False`. `services/page_indexer/main.py`
   (`init_state`, `insert_neon`, `update_state`, `page_updated`, `page_removed`,
   `page_restored`, `page_title_updated`), `jobs/indexer_retry/main.py`
   (`set_all_fatal`), and `cron`'s `deactivate_neon_*` all use
   `with conn.cursor() as cur:` and then `conn.close()` — **with no `conn.commit()`
   anywhere in the repo**. Closing a connection discards the open transaction, so those
   writes never persist.
   Contrast `jobs/page_processor/helpers/utils.py`, which uses `with conn:` — the
   *connection* context manager, which commits on clean exit. That's why processing
   writes work and indexer writes don't.
   Subtlety worth mentioning: in `cron`, `deactivate_neon_chunks` doesn't commit, but a
   later `mark_page_unstashed` uses `with conn:` on the *same connection*, so the
   pending deactivations get committed as a side effect. It works by accident.
   **Fix:** set `autocommit=True` in `get_db_conn`, or wrap every write in `with conn:`,
   or add explicit `conn.commit()`. This is the first thing to fix and the best thing to
   volunteer — it shows you can reason about transaction semantics rather than just
   calling an ORM.

2. **Page-score key mismatch kills the page-level channel.**
   `page_scores` keys are `"page:{id}"`; lookup uses `"{id}"` → always 0.0 → `W_PAGE`
   does nothing. One-line fix. The real lesson is **silent degradation**: no error, no
   log, just quietly worse retrieval. Mitigation is a retrieval eval in CI.

3. **Dead web-fallback branch.** `"not found in knowledge base"` vs the prompt's
   `"I couldn't find that in the knowledge base."` Fix: have the LLM emit a structured
   signal instead of matching prose.

4. **NLI premise truncated to 512 tokens** — only the first chunk or two is actually
   checked. Fix: per-chunk NLI with max-aggregation.

5. **Mixed score scales at the threshold** — unbounded cross-encoder logits blended
   with [0,1] cosine, compared to 0.6. Fix: sigmoid the logits first.

6. **`/query` and `/query/stream` duplicate the entire pipeline.** ~300 lines of
   retrieval/rerank/validate logic exist twice, and the two can drift (they already
   differ: the stream path has a retrieval→web fallback the graph path lacks, and the
   graph path has no `web_fallback` status). Fix: keep LangGraph as the single
   implementation and stream from it (`stream_mode="updates"` or `astream_events`),
   emitting SSE from node transitions. Say this before you're asked — the right framing
   is *"the graph was built first; the streaming UX was added later and I duplicated
   rather than refactored. That was the wrong call and I know how to fix it."*

7. **ACTIVE/INACTIVE labels are never explained to the model** — the marker exists but
   the prompt gives no instruction for weighing them, so stale content may be used
   silently. One prompt line fixes it.

8. **Broad `except Exception: return None/False` everywhere.** It makes the system
   resilient but *swallows root causes* — `get_env`, `get_db_conn`, and
   `get_pinecone_client` all return `None` on failure with no log line, so a bad
   credential looks identical to a network blip. Fix: log the exception, and distinguish
   expected failures from bugs.

9. **Duplicate function names shadow each other.** Two functions named `page_created`;
   the route handler `page_restored` is shadowed by a later module-level
   `page_restored`, which is what `add_task` actually resolves to at runtime. It happens
   to work; it's a landmine.

10. **No Postgres DDL in the repo.** Only the ClickHouse schema is version-controlled.
    The four Postgres tables were created by hand in Neon, so the schema is undocumented,
    unreproducible, and un-reviewable. Fix: add migrations (Alembic or plain SQL files)
    — note the ClickHouse side already does this correctly via
    `docker-entrypoint-initdb.d`, so you know the pattern.

11. **🔴 A live Confluence API token is committed to the repo** in
    `confluence_webhooks_guide.txt` (and `.gitignore` has `# .env` commented out).
    Before showing this repo to anyone: **revoke that token in Atlassian**, replace it
    with a placeholder, and purge it from git history (`git filter-repo` or BFG) —
    rewriting the file alone leaves it in every clone. If an interviewer finds it first,
    it's the only thing they'll remember. If *you* raise it as "here's a mistake I made
    and here's the full remediation — revoke, purge history, add a pre-commit secret
    scanner like gitleaks," it becomes a point in your favour.

## 10.2 Architectural gaps

- **No authentication or authorisation anywhere.** Anyone who can reach the frontend
  can query the entire knowledge base. A real enterprise KB needs SSO and
  **document-level ACLs enforced at retrieval time** (filter by the user's permitted
  spaces *before* reranking — a hard and interesting problem, because the vector index
  must be ACL-aware or you leak content through retrieval). Worth naming as the #1
  productionisation gap.
- **No evaluation harness.** Thresholds (0.6, 0.7, 0.7/0.3, 0.7/0.3, 0.15) were tuned
  by hand with no measurement. Without a golden set you cannot know whether a change
  helped. This is the highest-leverage thing to add.
- **No tests at all.** No unit tests for extractors (which are pure functions over HTML
  and the easiest thing in the repo to test), no integration tests, no CI.
- **No durable queue.** `BackgroundTasks` loses work on restart.
- **No rate limiting** on the public frontend route — an open door to LLM cost abuse.
  `max_length` caps per-request cost but nothing caps request volume.
- **No connection pooling** — `psycopg2.connect` per request.
- **No circuit breakers or jitter** — retries can synchronise into a herd; a long
  outage means every request still pays three timeouts.
- **Query path not history-aware** — no query rewriting, so follow-up questions
  retrieve badly. High-value, low-effort fix.
- **No reaper for inactive rows/vectors** — soft-deleted content accumulates forever
  and dead vectors consume top-k recall slots.
- **No `conn.commit`-adjacent concern: no transaction boundary around the Pinecone +
  Postgres pair** — they can't be made atomic, so reconciliation is required and isn't
  implemented.
- **Domain hard-coded in prompts** ("Alphabet/Google", "not Amazon/Apple") — the router
  is coupled to the corpus; should be config/derived per tenant.
- **Secrets via `.env` + a file-based Docker secret** — fine locally, should be a
  managed secret store (Vault, AWS Secrets Manager) in production.
- **No healthchecks / readiness probes** (only `page-indexer` has `/health`), and
  `depends_on` is start-ordering, not readiness.
- **Frontend runs `next dev` in Docker** — should be `next build && next start`.
- **No multi-stage builds / non-root user** in the images.
- **Observability sprawl** — ClickHouse + LangSmith + PromptLayer is three vendors;
  OpenTelemetry into one backend would be cleaner, and the trace_id isn't propagated to
  the reranker/NLI services so their internal time is invisible.

## 10.3 The improvement roadmap (say it in this order)
1. Fix the transaction bug, the page-score key, and purge the leaked token.
2. Build a golden eval set (start from the five golden questions in the UI) and wire
   Recall@k / NDCG into CI.
3. Add auth + document-level ACL filtering at retrieval time.
4. Collapse `/query` and `/query/stream` onto one LangGraph implementation.
5. Add sparse/BM25 retrieval fused via RRF for exact-match queries.
6. Add history-aware query rewriting.
7. Per-chunk NLI with max-aggregation; sigmoid-normalise rerank scores.
8. Durable queue for ingestion + connection pooling + rate limiting.
9. Semantic response cache; real token streaming from Groq.
10. Migrations, tests, healthchecks, multi-stage builds.

---

# PART 11 — The Numbers Cheat Sheet

| Constant | Value | Where | Why |
|---|---|---|---|
| `chunk_size` | 300 tokens | SentenceSplitter | one idea per chunk; ~10 fit in context |
| `chunk_overlap` | 50 (~17%) | SentenceSplitter | facts straddling a boundary stay retrievable |
| `buffer_size` | 1 | SemanticSplitter | compare each sentence to its neighbour |
| `breakpoint_percentile_threshold` | 90 | SemanticSplitter | cut at top 10% of distance spikes; adapts per doc |
| min chunk length | > 30 chars | processor | drop meaningless fragments |
| `TOP_K_CHUNKS` | 20 | retrieve | recall stage |
| `TOP_K_PAGES` | 20 | retrieve | page-level signal |
| pre-rerank cut | 15 | retrieve | bound cross-encoder cost |
| `FINAL_TOP_K` | 10 | retrieve | context budget |
| `W_CHUNK` / `W_PAGE` | 0.7 / 0.3 | fusion | chunk primary, page modifier |
| `ALPHA` | 0.7 | rerank blend | reranker primary, retrieval as ensemble vote |
| `THRESHOLD` | 0.6 | final gate | enables "I don't know" + web fallback |
| `IMAGE_SCORE_THRESHOLD` | 0.15 | images | low — images are supplementary, favour recall |
| `TOP_K_IMAGES` | 5 (from 20 searched) | images | UI panel size |
| `NLI_CONTRADICTION_THRESHOLD` | 0.7 | validate | conservative; avoid false refusals |
| `ValidLength` | 20–4000 chars | guardrails | catch degenerate output |
| `temperature` | 0 / 0.2 / 0.7 | intent / answer / chat | determinism scales with factuality need |
| `max_tokens` | 5 / 800 / 400 | intent / answer / chat | bound cost per task |
| `Retry.total` | 3, `backoff_factor` 1 | all HTTP | 0s/2s/4s exponential backoff |
| `status_forcelist` | 429,500,502,503,504 | all HTTP | retry only transient failures |
| Pinecone batch | 90 | upserts | under the 96 API limit |
| `max_workers` | 4 | reranker, NLI | bound CPU contention |
| query `max_length` | 1000 chars | Pydantic | DoS / cost bound |
| history `max_items` | 20 | Pydantic | token-growth bound |
| rerank input cap | 50 texts, 2000 chars each | Pydantic | latency bound |
| SSE idle timeout | 60s, resets per chunk | frontend | kill stalled, not slow, streams |
| Confluence page fetch | `limit=50` per page | listing | bounded responses |
| ClickHouse `nofile` | 262144 | compose | ClickHouse opens many files per part |

---

# PART 12 — Whiteboard Narration

Draw this, left to right, and narrate in this order.

```
OFFLINE (freshness)                          ONLINE (trust)

 Wikipedia                                    Browser
    │ n8n (one-off ETL)                          │  SSE
    ▼                                            ▼
 CONFLUENCE  ◄── source of truth ──┐        Next.js proxy  (keeps engine private)
    │                              │             │
    │ webhooks (page created/      │             ▼
    │  updated/deleted/restored)   │        ┌─────────── LLM ENGINE ───────────┐
    ▼                              │        │ 1 intent router  (Groq, temp 0)  │
 PAGE-INDEXER ──► kb_pages         │        │      ├── general ──► reply ──► ⏎ │
   (metadata only,                 │        │      └── knowledge               │
    is_stashed=TRUE,               │        │ 2 retrieve: Pinecone chunks(20)  │
    state machine)   ──► kb-pages  │        │    + pages(20) ─► hydrate Neon   │
    │                   (Pinecone) │        │    ─► fuse 0.7/0.3 ─► top 15     │
    │ failures                     │        │ 3 rerank: cross-encoder          │
    ▼                              │        │    ─► 0.7·rr + 0.3·fused         │
 INDEXER-RETRY (replay / fatal)    │        │    ─► top 10 ─► gate ≥ 0.6       │
                                   │        │        └─ none ─► WEB FALLBACK   │
 PAGE-PROCESSOR ───────────────────┘        │ 4 generate (Groq)  ║  images     │
   one_off : all pages                      │    "answer only from context"    │
   cron    : is_stashed only                │ 5 validate: NLI contradiction    │
   ├ extract images   ─► kb_images/kb-images│    ≥0.7 block + length guard     │
   ├ extract tables (per-cell facts)        └──────────────────────────────────┘
   ├ html ─► markdown ─► SentenceSplitter(300/50)      │
   │                   ─► SemanticSplitter(p90)        │ answer + sources + images
   ├ sha256 ─► content-addressed IDs                   ▼
   ├ hash set-diff: add new / is_active=FALSE old   Browser (thinking panel,
   └ all 4 writes OK ─► is_stashed=FALSE             citations, image panel)

       every stage ──► trace_id ──► CLICKHOUSE ──► METABASE
```

**Narration script (60–90 seconds):**
1. "Confluence is the source of truth. n8n seeded it from Wikipedia as a one-time ETL."
2. "Page-indexer does metadata **only** — it has to stay fast because it also serves
   webhooks. It records that a page exists and marks it `is_stashed`."
3. "Webhooks answer one question: *which page changed?* They do no chunking. That's
   deliberate — edits aren't atomic, so one logical update fires dozens of events, and
   reprocessing on each would make load scale with typing rather than with meaning."
4. "Cron decides *when* to pay. It processes only stashed pages, hash-diffs old against
   new, inserts the adds, soft-deletes the removes, and leaves the unchanged majority
   untouched. Cost scales with change, not with corpus size."
5. "Processing decomposes HTML three ways — images, tables as per-cell self-contained
   facts, prose via structural-then-semantic chunking — and content-hashes everything,
   which gives idempotency, change detection, and the cross-store join key from one
   decision."
6. "On the query side: route cheap requests away from the expensive path, then
   recall-then-precision — bi-encoder ANN for cheap recall, cross-encoder for expensive
   precision on survivors, and an absolute threshold so the system can say 'I don't
   know' and fall back to the web."
7. "Generation is constrained to the retrieved context, then independently verified by
   an NLI model asking 'does the knowledge base actually support this?'"
8. "Every stage writes a trace-correlated row to ClickHouse, so I can answer 'which
   stage is the p95 bottleneck' and 'what fraction of answers get blocked as
   contradicted' without guessing."

**Closing line:** *"The three ideas I'd want you to take away are: separate change
detection from change processing, separate recall from precision, and separate
generation from trust."*
