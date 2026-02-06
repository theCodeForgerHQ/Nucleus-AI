"use client";

import type { QueryResponse } from "@/lib/api";

const PROMPT_PREFIX = "you@nucleus ~ % ";

type TerminalBlockProps = {
  prompt: string;
  response: QueryResponse | null;
  isStreaming?: boolean;
};

export function TerminalBlock({
  prompt,
  response,
  isStreaming = false,
}: TerminalBlockProps) {
  return (
    <div className="terminal-block rounded-r pl-4 pr-4 py-3 my-1">
      {/* Command line */}
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-warp-green shrink-0">{PROMPT_PREFIX}</span>
        <span className="text-warp-fg break-words">{prompt}</span>
      </div>

      {/* Output */}
      {response === null && isStreaming && (
        <div className="mt-2 text-warp-muted flex items-center gap-2">
          <span className="cursor-blink">▌</span>
          <span>Thinking...</span>
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
            <details className="text-warp-muted">
              <summary className="cursor-pointer hover:text-warp-accent">
                Sources ({response.sources.length})
              </summary>
              <ul className="mt-2 space-y-2 pl-4 border-l border-warp-border">
                {response.sources.map((s, i) => (
                  <li key={i} className="text-xs">
                    <span className="text-warp-accent">[{s.page_id}]</span>{" "}
                    {s.section}
                    <div className="text-warp-muted mt-0.5 line-clamp-2">
                      {s.text}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {response.images.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {response.images.map((img, i) => (
                <a
                  key={i}
                  href={img.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded border border-warp-border overflow-hidden hover:border-warp-accent max-w-[200px]"
                >
                  <img
                    src={img.url}
                    alt={img.caption || "Source"}
                    className="w-full h-auto object-cover"
                  />
                  {img.caption && (
                    <div className="px-2 py-1 text-xs text-warp-muted truncate">
                      {img.caption}
                    </div>
                  )}
                </a>
              ))}
            </div>
          )}
        </div>
      )}

    </div>
  );
}
