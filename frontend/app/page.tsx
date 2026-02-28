"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import {
  postQueryStream,
  type QueryResponse,
  type HistoryMessage,
  type ThinkingStep,
  type StreamEvent,
} from "@/lib/api";
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
  thinkingSteps: ThinkingStep[];
  branch: string | null;
  thinkingDurationMs: number | null;
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
      thinkingSteps?: ThinkingStep[];
      branch?: string | null;
      thinkingDurationMs?: number | null;
    }[];
    return parsed.map((b) => ({
      id: b.id,
      prompt: b.prompt,
      response: b.response,
      isLoading: false,
      thinkingSteps: b.thinkingSteps || [],
      branch: b.branch || null,
      thinkingDurationMs: b.thinkingDurationMs || null,
    }));
  } catch {
    return [];
  }
}

function saveBlocksToStorage(blocks: Block[]) {
  try {
    const toSave = blocks
      .filter((b) => b.response !== null)
      .map((b) => ({
        id: b.id,
        prompt: b.prompt,
        response: b.response!,
        thinkingSteps: b.thinkingSteps,
        branch: b.branch,
        thinkingDurationMs: b.thinkingDurationMs,
      }));
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
  } catch {}
}

function clearStorage() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {}
}

const SendArrowIcon = () => (
  <svg
    width="22"
    height="22"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M5 12L19 5L12 19L10 14L5 12Z" />
  </svg>
);

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
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const ratioRef = useRef<number[]>([]);
  const inputRef = useRef<TerminalInputHandle | null>(null);
  const hasHydratedRef = useRef(false);
  const didInitialScrollRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const ARROW_LAUNCH_MS = 550;

  useEffect(() => {
    const loaded = loadBlocksFromStorage();
    setBlocks(loaded);
  }, []);

  useEffect(() => {
    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true;
      return;
    }
    saveBlocksToStorage(blocks);
  }, [blocks]);

  useEffect(() => {
    if (
      blocks.length > 0 &&
      !didInitialScrollRef.current &&
      scrollRef.current
    ) {
      didInitialScrollRef.current = true;
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [blocks.length]);

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

  // ── Auto-scroll during streaming / typing ──
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const lastBlock = blocks[blocks.length - 1];
    if (!lastBlock) return;

    // Active while loading OR while response just arrived (typing animation)
    if (!lastBlock.isLoading && !lastBlock.response) return;

    const interval = setInterval(() => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distFromBottom < 150) {
        el.scrollTop = el.scrollHeight;
      }
    }, 80);

    // Stop after 30s max (covers longest typing animations)
    const timeout = setTimeout(() => clearInterval(interval), 30000);

    return () => {
      clearInterval(interval);
      clearTimeout(timeout);
    };
  }, [blocks]);

  const scrollToBottom = useCallback(
    (behavior: ScrollBehavior = "smooth") => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior,
      });
    },
    [],
  );

  const handleNewChat = useCallback(() => {
    abortRef.current?.abort();
    setBlocks([]);
    setError(null);
    setLoading(false);
    setImagesPanelOpen(false);
    clearStorage();
    didInitialScrollRef.current = false;
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

  const sendPrompt = useCallback(
    async (q: string) => {
      if (!q.trim() || loading) return;
      setSendAnimating(true);
      setInput("");
      setError(null);
      const id = nextId();
      const thinkingStartedAt = Date.now();

      setBlocks((prev) => [
        ...prev,
        {
          id,
          prompt: q,
          response: null,
          isLoading: true,
          thinkingSteps: [],
          branch: null,
          thinkingDurationMs: null,
        },
      ]);
      setLoading(true);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const history = buildHistory();
        let gotResult = false;

        await postQueryStream(
          q,
          history,
          (event: StreamEvent) => {
            if (event.type === "step") {
              setBlocks((prev) =>
                prev.map((b) => {
                  if (b.id !== id) return b;
                  const steps = [...b.thinkingSteps];
                  const idx = steps.findIndex((s) => s.id === event.data.id);
                  if (idx >= 0) {
                    steps[idx] = { ...steps[idx], ...event.data };
                  } else {
                    steps.push(event.data as ThinkingStep);
                  }
                  return { ...b, thinkingSteps: steps };
                }),
              );
            } else if (event.type === "branch") {
              setBlocks((prev) =>
                prev.map((b) =>
                  b.id === id ? { ...b, branch: event.data.name } : b,
                ),
              );
            } else if (event.type === "steps") {
              setBlocks((prev) =>
                prev.map((b) => {
                  if (b.id !== id) return b;
                  const steps = [...b.thinkingSteps];
                  for (const s of event.data.steps) {
                    if (!steps.find((e) => e.id === s.id)) {
                      steps.push({
                        id: s.id,
                        label: s.label,
                        status: "pending",
                      });
                    }
                  }
                  return { ...b, thinkingSteps: steps };
                }),
              );
            } else if (event.type === "result") {
              gotResult = true;
              const durationMs = Date.now() - thinkingStartedAt;
              setBlocks((prev) =>
                prev.map((b) =>
                  b.id === id
                    ? {
                        ...b,
                        response: event.data as QueryResponse,
                        isLoading: false,
                        thinkingDurationMs: durationMs,
                      }
                    : b,
                ),
              );
            }
          },
          controller.signal,
        );

        if (!gotResult) {
          setBlocks((prev) =>
            prev.map((b) =>
              b.id === id
                ? {
                    ...b,
                    isLoading: false,
                    response: {
                      query: q,
                      answer: "Connection lost. Please try again.",
                      sources: [],
                      images: [],
                    },
                  }
                : b,
            ),
          );
        }
      } catch (e) {
        if (e instanceof Error && e.name === "AbortError") return;
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
    },
    [loading, buildHistory],
  );

  const handleSubmit = useCallback(async () => {
    const q = input.trim();
    if (!q || loading) return;
    await sendPrompt(q);
  }, [input, loading, sendPrompt]);

  const goldenQuestions = [
    "When was Alphabet Inc. founded and why was it created?",
    "Who are the founders of Google?",
    "Which companies are subsidiaries of Alphabet Inc.?",
    "What is the relationship between Google and Alphabet Inc.?",
    "Where is Alphabet Inc. headquartered?",
  ];

  const handleGoldenEdit = (q: string) => {
    setInput(q);
  };
  const handleGoldenSend = (q: string) => {
    setInput(q);
    sendPrompt(q);
  };

  const hasConversation = blocks.length > 0;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowScrollBtn(distFromBottom > 120);
    };
    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

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
              className="text-warp-muted hover:text-warp-fg text-xs font-medium px-2 py-1.5 rounded border border-warp-border/60 hover:border-warp-border bg-transparent transition-colors"
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
      <div className="flex-1 flex min-h-0 relative bg-black overflow-hidden">
        <div
          className={`flex-1 flex flex-col min-h-0 transition-all duration-300 ${hasConversation && imagesPanelOpen ? "md:w-[75%]" : "w-full"}`}
        >
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto px-3 sm:px-4 py-4 relative"
          >
            {!hasConversation && !error && (
              <div className="h-full flex flex-col items-center justify-center gap-4">
                <p className="text-warp-fg text-base sm:text-lg font-medium tracking-wide text-center">
                  Ask. Search. Know.
                </p>
                <div className="flex flex-wrap justify-center gap-2">
                  {goldenQuestions.map((q) => (
                    <div
                      key={q}
                      className="flex items-center gap-1 rounded-full border border-warp-border/70 px-3 py-1 bg-black/40"
                    >
                      <button
                        type="button"
                        onClick={() => handleGoldenEdit(q)}
                        className="text-xs sm:text-[13px] text-warp-muted hover:text-warp-fg"
                      >
                        {q}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleGoldenSend(q)}
                        className="text-emerald-500 hover:text-emerald-400 flex items-center justify-center"
                        aria-label="Send"
                      >
                        <SendArrowIcon />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {hasConversation && (
              <>
                {blocks.map((block, i) => (
                  <div key={block.id} data-block-index={i}>
                    <TerminalBlock
                      prompt={block.prompt}
                      response={block.response}
                      isLoading={block.isLoading}
                      thinkingSteps={block.thinkingSteps}
                      branch={block.branch}
                      thinkingDurationMs={block.thinkingDurationMs}
                      onScrollToImages={() => setActiveBlockIndex(i)}
                    />
                  </div>
                ))}
                {error && (
                  <div className="text-warp-red text-sm py-2">{error}</div>
                )}
              </>
            )}
            {!hasConversation && error && (
              <div className="text-warp-red text-sm py-2">{error}</div>
            )}
            {showScrollBtn && (
              <button
                type="button"
                onClick={() => scrollToBottom("smooth")}
                className="fixed bottom-24 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-warp-border bg-black/80 text-warp-muted hover:text-warp-fg hover:border-warp-border text-xs transition-all shadow-lg backdrop-blur-sm"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <polyline points="19 12 12 19 5 12" />
                </svg>
                Scroll to bottom
              </button>
            )}
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
              className="w-9 h-9 mt-5 flex items-center justify-center text-emerald-500 hover:text-emerald-400 disabled:opacity-35"
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
        {hasConversation && imagesPanelOpen && (
          <aside className="absolute inset-y-0 right-0 w-[280px] sm:w-[300px] md:relative md:w-[25%] border-l border-warp-border bg-black z-40 flex flex-col">
            <div className="flex items-center justify-between px-3 py-2 border-b border-warp-border shrink-0 md:hidden">
              <span className="text-warp-muted text-xs font-medium">
                Images{" "}
                {sidebarImagesWithBlock.length > 0 &&
                  `(${sidebarImagesWithBlock.length})`}
              </span>
              <button
                type="button"
                onClick={() => setImagesPanelOpen(false)}
                className="text-warp-muted hover:text-warp-fg text-xs px-2 py-0.5 rounded border border-warp-border/60"
              >
                Close
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto">
              <ImagesPanel
                images={sidebarImagesWithBlock}
                scrollToBlockIndex={activeBlockIndex}
                isLoading={loading}
              />
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
