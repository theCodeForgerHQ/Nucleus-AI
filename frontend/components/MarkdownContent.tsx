"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const markdownComponents: Components = {
  p: ({ children }) => (
    <p className="mb-2 last:mb-0 text-warp-fg leading-relaxed">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-warp-fg">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-warp-fg">{children}</em>,
  code: ({ className, children, ...props }) => {
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      return (
        <pre className="my-2 p-3 rounded border border-warp-border bg-warp-surface overflow-x-auto text-[13px]">
          <code className="text-warp-fg" {...props}>
            {children}
          </code>
        </pre>
      );
    }
    return (
      <code
        className="px-1.5 py-0.5 rounded bg-warp-surface border border-warp-border/60 text-warp-accent text-[13px]"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  ul: ({ children }) => (
    <ul className="list-disc list-inside mb-2 space-y-0.5 text-warp-fg">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-2 space-y-0.5 text-warp-fg">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href ?? ""}
      target="_blank"
      rel="noopener noreferrer"
      className="text-warp-accent hover:underline"
    >
      {children}
    </a>
  ),
  h1: ({ children }) => (
    <h1 className="text-lg font-semibold text-warp-fg mt-3 mb-1 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-base font-semibold text-warp-fg mt-2 mb-1">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-sm font-semibold text-warp-fg mt-2 mb-0.5">
      {children}
    </h3>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-warp-border pl-3 my-2 text-warp-muted italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-warp-border my-2" />,
};

type MarkdownContentProps = {
  content: string;
  variant?: "default" | "muted" | "error";
};

export function MarkdownContent({
  content,
  variant = "default",
}: MarkdownContentProps) {
  const isMuted = variant === "muted";
  const isError = variant === "error";

  return (
    <div
      className={
        isMuted
          ? "text-warp-muted text-[13px] leading-relaxed [&_strong]:text-warp-fg [&_a]:text-warp-accent"
          : isError
            ? "text-warp-red [&_*]:text-warp-red"
            : ""
      }
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
