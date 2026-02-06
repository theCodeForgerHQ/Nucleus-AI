"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { postQuery, type QueryResponse, type HistoryMessage } from "@/lib/api";
import { TerminalBlock } from "@/components/TerminalBlock";
import { TerminalInput } from "@/components/TerminalInput";

type Block = {
  id: string;
  prompt: string;
  response: QueryResponse | null;
  isStreaming: boolean;
};

function nextId() {
  return Math.random().toString(36).slice(2, 12);
}

export default function Home() {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const buildHistory = useCallback((): HistoryMessage[] => {
    const out: HistoryMessage[] = [];
    for (const b of blocks) {
      if (b.response) {
        out.push({ role: "user", content: b.prompt });
        out.push({ role: "assistant", content: b.response.answer });
      }
    }
    return out;
  }, [blocks]);

  const handleSubmit = useCallback(async () => {
    const q = input.trim();
    if (!q || loading) return;

    setInput("");
    setError(null);
    const id = nextId();
    setBlocks((prev) => [
      ...prev,
      { id, prompt: q, response: null, isStreaming: true },
    ]);
    setLoading(true);

    try {
      const history = buildHistory();
      const response = await postQuery(q, history);
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === id
            ? { ...b, response, isStreaming: false }
            : b
        )
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : "Request failed";
      setError(message);
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === id
            ? {
                ...b,
                response: {
                  query: q,
                  answer: `Error: ${message}`,
                  sources: [],
                  images: [],
                },
                isStreaming: false,
              }
            : b
        )
      );
    } finally {
      setLoading(false);
    }
  }, [input, loading, buildHistory]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [blocks]);

  return (
    <div className="flex flex-col h-screen">
      {/* Top bar - Warp style minimal */}
      <header className="shrink-0 h-9 flex items-center px-4 border-b border-warp-border bg-warp-surface">
        <span className="text-warp-muted text-xs font-medium">
          Nucleus AI — Knowledge Base
        </span>
      </header>

      {/* Scrollable blocks */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-4"
      >
        {blocks.length === 0 && (
          <div className="text-warp-muted text-sm py-8">
            <p>Ask a question. Answers are based on your knowledge base.</p>
            <p className="mt-2 text-warp-accent">
              Type below and press Enter to query.
            </p>
          </div>
        )}
        {blocks.map((block) => (
          <TerminalBlock
            key={block.id}
            prompt={block.prompt}
            response={block.response}
            isStreaming={block.isStreaming}
          />
        ))}
        {error && (
          <div className="text-warp-red text-sm py-2" role="alert">
            {error}
          </div>
        )}
      </div>

      {/* Fixed input at bottom */}
      <TerminalInput
        value={input}
        onChange={setInput}
        onSubmit={handleSubmit}
        disabled={loading}
        placeholder="Ask anything..."
      />
    </div>
  );
}
