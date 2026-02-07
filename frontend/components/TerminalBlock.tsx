"use client";

import { useState, useEffect, useRef } from "react";
import type { QueryResponse } from "@/lib/api";
import type { StreamStagePayload } from "@/lib/api";

const PROMPT_PREFIX = "you@nucleus ~ % ";

/** Fallback steps when backend doesn't send stage yet (cycle on timer) */
const FALLBACK_STEPS = [
  "Searching knowledge base…",
  "Fetching context…",
  "Reranking results…",
  "Generating answer…",
  "Almost there…",
];

const STAGE_LABELS: Record<StreamStagePayload["stage"], string> = {
  searching: "Searching knowledge base…",
  fetching_context: "Fetching context…",
  reranking: "Reranking results…",
  fetching_images: "Fetching images…",
  generating: "Generating answer…",
};

const INITIAL_SOURCES_VISIBLE = 4;

type TerminalBlockProps = {
  prompt: string;
  response: QueryResponse | null;
  isStreaming?: boolean;
  /** Accumulated answer while streaming (tokens in real time) */
  streamingAnswer?: string;
  /** Real pipeline stage from backend; when set, this is shown instead of fallback steps */
  pipelineStage?: StreamStagePayload["stage"] | null;
};

export function TerminalBlock({
  prompt,
  response,
  isStreaming = false,
  streamingAnswer = "",
  pipelineStage = null,
}: TerminalBlockProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [openSourceIndex, setOpenSourceIndex] = useState<number | null>(null);
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const openSourceRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (!isStreaming || response !== null || pipelineStage != null) return;
    const id = setInterval(() => {
      setStepIndex((i) => (i + 1) % FALLBACK_STEPS.length);
    }, 1600);
    return () => clearInterval(id);
  }, [isStreaming, response, pipelineStage]);

  useEffect(() => {
    if (openSourceIndex === null) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (openSourceRef.current && !openSourceRef.current.contains(e.target as Node)) {
        setOpenSourceIndex(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openSourceIndex]);

  const statusLabel =
    pipelineStage != null
      ? STAGE_LABELS[pipelineStage]
      : FALLBACK_STEPS[stepIndex];

  return (
    <div className="terminal-block rounded-r pl-4 pr-4 py-3 my-1">
      {/* Command line */}
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-warp-green shrink-0">{PROMPT_PREFIX}</span>
        <span className="text-warp-fg break-words">{prompt}</span>
      </div>

      {/* Output: streaming tokens (live) or progress steps (before first token) */}
      {response === null && isStreaming && (
        <div className="mt-2 text-sm">
          {streamingAnswer ? (
            <div className="text-warp-fg whitespace-pre-wrap break-words leading-relaxed">
              {streamingAnswer}
              <span className="cursor-blink">▌</span>
            </div>
          ) : (
            <div className="text-warp-muted flex items-center gap-2">
              <span className="cursor-blink">▌</span>
              <span>{statusLabel}</span>
            </div>
          )}
        </div>
      )}

      {response && (
        <div className="mt-3 space-y-3 text-sm">
          <div
            className={
              response.answer === "Not found in knowledge base."
                ? "text-warp-red whitespace-pre-wrap break-words leading-relaxed"
                : "text-warp-fg whitespace-pre-wrap break-words leading-relaxed"
            }
          >
            {response.answer}
          </div>
          {response.sources.length > 0 && (() => {
            const total = response.sources.length;
            const showExpand = total > INITIAL_SOURCES_VISIBLE && !sourcesExpanded;
            const visibleSources = showExpand
              ? response.sources.slice(0, INITIAL_SOURCES_VISIBLE)
              : response.sources;
            return (
              <div className="mt-2 text-[11px]">
                <div className="text-warp-muted font-medium mb-1.5">
                  Sources ({total})
                </div>
                <ul className="space-y-1">
                  {visibleSources.map((s, i) => {
                    const firstLine =
                      s.text.split(/\r?\n/)[0]?.trim().replace(/\s+/g, " ") ||
                      s.section;
                    const isOpen = openSourceIndex === i;
                    return (
                      <li
                        key={i}
                        ref={isOpen ? openSourceRef : undefined}
                        className="relative"
                      >
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenSourceIndex((prev) => (prev === i ? null : i));
                          }}
                          className="w-full text-left px-2 py-1 rounded border border-warp-border/60 bg-warp-surface/40 hover:bg-warp-surface/60 hover:border-warp-border cursor-pointer flex items-center gap-1 min-w-0 transition-colors"
                        >
                          <span className="text-warp-accent shrink-0">
                            [{s.page_id}]
                          </span>
                          <span className="text-warp-fg truncate min-w-0">
                            {firstLine}
                          </span>
                        </button>
                        {isOpen && (
                          <div className="absolute bottom-full left-0 mb-1.5 w-[min(380px,85vw)] max-h-[45vh] overflow-y-auto overflow-x-hidden rounded border border-warp-border bg-warp-surface/95 backdrop-blur-md shadow-xl z-20 text-[11px]">
                            <div className="p-3 sticky top-0 border-b border-warp-border/60 bg-warp-surface/95 backdrop-blur-sm z-10">
                              <span className="text-warp-accent">[{s.page_id}]</span>{" "}
                              <span className="text-warp-fg font-medium">
                                {s.section}
                              </span>
                            </div>
                            <div className="p-3 text-warp-muted leading-relaxed whitespace-pre-wrap">
                              {s.text}
                            </div>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {total > INITIAL_SOURCES_VISIBLE && (
                  <button
                    type="button"
                    onClick={() => {
                      setSourcesExpanded((e) => !e);
                      setOpenSourceIndex(null);
                    }}
                    className="mt-1.5 text-warp-accent hover:underline focus:outline-none"
                  >
                    {sourcesExpanded
                      ? "Show less"
                      : `View all ${total} sources`}
                  </button>
                )}
              </div>
            );
          })()}
        </div>
      )}

    </div>
  );
}
