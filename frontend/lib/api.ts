export type HistoryMessage = { role: "user" | "assistant"; content: string };

export type QueryResponse = {
  query: string;
  answer: string;
  sources: { page_id: string; section: string; text: string }[];
  images: { url: string; page_id: string; caption: string | null }[];
};

export async function postQuery(
  query: string,
  history: HistoryMessage[] = []
): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, history }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.detail || `Query failed: ${res.status}`);
  }
  return data as QueryResponse;
}

export type StreamDonePayload = {
  type: "done";
  answer: string;
  sources: QueryResponse["sources"];
  images: QueryResponse["images"];
  replaceAnswer: string | null;
};

export type StreamTokenPayload = { type: "token"; delta: string };
export type StreamErrorPayload = { type: "error"; error: string };
export type StreamStagePayload = {
  type: "stage";
  stage: "searching" | "fetching_context" | "reranking" | "fetching_images" | "generating";
};

export async function postQueryStream(
  query: string,
  history: HistoryMessage[],
  callbacks: {
    onToken: (delta: string) => void;
    onStage?: (stage: StreamStagePayload["stage"]) => void;
    onDone: (payload: StreamDonePayload) => void;
    onError: (message: string) => void;
  }
): Promise<void> {
  const res = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, history }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    callbacks.onError(data.error || `Stream failed: ${res.status}`);
    return;
  }
  const reader = res.body?.getReader();
  if (!reader) {
    callbacks.onError("No response body");
    return;
  }
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const raw = line.slice(6);
          if (raw === "[DONE]" || raw === "") continue;
          try {
            const data = JSON.parse(raw) as
              | StreamTokenPayload
              | StreamDonePayload
              | StreamErrorPayload
              | StreamStagePayload;
            if (data.type === "token") {
              callbacks.onToken(data.delta);
            } else if (data.type === "stage") {
              callbacks.onStage?.(data.stage);
            } else if (data.type === "done") {
              callbacks.onDone(data);
            } else if (data.type === "error") {
              callbacks.onError(data.error);
            }
          } catch {
            // ignore parse errors for partial chunks
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
