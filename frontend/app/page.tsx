"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  postQueryStream,
  type QueryResponse,
  type HistoryMessage,
  type StreamStagePayload,
} from "@/lib/api";
import { TerminalBlock } from "@/components/TerminalBlock";
import { TerminalInput } from "@/components/TerminalInput";
import { ImagesPanel } from "@/components/ImagesPanel";

const STORAGE_KEY = "nucleus-ai-chat-blocks";

type Block = {
  id: string;
  prompt: string;
  response: QueryResponse | null;
  isStreaming: boolean;
  /** Accumulated answer text while streaming (tokens in real time) */
  streamingAnswer: string;
  /** Current pipeline stage from backend (real status) */
  pipelineStage: StreamStagePayload["stage"] | null;
};

function nextId() {
  return Math.random().toString(36).slice(2, 12);
}

function loadBlocksFromStorage(): Block[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { id: string; prompt: string; response: QueryResponse }[];
    return parsed.map((b) => ({
      id: b.id,
      prompt: b.prompt,
      response: b.response,
      isStreaming: false,
      streamingAnswer: "",
      pipelineStage: null,
    }));
  } catch {
    return [];
  }
}

function saveBlocksToStorage(blocks: Block[]) {
  const toSave = blocks
    .filter((b) => b.response !== null)
    .map((b) => ({ id: b.id, prompt: b.prompt, response: b.response! }));
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
  } catch {
    // ignore quota or other storage errors
  }
}

export default function Home() {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const blockRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [activeBlockIndex, setActiveBlockIndex] = useState<number>(0);
  const ratioRef = useRef<number[]>([]);

  // Restore conversation from localStorage after mount (client-only)
  useEffect(() => {
    setBlocks(loadBlocksFromStorage());
  }, []);

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
        pipelineStage: null,
      },
    ]);
    setLoading(true);

    const history = buildHistory();
    postQueryStream(q, history, {
      onStage: (stage) => {
        setBlocks((prev) =>
          prev.map((b) => (b.id === id ? { ...b, pipelineStage: stage } : b))
        );
      },
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

  // Persist completed blocks to localStorage so conversation survives refresh
  useEffect(() => {
    saveBlocksToStorage(blocks);
  }, [blocks]);

  // When blocks change (e.g. new message), default to showing the latest block's images
  useEffect(() => {
    if (blocks.length > 0) {
      setActiveBlockIndex(blocks.length - 1);
    }
    ratioRef.current = new Array(blocks.length).fill(0);
    blockRefs.current = blockRefs.current.slice(0, blocks.length);
  }, [blocks.length]);

  // Intersection Observer: which block is in view → show that block's images in the sidebar
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || blocks.length === 0) return;

    ratioRef.current = new Array(blocks.length).fill(0);

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const index = Number((entry.target as HTMLElement).dataset.blockIndex);
          if (Number.isFinite(index) && index >= 0 && index < blocks.length) {
            ratioRef.current[index] = entry.intersectionRatio;
          }
        }
        const ratios = ratioRef.current;
        let best = 0;
        for (let i = 0; i < ratios.length; i++) {
          if (ratios[i] > ratios[best]) best = i;
        }
        setActiveBlockIndex(best);
      },
      { root, rootMargin: "-15% 0px -25% 0px", threshold: [0, 0.1, 0.25, 0.5, 0.75, 1] }
    );

    const elements = root.querySelectorAll("[data-block-index]");
    elements.forEach((el) => observer.observe(el));

    return () => observer.disconnect();
  }, [blocks.length]);

  // Images for the block currently in view (dynamic by scroll position)
  const sidebarImages = blocks[activeBlockIndex]?.response?.images ?? [];

  // No split until user has sent at least one message; then smooth transition to 75/25
  const hasConversation = blocks.length > 0;

  return (
    <div className="flex flex-col h-screen">
      {/* Top bar - Warp style minimal */}
      <header className="shrink-0 h-9 flex items-center px-4 border-b border-warp-border bg-warp-surface">
        <span className="text-warp-muted text-xs font-medium">
          Nucleus AI — Knowledge Base
        </span>
      </header>

      {/* Full width initially; smooth transition to 75% chat | 25% images after first query */}
      <div className="flex-1 flex min-h-0">
        {/* Main content: 100% when empty, 75% after first message — transition for smoothness */}
        <div
          ref={scrollRef}
          className={`min-w-0 flex flex-col overflow-y-auto overflow-x-hidden px-4 py-4 transition-[width] duration-300 ease-out ${
            hasConversation ? "w-[75%] border-r border-warp-border" : "w-full"
          }`}
        >
          {blocks.length === 0 && (
            <div className="text-warp-muted text-sm py-8 max-w-xl mx-auto text-center">
              <p>Ask a question. Answers are based on your knowledge base.</p>
              <p className="mt-2 text-warp-accent">
                Type below and press Enter to query.
              </p>
            </div>
          )}
          {blocks.map((block, i) => (
            <div
              key={block.id}
              ref={(el) => {
                blockRefs.current[i] = el;
              }}
              data-block-index={i}
            >
              <TerminalBlock
                prompt={block.prompt}
                response={block.response}
                isStreaming={block.isStreaming}
                streamingAnswer={block.streamingAnswer}
                pipelineStage={block.pipelineStage}
              />
            </div>
          ))}
          {error && (
            <div className="text-warp-red text-sm py-2" role="alert">
              {error}
            </div>
          )}
        </div>

        {/* Sidebar: 0 width when no conversation, 25% with smooth slide-in after first message */}
        <aside
          className={`min-w-0 flex flex-col bg-warp-surface/50 overflow-hidden transition-[width] duration-300 ease-out ${
            hasConversation ? "w-[25%]" : "w-0"
          }`}
          aria-hidden={!hasConversation}
        >
          <div className="shrink-0 px-2 py-2 border-b border-warp-border text-warp-muted text-xs font-medium whitespace-nowrap">
            Images
          </div>
          <ImagesPanel images={sidebarImages} isLoading={loading} />
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
