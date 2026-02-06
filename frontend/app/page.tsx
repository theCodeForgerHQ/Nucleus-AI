"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  postQueryStream,
  type QueryResponse,
  type HistoryMessage,
} from "@/lib/api";
import { TerminalBlock } from "@/components/TerminalBlock";
import { TerminalInput } from "@/components/TerminalInput";
import { ImagesPanel } from "@/components/ImagesPanel";

type Block = {
  id: string;
  prompt: string;
  response: QueryResponse | null;
  isStreaming: boolean;
  /** Accumulated answer text while streaming (tokens in real time) */
  streamingAnswer: string;
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
      {
        id,
        prompt: q,
        response: null,
        isStreaming: true,
        streamingAnswer: "",
      },
    ]);
    setLoading(true);

    const history = buildHistory();
    postQueryStream(q, history, {
      onToken: (delta) => {
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === id
              ? { ...b, streamingAnswer: b.streamingAnswer + delta }
              : b
          )
        );
      },
      onDone: (payload) => {
        const answer = payload.replaceAnswer ?? payload.answer;
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === id
              ? {
                  ...b,
                  response: {
                    query: q,
                    answer,
                    sources: payload.sources,
                    images: payload.images,
                  },
                  isStreaming: false,
                  streamingAnswer: "",
                }
              : b
          )
        );
        setLoading(false);
      },
      onError: (message) => {
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
                  streamingAnswer: "",
                }
              : b
          )
        );
        setLoading(false);
      },
    });
  }, [input, loading, buildHistory]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [blocks]);

  // Images from the most recent response that has images (for right panel)
  const sidebarImages =
    [...blocks]
      .reverse()
      .find((b) => b.response && b.response.images.length > 0)?.response
      ?.images ?? [];

  return (
    <div className="flex flex-col h-screen">
      {/* Top bar - Warp style minimal */}
      <header className="shrink-0 h-9 flex items-center px-4 border-b border-warp-border bg-warp-surface">
        <span className="text-warp-muted text-xs font-medium">
          Nucleus AI — Knowledge Base
        </span>
      </header>

      {/* 75% content | 25% images */}
      <div className="flex-1 flex min-h-0">
        {/* Left 75%: scrollable chat */}
        <div
          ref={scrollRef}
          className="w-[75%] min-w-0 flex flex-col overflow-y-auto overflow-x-hidden px-4 py-4 border-r border-warp-border"
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
              streamingAnswer={block.streamingAnswer}
            />
          ))}
          {error && (
            <div className="text-warp-red text-sm py-2" role="alert">
              {error}
            </div>
          )}
        </div>

        {/* Right 25%: vertical scroll of images */}
        <aside className="w-[25%] min-w-0 flex flex-col bg-warp-surface/50">
          <div className="shrink-0 px-2 py-2 border-b border-warp-border text-warp-muted text-xs font-medium">
            Images
          </div>
          <ImagesPanel images={sidebarImages} />
        </aside>
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
