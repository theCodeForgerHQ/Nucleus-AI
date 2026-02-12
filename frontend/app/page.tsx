"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { postQuery, type QueryResponse, type HistoryMessage } from "@/lib/api";
import { TerminalBlock } from "@/components/TerminalBlock";
import {
  TerminalInput,
  type TerminalInputHandle,
} from "@/components/TerminalInput";
import { ImagesPanel } from "@/components/ImagesPanel";

const STORAGE_KEY = "nucleus-ai-chat-blocks";

type Block = {
  id: string;
  prompt: string;
  response: QueryResponse | null;
  isLoading: boolean;
};

function nextId() {
  return Math.random().toString(36).slice(2, 12);
}

function loadBlocksFromStorage(): Block[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as {
      id: string;
      prompt: string;
      response: QueryResponse;
    }[];
    return parsed.map((b) => ({
      id: b.id,
      prompt: b.prompt,
      response: b.response,
      isLoading: false,
    }));
  } catch {
    return [];
  }
}

function saveBlocksToStorage(blocks: Block[]) {
  try {
    const toSave = blocks
      .filter((b) => b.response !== null)
      .map((b) => ({ id: b.id, prompt: b.prompt, response: b.response! }));
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
  } catch {}
}

function clearStorage() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {}
}

export default function Home() {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const blockRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [activeBlockIndex, setActiveBlockIndex] = useState(0);
  const [imagesPanelOpen, setImagesPanelOpen] = useState(false);
  const [sendAnimating, setSendAnimating] = useState(false);
  const [arrowEntering, setArrowEntering] = useState(false);
  const ratioRef = useRef<number[]>([]);
  const inputRef = useRef<TerminalInputHandle | null>(null);
  const hasHydratedRef = useRef(false);

  const ARROW_LAUNCH_MS = 550;

  useEffect(() => {
    setBlocks(loadBlocksFromStorage());
  }, []);

  useEffect(() => {
    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true;
      return;
    }
    saveBlocksToStorage(blocks);
  }, [blocks]);

  useEffect(() => {
    if (!sendAnimating) return;
    const t = setTimeout(() => setSendAnimating(false), ARROW_LAUNCH_MS);
    return () => clearTimeout(t);
  }, [sendAnimating]);

  useEffect(() => {
    if (loading) return;
    setArrowEntering(true);
    const t = setTimeout(() => setArrowEntering(false), 50);
    return () => clearTimeout(t);
  }, [loading]);

  const handleNewChat = useCallback(() => {
    setBlocks([]);
    setError(null);
    setLoading(false);
    clearStorage();
    setTimeout(() => inputRef.current?.focus(), 0);
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

    setSendAnimating(true);
    setInput("");
    setError(null);

    const id = nextId();
    setBlocks((prev) => [
      ...prev,
      { id, prompt: q, response: null, isLoading: true },
    ]);
    setLoading(true);

    try {
      const history = buildHistory();
      const res = await postQuery(q, history);
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === id ? { ...b, response: res, isLoading: false } : b,
        ),
      );
    } catch (e) {
      const message = e instanceof Error ? e.message : "Request failed";
      setError(message);
      setBlocks((prev) =>
        prev.map((b) =>
          b.id === id
            ? {
                ...b,
                isLoading: false,
                response: {
                  query: q,
                  answer: `Error: ${message}`,
                  sources: [],
                  images: [],
                },
              }
            : b,
        ),
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

  useEffect(() => {
    if (blocks.length > 0) {
      setActiveBlockIndex(blocks.length - 1);
    }
    ratioRef.current = new Array(blocks.length).fill(0);
    blockRefs.current = blockRefs.current.slice(0, blocks.length);
  }, [blocks.length]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root || blocks.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const index = Number(
            (entry.target as HTMLElement).dataset.blockIndex,
          );
          if (Number.isFinite(index)) {
            ratioRef.current[index] = entry.intersectionRatio;
          }
        }
        let best = 0;
        for (let i = 1; i < ratioRef.current.length; i++) {
          if (ratioRef.current[i] > ratioRef.current[best]) best = i;
        }
        setActiveBlockIndex(best);
      },
      {
        root,
        rootMargin: "-15% 0px -25% 0px",
        threshold: [0, 0.25, 0.5, 0.75, 1],
      },
    );

    root
      .querySelectorAll("[data-block-index]")
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [blocks.length]);

  const sidebarImagesWithBlock: {
    url: string;
    page_id: string;
    caption: string | null;
    blockIndex: number;
  }[] = [];
  blocks.forEach((b, i) => {
    b.response?.images.forEach((img) => {
      sidebarImagesWithBlock.push({ ...img, blockIndex: i });
    });
  });

  const hasConversation = blocks.length > 0;

  return (
    <div className="flex flex-col h-screen min-h-0">
      <header className="shrink-0 min-h-9 flex items-center justify-between gap-2 px-3 sm:px-4 py-2 border-b border-warp-border bg-black">
        <span className="text-warp-muted text-xs font-medium truncate min-w-0">
          Nucleus AI - Google Knowledge Base
        </span>
        <div className="flex items-center gap-1.5 shrink-0">
          {hasConversation && (
            <button
              type="button"
              onClick={() => setImagesPanelOpen((o) => !o)}
              className="md:hidden text-warp-muted hover:text-warp-fg text-xs font-medium px-2 py-1.5 rounded border border-warp-border/60 hover:border-warp-border bg-transparent transition-colors"
            >
              Images{" "}
              {sidebarImagesWithBlock.length > 0 &&
                `(${sidebarImagesWithBlock.length})`}
            </button>
          )}
          <button
            type="button"
            onClick={handleNewChat}
            disabled={loading}
            className="text-warp-muted hover:text-warp-fg text-xs font-medium px-2 py-1 rounded border border-warp-border/60 hover:border-warp-border bg-transparent transition-colors disabled:opacity-50"
          >
            New chat
          </button>
        </div>
      </header>

      <div className="flex-1 flex min-h-0 relative bg-black">
        <div
          className={`flex-1 flex flex-col min-h-0 ${hasConversation ? "md:border-r md:border-warp-border md:w-[75%]" : ""}`}
        >
          {!error && hasConversation && (
            <div className="px-3 sm:px-4 pt-4">
              <p className="text-warp-fg text-base sm:text-lg font-medium tracking-wide text-center">
                Ask. Search. Know.
              </p>
            </div>
          )}
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto px-3 sm:px-4 py-4"
          >
            {!hasConversation && !error && (
              <div className="h-full flex items-center justify-center">
                <p className="text-warp-fg text-base sm:text-lg font-medium tracking-wide">
                  Ask. Search. Know.
                </p>
              </div>
            )}
            {blocks.map((block, i) => (
              <div key={block.id} data-block-index={i}>
                <TerminalBlock
                  blockIndex={i}
                  prompt={block.prompt}
                  response={block.response}
                  isLoading={block.isLoading}
                  onScrollToImages={() => setActiveBlockIndex(i)}
                />
              </div>
            ))}
            {error && <div className="text-warp-red text-sm py-2">{error}</div>}
          </div>

          <div className="border-t border-warp-border px-2 pr-4 flex items-start gap-2">
            <TerminalInput
              ref={inputRef}
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              placeholder="Ask anything..."
              loading={loading}
            />
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!input.trim() || loading}
              className="w-9 h-9 mt-2.5 flex items-center justify-center text-emerald-500 hover:text-emerald-400 disabled:opacity-35"
            >
              <span
                className={`inline-flex text-xl ${
                  sendAnimating
                    ? "-translate-y-8 opacity-0"
                    : arrowEntering
                      ? "translate-y-6 opacity-0"
                      : "translate-y-0 opacity-100"
                } transition-all duration-[350ms]`}
              >
                ↑
              </span>
            </button>
          </div>
        </div>

        <aside
          className={`fixed md:relative right-0 top-0 h-full border-l border-warp-border bg-black transition-transform ${
            hasConversation ? "md:translate-x-0 md:w-[25%]" : "hidden"
          }`}
        >
          <ImagesPanel
            images={sidebarImagesWithBlock}
            scrollToBlockIndex={activeBlockIndex}
            isLoading={loading}
          />
        </aside>
      </div>
    </div>
  );
}
