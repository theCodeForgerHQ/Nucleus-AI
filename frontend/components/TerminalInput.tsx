"use client";

import { useRef, useEffect } from "react";

const PROMPT_PREFIX = "you@nucleus ~ %";

type TerminalInputProps = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
};

export function TerminalInput({
  value,
  onChange,
  onSubmit,
  disabled = false,
  placeholder = "",
}: TerminalInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim()) onSubmit();
    }
  };

  return (
    <div className="flex items-baseline gap-0 w-full border-t border-warp-border bg-warp-bg py-3 px-4">
      <span className="text-warp-green shrink-0 select-none">
        {PROMPT_PREFIX}
        {" "}
      </span>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder={placeholder}
        className="flex-1 min-w-0 bg-transparent text-warp-fg placeholder-warp-muted outline-none font-mono text-[15px]"
        autoFocus
        autoComplete="off"
        spellCheck={false}
        aria-label="Query input"
      />
      <span
        className="cursor-blink shrink-0 inline-block w-3 h-0.5 bg-warp-accent ml-0.5"
        aria-hidden
      />
    </div>
  );
}
