Author: Nithisha
Date: 14 December, 2025

Today was about turning a conceptual idea into a system that executes under real conditions. The focus wasn’t on experimenting with features, but on validating a reliable ingestion workflow end to end: predictable inputs, controlled execution, and deterministic output.

At a high level, the workflow performs four phases in sequence:

Expanding a Wikipedia category into individual content units

Fetching article-level HTML

Normalising content for Confluence storage

Persisting pages into a defined Confluence hierarchy

Each phase is separated at the node level. That separation isn’t incidental — it keeps control flow readable, behaviour observable, and failures easy to reason about during execution.

High-Level Data Flow

This workflow intentionally starts with a Manual Trigger. The goal isn’t continuous synchronisation with Wikipedia, and it isn’t experimentation. This workflow exists to populate a knowledge base once, so there’s no need for scheduling or recurring execution.

All runtime parameters are defined upfront in a single Configuration node: the Wikipedia category to ingest, the target Confluence space, and the ancestor page under which content should be created.

That decision keeps the rest of the workflow stable. The logic never changes; only the inputs do.

By isolating configuration from execution, the workflow stays reusable without becoming dynamic in unpredictable ways. The same pipeline can be pointed at different categories or spaces without conditional logic or hidden state, which makes failures easier to debug and successes easier to reproduce.

Wikipedia Category Pages Fetch

The first operational phase retrieves members of the configured Wikipedia category using the categorymembers API.

This response is intentionally broad. It includes article pages, subcategories, and other structural entities. At this stage, the workflow does not attempt to interpret or filter the results.

Category expansion and content ingestion are treated as separate concerns. The workflow captures the full surface area of the category first, then evaluates each item independently later. This avoids premature assumptions about structure, depth, or relevance.

{
"action": "query",
"list": "categorymembers",
"cmtitle": "{{ $json.title ? $json.title : 'Category:'+$json.category }}",
"cmlimit": "max",
"format": "json"
}

Item-Level Isolation

Once retrieved, category members are split into individual items so each can move through the workflow independently.

This is the point where the workflow stops thinking in aggregates and starts behaving like a system.

Item-level isolation enables controlled retries, batching, and error handling. A malformed page no longer compromises the entire run — it fails locally, not globally.

Controlled Traversal

Items are processed through a bounded loop using batch-based execution. This loop becomes the natural control boundary of the workflow: it regulates throughput, defines progress, and serves as the return point after each item completes.

Each item is then evaluated by namespace:

Namespace 0 items are treated as article pages and forwarded for ingestion

Non-zero namespaces are treated as categories and routed back into category expansion

This allows the workflow to traverse nested categories naturally, without flattening the tree or hard-coding depth limits.

{{ $json.ns }} = 0

This is where the workflow asserts intent: ingest knowledge, not structure — but don’t lose structure in the process.

Wikipedia Page Fetch

Once an item is identified as an article, the workflow fetches the fully rendered HTML from the page URL.

This avoids the limitations of summary-based APIs. Mathematical notation, tables, nested markup, and complex layouts are preserved exactly as they appear on the page.

Here, the workflow prioritises fidelity over convenience, accepting additional cleanup work later in exchange for correctness.

Content Extraction

From the retrieved HTML, only the meaningful content is retained:

The article title

The main content body

Navigation, sidebars, and layout elements are discarded. This keeps downstream transformations focused purely on content that will actually be stored.

Normalisation for Confluence

Before persistence, the content is normalised to match Confluence’s storage format.

This includes:

Rewriting internal Wikipedia links to absolute URLs so references remain valid outside Wikipedia

Converting mathematical expressions into Confluence-compatible macros to ensure correct rendering

This step contains the only truly custom logic in the workflow. Everything else is standard orchestration; this is where platform boundaries are bridged.

The output of this phase is content that is structurally clean, semantically intact, and ready for long-term storage.

let content = $input.first().json.content || "";

content = content.replace(
/<span class="mwe-math-element">[\s\S]*?<img[^>]*alt="([^"]+)"[^>]_>[\s\S]_?<\/span>/g,
(\_match, latex) => {
return `
<ac:structured-macro ac:name="math">
  <ac:plain-text-body><![CDATA[
${latex}
  ]]></ac:plain-text-body>
</ac:structured-macro>`;
}
);

content = content.replace(
/<math[^>]_>([\s\S]_?)<\/math>/g,
(\_match, latex) => {
return `
<ac:structured-macro ac:name="math">
  <ac:plain-text-body><![CDATA[
${latex}
  ]]></ac:plain-text-body>
</ac:structured-macro>`;
}
);

content = content.replace(
/href="\/wiki\//g,
'href="https://en.wikipedia.org/wiki/'
);

return {
json: {
title: $input.first().json.title.trim(),
content
}
};

Confluence Page Resolution and Persistence

With content prepared, the workflow transitions into Confluence-specific operations.

Resolve Before Write

Before creating anything, the workflow checks whether a page with the same title already exists in the target space.

This resolution step is kept separate from the write operation. The workflow determines intent first, then acts.

That separation ensures consistent behaviour across re-runs and avoids hidden branching during persistence.

space="{{ $('Configuration').item.json.space_key }}" AND type=page AND title="{{ $json.title }}"

Replace-or-Create Strategy

If a page already exists, it is deleted and recreated. If it does not exist, it is created directly.

In both cases, the final outcome is the same: a fresh page created under the configured ancestor, using Confluence’s storage representation.

This guarantees idempotency at the workflow level. Every successful run converges on the same state, regardless of what existed before.

Once a page is created, control returns to the main loop and the workflow advances to the next item.

{
"type": "page",
"title": "{{ $('HTML Cleanup').item.json.title }}",
"space": { "key": "{{ $('Configuration').item.json.space_key }}" },
"ancestors": [
{
"id": "{{ $('Configuration').item.json.ancestor_id }}"
}
],
"body": {
"storage": {
"value": {{ JSON.stringify( $('HTML Cleanup').item.json.content) }},
"representation": "storage"
}
}
}

Closing the Loop

Seeing the workflow run end to end validated the design decisions behind it. Clicking “test” and watching pages appear in Confluence — structured, readable, and already organised — marked the point where the system stopped being theoretical. It showed that this approach can scale, and more importantly, that knowledge doesn’t need to be manually moved around all the time.

By the end of the day, the workflow was running as expected, even with edge cases accounted for. It’s a solid, practical step forward, and a foundation we can confidently build on next.
