"use client";

import { useState, useEffect } from "react";
import type { QueryResponse } from "@/lib/api";

const PROMPT_PREFIX = "you@nucleus ~ % ";

const PROGRESS_STEPS = [
  "Searching knowledge base…",
  "Fetching context…",
  "Reranking results…",
  "Generating answer…",
  "Almost there…",
];

type TerminalBlockProps = {
  prompt: string;
  response: QueryResponse | null;
  isStreaming?: boolean;
  /** Accumulated answer while streaming (tokens in real time) */
  streamingAnswer?: string;
};

export function TerminalBlock({
  prompt,
  response,
  isStreaming = false,
  streamingAnswer = "",
}: TerminalBlockProps) {
  const [stepIndex, setStepIndex] = useState(0);

  useEffect(() => {
    if (!isStreaming || response !== null) return;
    const id = setInterval(() => {
      setStepIndex((i) => (i + 1) % PROGRESS_STEPS.length);
    }, 1600);
    return () => clearInterval(id);
  }, [isStreaming, response]);

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
              <span>{PROGRESS_STEPS[stepIndex]}</span>
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
                {response.sources.map((s, i) => (
                  <li key={i} className="group relative">
                    {/* One line per source: transparent/glass bg */}
                    <div className="text-xs px-2.5 py-1.5 rounded border border-warp-border/60 bg-warp-surface/40 backdrop-blur-sm cursor-default truncate">
                      <span className="text-warp-accent">[{s.page_id}]</span>{" "}
                      <span className="text-warp-fg">{s.section}</span>
                    </div>
                    {/* Hover popover above: full content, glass bg */}
                    <div className="absolute bottom-full left-0 mb-1.5 w-full min-w-[280px] max-w-md max-h-56 overflow-y-auto rounded border border-warp-border bg-warp-surface/90 backdrop-blur-md shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-opacity duration-150 z-20 pointer-events-none group-hover:pointer-events-auto">
                      <div className="p-3 text-xs sticky top-0 border-b border-warp-border/60 bg-warp-surface/80 backdrop-blur-sm">
                        <span className="text-warp-accent">[{s.page_id}]</span>{" "}
                        <span className="text-warp-fg font-medium">
                          {s.section}
                        </span>
                      </div>
                      <div className="p-3 text-warp-muted text-xs leading-relaxed whitespace-pre-wrap">
                        {s.text}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
