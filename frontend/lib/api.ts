export type HistoryMessage = { role: "user" | "assistant"; content: string };

export type QueryResponse = {
  query: string;
  answer: string;
  sources: { page_id: string; section: string; text: string }[];
  images: { url: string; page_id: string; caption: string | null }[];
};

export type ThinkingStep = {
  id: string;
  label: string;
  status: "pending" | "in_progress" | "done" | "failed";
  result?: string;
};

export type StreamEvent =
  | { type: "step"; data: ThinkingStep }
  | { type: "branch"; data: { name: string } }
  | { type: "steps"; data: { steps: { id: string; label: string }[] } }
  | { type: "result"; data: QueryResponse }
  | { type: "done"; data: Record<string, never> };

export async function postQuery(
  query: string,
  history: HistoryMessage[] = [],
  signal?: AbortSignal,
): Promise<QueryResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 60000);

  const combinedSignal = signal
    ? AbortSignal.any([signal, controller.signal])
    : controller.signal;

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, history }),
      signal: combinedSignal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || data.detail || `Query failed: ${res.status}`);
    }
    return data as QueryResponse;
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function postQueryStream(
  query: string,
  history: HistoryMessage[] = [],
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const controller = new AbortController();
  let timeoutId = setTimeout(() => controller.abort(), 60000);

  const combinedSignal = signal
    ? AbortSignal.any([signal, controller.signal])
    : controller.signal;

  try {
    const res = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, history }),
      signal: combinedSignal,
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || data.detail || `Query failed: ${res.status}`);
    }

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();

      // Reset timeout on each chunk received
      clearTimeout(timeoutId);
      if (done) break;
      timeoutId = setTimeout(() => controller.abort(), 60000);

      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double newlines
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        if (!part.trim()) continue;

        let eventType = "";
        let eventData = "";

        for (const line of part.split("\n")) {
          if (line.startsWith("event:")) {
            eventType = line.substring(6).trim();
          } else if (line.startsWith("data:")) {
            eventData += line.substring(line.startsWith("data: ") ? 6 : 5) + "\n";
          }
        }

        if (eventType && eventData) {
          try {
            // Trim trailing newline for parsing just in case
            const parsed = JSON.parse(eventData.trimEnd());
            onEvent({ type: eventType, data: parsed } as StreamEvent);
          } catch {
            // skip malformed events
          }
        }
      }
    }
  } finally {
    clearTimeout(timeoutId);
  }
}
