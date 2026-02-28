"use client";

import { useEffect, useRef, useState } from "react";
import type { QueryResponse, ThinkingStep } from "@/lib/api";
import { MarkdownContent } from "@/components/MarkdownContent";

const PROMPT_PREFIX = "you@nucleus ~ % ";
const INITIAL_SOURCES_VISIBLE = 4;

type TerminalBlockProps = {
  onScrollToImages?: () => void;
  prompt: string;
  response: QueryResponse | null;
  isLoading?: boolean;
  thinkingSteps?: ThinkingStep[];
  branch?: string | null;
  thinkingDurationMs?: number | null;
};

export function TerminalBlock({
  onScrollToImages,
  prompt,
  response,
  isLoading = false,
  thinkingSteps = [],
  branch,
  thinkingDurationMs,
}: TerminalBlockProps) {
  const [openSourceId, setOpenSourceId] = useState<string | null>(null);
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const [typedAnswer, setTypedAnswer] = useState("");
  const openSourceRef = useRef<HTMLLIElement>(null);
  const lastAnimatedAnswerRef = useRef<string | null>(null);

  // Thinking panel: expanded by default during loading, collapsed after
  const [thinkingExpanded, setThinkingExpanded] = useState(!response);
  const hasAutoCollapsedRef = useRef(!!response);

  useEffect(() => {
    if (response && !isLoading && !hasAutoCollapsedRef.current) {
      hasAutoCollapsedRef.current = true;
      const t = setTimeout(() => setThinkingExpanded(false), 400);
      return () => clearTimeout(t);
    }
  }, [response, isLoading]);

  useEffect(() => {
    if (!response?.answer) return;

    if (lastAnimatedAnswerRef.current === response.answer) {
      setTypedAnswer(response.answer);
      return;
    }

    lastAnimatedAnswerRef.current = response.answer;

    setTypedAnswer("");
    let i = 0;
    const text = response.answer;

    const id = setInterval(() => {
      i += 2;
      setTypedAnswer(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, 18);

    return () => clearInterval(id);
  }, [response?.answer]);

  useEffect(() => {
    if (openSourceId === null) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        openSourceRef.current &&
        !openSourceRef.current.contains(e.target as Node)
      ) {
        setOpenSourceId(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openSourceId]);

  const hasImages = response?.images && response.images.length > 0;
  const isPromptClickable = hasImages && onScrollToImages;
  const hasThinking = thinkingSteps.length > 0;

  const durationLabel = thinkingDurationMs
    ? `Thought for ${(thinkingDurationMs / 1000).toFixed(1)}s`
    : null;

  return (
    <div className="terminal-block rounded-r pl-3 pr-3 sm:pl-4 sm:pr-4 py-3 my-1 transition-colors">
      <div
        className={`flex flex-wrap items-baseline gap-1 ${
          isPromptClickable ? "cursor-pointer hover:bg-warp-surface/30" : ""
        }`}
        role={isPromptClickable ? "button" : undefined}
        tabIndex={isPromptClickable ? 0 : undefined}
        onClick={isPromptClickable ? onScrollToImages : undefined}
        onKeyDown={
          isPromptClickable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onScrollToImages?.();
                }
              }
            : undefined
        }
      >
        <span className="text-warp-green shrink-0">{PROMPT_PREFIX}</span>
        <span className="text-warp-fg break-words">{prompt}</span>
      </div>

      {/* ── Thinking Panel ── */}
      {(hasThinking || isLoading) && (
        <div className="mt-2">
          {/* Toggle button */}
          <button
            type="button"
            onClick={() => setThinkingExpanded((p) => !p)}
            className="flex items-center gap-1.5 text-xs text-warp-muted hover:text-warp-fg transition-colors select-none"
          >
            <span className="text-[10px] w-3 inline-flex justify-center">
              {thinkingExpanded ? "▾" : "▸"}
            </span>
            {isLoading ? (
              <>
                <span>Thinking</span>
                <span className="thinking-dots">
                  <span>.</span>
                  <span>.</span>
                  <span>.</span>
                </span>
              </>
            ) : (
              <span className="text-warp-muted/80">
                {durationLabel || "Thought"}
              </span>
            )}
          </button>

          {/* Collapsed pulsating bar during loading */}
          {!thinkingExpanded && isLoading && (
            <div className="mt-1.5 ml-4 h-[3px] w-20 rounded-full overflow-hidden bg-warp-border/40">
              <div className="h-full w-full bg-warp-accent/60 thinking-progress" />
            </div>
          )}

          {/* Expanded steps list */}
          {thinkingExpanded && (
            <div className="mt-1.5 ml-4 pl-3 border-l border-warp-border/40 space-y-1">
              {thinkingSteps.map((step) => (
                <div
                  key={step.id}
                  className="flex items-center gap-2 text-xs leading-5"
                >
                  {/* Status icon */}
                  {step.status === "pending" && (
                    <span className="w-3 text-center text-warp-muted/40 text-[11px]">
                      ○
                    </span>
                  )}
                  {step.status === "in_progress" && (
                    <span className="thinking-spinner" />
                  )}
                  {step.status === "done" && (
                    <span className="w-3 text-center text-warp-green text-[11px]">
                      ✓
                    </span>
                  )}
                  {step.status === "failed" && (
                    <span className="w-3 text-center text-warp-red text-[11px]">
                      ✗
                    </span>
                  )}

                  {/* Label */}
                  <span
                    className={
                      step.status === "failed"
                        ? "text-warp-muted/60 line-through"
                        : step.status === "pending"
                          ? "text-warp-muted/40"
                          : step.status === "in_progress"
                            ? "text-warp-fg"
                            : "text-warp-muted"
                    }
                  >
                    {step.label}
                  </span>

                  {/* Result badge (e.g. "→ Knowledge Base") */}
                  {step.result && (
                    <span className="text-warp-accent/80">
                      → {step.result}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Response ── */}
      {response && (
        <div className="mt-3 space-y-3 text-sm">
          <div className="flex flex-wrap items-baseline gap-2">
            <div className="min-w-0 flex-1 break-words leading-relaxed">
              <MarkdownContent
                content={typedAnswer}
                variant={
                  response.answer === "Not found in knowledge base."
                    ? "error"
                    : "default"
                }
              />
              {typedAnswer.length < response.answer.length && (
                <span className="cursor-blink">▌</span>
              )}
            </div>
          </div>

          {response.sources.length > 0 &&
            (() => {
              const total = response.sources.length;
              const showExpand =
                total > INITIAL_SOURCES_VISIBLE && !sourcesExpanded;
              const visibleSources = showExpand
                ? response.sources.slice(0, INITIAL_SOURCES_VISIBLE)
                : response.sources;

              return (
                <div className="mt-2 text-[11px]">
                  <div className="text-warp-muted font-medium mb-1.5">
                    Sources ({total})
                  </div>
                  <ul className="space-y-1">
                    {visibleSources.map((s) => {
                      const firstLine =
                        s.text.split(/\r?\n/)[0]?.trim().replace(/\s+/g, " ") ||
                        s.section;
                      const isOpen = openSourceId === s.page_id;

                      return (
                        <li
                          key={`${s.page_id}/${s.text}`}
                          ref={isOpen ? openSourceRef : undefined}
                          className="relative"
                        >
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              e.preventDefault();
                              setOpenSourceId((prev) =>
                                prev === s.page_id ? null : s.page_id,
                              );
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
                                <span className="text-warp-accent font-medium">
                                  [{s.page_id}]
                                </span>{" "}
                                <span className="text-warp-fg font-medium">
                                  {s.section}
                                </span>
                              </div>
                              <div className="px-5 py-4 text-[13px]">
                                <MarkdownContent
                                  content={s.text}
                                  variant="muted"
                                />
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
                        setOpenSourceId(null);
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
