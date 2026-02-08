"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import {
  postQueryStream,
  type QueryResponse,
  type HistoryMessage,
  type StreamStagePayload,
} from "@/lib/api";
import { TerminalBlock } from "@/components/TerminalBlock";
import { TerminalInput, type TerminalInputHandle } from "@/components/TerminalInput";
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

function clearStorage() {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
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
  const [imagesPanelOpen, setImagesPanelOpen] = useState(false);
  const [sendAnimating, setSendAnimating] = useState(false);
  const [stopButtonEntering, setStopButtonEntering] = useState(false);
  const [arrowEntering, setArrowEntering] = useState(false);
  const ratioRef = useRef<number[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<TerminalInputHandle | null>(null);
  /** Only allow saving after we've had blocks (from load or user); avoids saving [] on first paint before load runs */
  const allowSaveRef = useRef(false);

  const ARROW_LAUNCH_MS = 550;
  const SLIDE_ANIMATION_MS = 350;

  // After arrow "launches" (moves up and disappears), clear sendAnimating so Stop button can slide up
  useEffect(() => {
    if (!sendAnimating) return;
    const t = setTimeout(() => setSendAnimating(false), ARROW_LAUNCH_MS);
    return () => clearTimeout(t);
  }, [sendAnimating]);

  // When we switch to showing Stop button (loading && !sendAnimating), animate it in from below
  useEffect(() => {
    if (!loading || sendAnimating) return;
    setStopButtonEntering(true);
    const t = setTimeout(() => setStopButtonEntering(false), 50);
    return () => clearTimeout(t);
  }, [loading, sendAnimating]);

  // When loading ends, animate arrow back in from below
  useEffect(() => {
    if (loading) return;
    setArrowEntering(true);
    const t = setTimeout(() => setArrowEntering(false), 50);
    return () => clearTimeout(t);
  }, [loading]);

  // Restore conversation from localStorage after mount (client-only)
  useEffect(() => {
    setBlocks(loadBlocksFromStorage());
  }, []);

  // Allow persistence only after blocks have been populated (from load or first message)
  useEffect(() => {
    if (blocks.length > 0) allowSaveRef.current = true;
  }, [blocks]);

  const handleNewChat = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
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
    const controller = new AbortController();
    abortControllerRef.current = controller;
    const history = buildHistory();

    postQueryStream(
      q,
      history,
      {
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
        onAbort: () => {
          setBlocks((prev) =>
            prev.map((b) =>
              b.id === id
                ? {
                    ...b,
                    response: {
                      query: q,
                      answer: b.streamingAnswer || "(Stopped)",
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
      },
      controller.signal
    );
  }, [input, loading, buildHistory]);

  const handleStopGenerating = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
  }, []);

  // Ctrl+C / Cmd+C stops generation when streaming
  useEffect(() => {
    if (!loading) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
        e.preventDefault();
        handleStopGenerating();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [loading, handleStopGenerating]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [blocks]);

  // Persist completed blocks to localStorage so conversation survives refresh
  useEffect(() => {
    if (!allowSaveRef.current) return;
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

  // All images cumulative: oldest query at top (chronological), so scroll direction matches chat
  // Scroll up in chat → scroll up in panel to older images; scroll down → newer images
  const sidebarImagesWithBlock: { url: string; page_id: string; caption: string | null; blockIndex: number }[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const imgs = blocks[i].response?.images ?? [];
    for (const img of imgs) {
      sidebarImagesWithBlock.push({ ...img, blockIndex: i });
    }
  }

  // No split until user has sent at least one message; then smooth transition to 75/25
  const hasConversation = blocks.length > 0;

  return (
    <div className="flex flex-col h-screen min-h-0">
      {/* Top bar - Warp style minimal; on mobile: compact title + Images toggle when conversation */}
      <header className="shrink-0 min-h-9 flex items-center justify-between gap-2 px-3 sm:px-4 py-2 border-b border-warp-border bg-warp-surface">
        <span className="text-warp-muted text-xs font-medium truncate min-w-0">
          Nucleus AI
        </span>
        <div className="flex items-center gap-1.5 shrink-0">
          {hasConversation && (
            <button
              type="button"
              onClick={() => setImagesPanelOpen((o) => !o)}
              className="md:hidden text-warp-muted hover:text-warp-fg text-xs font-medium px-2 py-1.5 rounded border border-warp-border/60 hover:border-warp-border bg-transparent transition-colors"
              title="Toggle images panel"
              aria-label={imagesPanelOpen ? "Close images" : "Open images"}
            >
              Images {sidebarImagesWithBlock.length > 0 && `(${sidebarImagesWithBlock.length})`}
            </button>
          )}
          <button
            type="button"
            onClick={handleNewChat}
            disabled={loading}
            className="text-warp-muted hover:text-warp-fg text-xs font-medium px-2 py-1 rounded border border-warp-border/60 hover:border-warp-border bg-transparent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Start a new chat (clears current conversation)"
          >
            New chat
          </button>
        </div>
      </header>

      {/* Mobile: backdrop when images panel is open */}
      {imagesPanelOpen && (
        <button
          type="button"
          className="md:hidden fixed inset-0 z-20 bg-black/50 transition-opacity"
          onClick={() => setImagesPanelOpen(false)}
          aria-label="Close images panel"
        />
      )}

      {/* Left column: chat + input bar (shrinks when images panel open). Right: images panel. */}
      <div className="flex-1 flex min-h-0 relative">
        {/* Chat column: scrollable area + input bar at bottom; full width on mobile, 75% on md+ when conversation */}
        <div
          className={`min-w-0 flex-1 flex flex-col min-h-0 transition-[width] duration-300 ease-out w-full ${
            hasConversation ? "md:w-[75%] md:border-r md:border-warp-border" : ""
          }`}
        >
          <div
            ref={scrollRef}
            className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-3 sm:px-4 py-4"
          >
            {blocks.length === 0 && (
              <div className="text-warp-muted text-sm py-6 sm:py-8 px-1 max-w-xl mx-auto text-center">
                <p>Ask a question. Answers are based on your knowledge base.</p>
                <p className="mt-2 text-warp-accent">
                  Type below and press Enter to query.
                </p>
              </div>
            )}
            {blocks.map((block, i) => (
              <div key={block.id}>
                <div
                  ref={(el) => {
                    blockRefs.current[i] = el;
                  }}
                  data-block-index={i}
                >
                  <TerminalBlock
                    blockIndex={i}
                    onScrollToImages={() => setActiveBlockIndex(i)}
                    prompt={block.prompt}
                    response={block.response}
                    isStreaming={block.isStreaming}
                    streamingAnswer={block.streamingAnswer}
                    pipelineStage={block.pipelineStage}
                  />
                </div>
                {i < blocks.length - 1 && (
                  <div className="block-separator" aria-hidden />
                )}
              </div>
            ))}
            {error && (
              <div className="text-warp-red text-sm py-2" role="alert">
                {error}
              </div>
            )}
          </div>

          {/* Input bar: inside chat column so it never goes under the images panel; button aligned to top (first line) */}
          <div className="shrink-0 flex items-start gap-1 sm:gap-2 border-t border-warp-border bg-warp-bg pl-2 pr-4 sm:pl-0 sm:pr-4 min-h-0">
            <div className="flex-1 min-w-0">
              <TerminalInput
                ref={inputRef}
                value={input}
                onChange={setInput}
                onSubmit={handleSubmit}
                disabled={false}
                placeholder="Ask anything..."
                loading={loading}
              />
            </div>
            {/* Send button: show when idle or while arrow is still "launching"; then Stop slides up */}
            {(!loading || sendAnimating) ? (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!input.trim() || loading}
                className="shrink-0 flex items-center justify-center w-9 h-9 text-emerald-500 hover:text-emerald-400 transition-colors disabled:opacity-35 disabled:cursor-not-allowed mt-[22px] sm:mt-[26px] overflow-hidden"
                title="Send"
                aria-label="Send message"
              >
                <span
                  className={`inline-flex ease-out ${
                    sendAnimating
                      ? "transition-all -translate-y-8 opacity-0"
                      : arrowEntering
                        ? "transition-all duration-[350ms] translate-y-6 opacity-0"
                        : "transition-all duration-[350ms] translate-y-0 opacity-100"
                  }`}
                  aria-hidden
                  style={sendAnimating ? { transitionDuration: `${ARROW_LAUNCH_MS}ms` } : undefined}
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                    <path d="M12 19V5m0 0l-7 7m7-7l7 7" />
                  </svg>
                </span>
              </button>
            ) : (
              <div className="shrink-0 flex items-center justify-center min-w-9 h-9 mt-[22px] sm:mt-[26px]">
                <button
                  type="button"
                  onClick={handleStopGenerating}
                  className={`shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded border border-warp-border bg-warp-surface font-mono text-sm text-warp-red hover:bg-warp-surface/80 hover:border-warp-red/50 transition-all duration-[350ms] ease-out mr-3 ${
                    stopButtonEntering ? "translate-y-6 opacity-0" : "translate-y-0 opacity-100"
                  }`}
                  title="Stop generating"
                  aria-label="Stop generating"
                >
                  <span className="w-2 h-2 rounded-sm shrink-0 bg-warp-red" />
                  <span>^C</span>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Images sidebar: on mobile = overlay when open; on md+ = inline 25% when has conversation */}
        <aside
          className={`flex flex-col bg-warp-surface overflow-hidden transition-[transform,width] duration-300 ease-out
            fixed md:relative top-0 right-0 z-30 h-full border-l border-warp-border
            ${!hasConversation ? "w-0 max-w-0 invisible pointer-events-none" : "w-[min(100%,320px)] md:w-[25%] md:min-w-0 md:max-w-none"}
            ${!hasConversation ? "translate-x-full" : imagesPanelOpen ? "translate-x-0" : "translate-x-full md:translate-x-0"}
            shadow-xl md:shadow-none`}
          aria-hidden={!hasConversation}
        >
          <div className="shrink-0 flex items-center justify-between px-3 py-2 border-b border-warp-border">
            <span className="text-warp-muted text-xs font-medium whitespace-nowrap">
              Images
            </span>
            <button
              type="button"
              onClick={() => setImagesPanelOpen(false)}
              className="md:hidden text-warp-muted hover:text-warp-fg p-1 rounded"
              aria-label="Close images panel"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
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
