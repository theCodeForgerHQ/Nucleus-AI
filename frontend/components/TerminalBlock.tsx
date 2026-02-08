"use client";

import { useState, useEffect, useRef } from "react";
import type { QueryResponse } from "@/lib/api";
import type { StreamStagePayload } from "@/lib/api";
import { MarkdownContent } from "@/components/MarkdownContent";

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
  blockIndex?: number;
  /** When user clicks "View images", scroll the images panel to this block's images */
  onScrollToImages?: () => void;
  prompt: string;
  response: QueryResponse | null;
  isStreaming?: boolean;
  /** Accumulated answer while streaming (tokens in real time) */
  streamingAnswer?: string;
  /** Real pipeline stage from backend; when set, this is shown instead of fallback steps */
  pipelineStage?: StreamStagePayload["stage"] | null;
};

export function TerminalBlock({
  blockIndex,
  onScrollToImages,
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

  const hasImages = response?.images && response.images.length > 0;
  const isBlockClickable = hasImages && onScrollToImages;

  return (
    <div
      className={`terminal-block rounded-r pl-3 pr-3 sm:pl-4 sm:pr-4 py-3 my-1 transition-colors ${
        isBlockClickable
          ? "cursor-pointer hover:bg-warp-surface/30"
          : ""
      }`}
      role={isBlockClickable ? "button" : undefined}
      tabIndex={isBlockClickable ? 0 : undefined}
      onClick={isBlockClickable ? onScrollToImages : undefined}
      onKeyDown={
        isBlockClickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onScrollToImages?.();
              }
            }
          : undefined
      }
    >
      {/* Command line */}
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-warp-green shrink-0">{PROMPT_PREFIX}</span>
        <span className="text-warp-fg break-words">{prompt}</span>
      </div>

      {/* Output: streaming tokens (live) or progress steps (before first token) */}
      {response === null && isStreaming && (
        <div className="mt-2 text-sm">
          {streamingAnswer ? (
            <div className="text-warp-fg break-words leading-relaxed">
              <MarkdownContent content={streamingAnswer} />
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
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1 break-words leading-relaxed">
              <MarkdownContent
                content={response.answer}
                variant={response.answer === "Not found in knowledge base." ? "error" : "default"}
              />
            </div>
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
                            e.preventDefault();
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
                          <div className="absolute bottom-full left-0 mb-2 w-[min(560px,92vw)] max-h-[55vh] overflow-y-auto overflow-x-hidden rounded-xl border border-warp-border/50 bg-warp-surface/40 backdrop-blur-xl shadow-2xl z-20 text-[12px] ring-1 ring-white/5">
                            <div className="sticky top-0 px-5 py-3.5 border-b border-warp-border/40 bg-warp-surface/30 backdrop-blur-md z-10 rounded-t-xl">
                              <span className="text-warp-accent font-medium">[{s.page_id}]</span>{" "}
                              <span className="text-warp-fg font-medium">
                                {s.section}
                              </span>
                            </div>
                            <div className="px-5 py-4 text-[13px]">
                              <MarkdownContent content={s.text} variant="muted" />
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
                    onClick={(e) => {
                      e.stopPropagation();
                      setSourcesExpanded((prev) => !prev);
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
