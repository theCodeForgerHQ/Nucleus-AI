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
