Today was about getting our hands a little dirty and turning yesterday’s ideas into something that actually works. The focus was on setting up a reliable workflow and making sure it behaved the way we expected under real conditions.

We worked with n8n to build a workflow that pulls content from Wikipedia and stores it into Confluence. The idea was straightforward: take a category from Wikipedia, fetch the related pages, extract the useful content, and save each page properly inside our knowledge base.

Technically, the flow starts by calling Wikipedia’s API to get a list of pages under a specific category. From there, we loop through the results in controlled batches, filtering out anything that isn’t an actual article page. Each valid page is then fetched individually, pulling the full HTML content directly from Wikipedia.

Once the raw content is available, we clean it up before ingestion. This includes fixing internal Wikipedia links so they resolve correctly outside Wikipedia, and converting math expressions into Confluence-compatible macros so formulas render properly instead of breaking. These small transformations make a big difference in how usable the final pages are.

On the Confluence side, the workflow checks whether a page with the same title already exists. If it does, the existing page is safely removed before creating a fresh version. This keeps the process idempotent and avoids clutter or version conflicts. The cleaned content is then pushed via the Confluence REST API and stored as a structured page in the correct space and hierarchy.

A lot of attention went into the practical details. Batch processing prevents overload, conditional checks ensure we only act on valid data, and basic error handling allows the workflow to fail gracefully instead of stopping mid-run. None of this is flashy, but it’s what makes the automation dependable rather than fragile.

What felt good was seeing the whole thing run end to end. Clicking “test” and watching pages appear in Confluence — structured, readable, and already organised — made the effort feel real. It showed that this approach can scale, and more importantly, that knowledge doesn’t need to be manually moved around all the time.

By the end of the day, the workflow was running as expected, even with edge cases accounted for. It’s a solid, practical step forward, and a foundation we can confidently build on next.
