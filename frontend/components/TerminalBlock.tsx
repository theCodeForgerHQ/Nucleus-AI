"use client";

import { useState, useEffect } from "react";
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

  useEffect(() => {
    if (!isStreaming || response !== null || pipelineStage != null) return;
    const id = setInterval(() => {
      setStepIndex((i) => (i + 1) % FALLBACK_STEPS.length);
    }, 1600);
    return () => clearInterval(id);
  }, [isStreaming, response, pipelineStage]);

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
          {response.sources.length > 0 && (
            <div className="mt-2">
              <div className="text-warp-muted text-xs font-medium mb-1.5">
                Sources ({response.sources.length})
              </div>
              <ul className="space-y-1.5">
                {response.sources.map((s, i) => {
                  const firstLine =
                    s.text.split(/\r?\n/)[0]?.trim().replace(/\s+/g, " ") ||
                    s.section;
                  return (
                    <li key={i} className="group relative">
                      {/* One line per source: first line of content, truncated to fit */}
                      <div className="text-xs px-2.5 py-1.5 rounded border border-warp-border/60 bg-warp-surface/40 backdrop-blur-sm cursor-default flex items-center gap-1 min-w-0">
                        <span className="text-warp-accent shrink-0">
                          [{s.page_id}]
                        </span>
                        <span className="text-warp-fg truncate min-w-0">
                          {firstLine}
                        </span>
                      </div>
                      {/* Hover popover: wide horizontal panel, single view; scroll only when content overflows */}
                      <div className="absolute bottom-full left-0 mb-1.5 w-[min(90vw,1100px)] min-w-[280px] max-h-[75vh] overflow-y-auto overflow-x-hidden rounded border border-warp-border bg-warp-surface/95 backdrop-blur-md shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-opacity duration-150 z-20 pointer-events-none group-hover:pointer-events-auto">
                        <div className="p-4 text-xs sticky top-0 border-b border-warp-border/60 bg-warp-surface/95 backdrop-blur-sm z-10">
                          <span className="text-warp-accent">[{s.page_id}]</span>{" "}
                          <span className="text-warp-fg font-medium">
                            {s.section}
                          </span>
                        </div>
                        <div className="p-4 text-warp-muted text-sm leading-relaxed whitespace-pre-wrap">
                          {s.text}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
