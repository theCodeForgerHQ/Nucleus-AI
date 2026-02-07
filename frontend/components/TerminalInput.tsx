"use client";

import { useRef, useEffect, useState, useCallback } from "react";

const PROMPT_PREFIX = "you@nucleus ~ % ";
const SPACE_AFTER_PREFIX_PX = 8;

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
  const mirrorRef = useRef<HTMLSpanElement>(null);
  const [cursorLeft, setCursorLeft] = useState(0);
  const [cursorWidth, setCursorWidth] = useState(8);
  const [atStart, setAtStart] = useState(true);

  const updateCaretPosition = useCallback(() => {
    const input = inputRef.current;
    const mirror = mirrorRef.current;
    if (!input || !mirror) return;
    const start = input.selectionStart ?? 0;
    setAtStart(start === 0);
    const s = value.substring(0, start);
    const inputStyle = getComputedStyle(input);
    mirror.style.font = inputStyle.font;
    mirror.style.letterSpacing = inputStyle.letterSpacing;
    mirror.textContent = "0";
    const chWidth = mirror.offsetWidth;
    mirror.textContent = s;
    setCursorWidth(Math.round(chWidth * 1.25));
    requestAnimationFrame(() => {
      const textWidth = mirror.offsetWidth;
      const left = SPACE_AFTER_PREFIX_PX + textWidth - chWidth - (input.scrollLeft || 0);
      setCursorLeft(Math.max(0, left));
    });
  }, [value]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [value]);

  useEffect(() => {
    updateCaretPosition();
  }, [value, updateCaretPosition]);

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
      </span>
      <div className="relative flex-1 min-w-0 flex items-baseline pl-2">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onKeyUp={updateCaretPosition}
          onMouseUp={updateCaretPosition}
          onClick={updateCaretPosition}
          onSelect={updateCaretPosition}
          onScroll={updateCaretPosition}
          disabled={disabled}
          placeholder={placeholder}
          className="w-full min-w-0 bg-transparent text-warp-fg placeholder-warp-muted outline-none font-mono text-[15px] caret-transparent py-0.5"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-label="Query input"
        />
        <span
          ref={mirrorRef}
          aria-hidden
          className="pointer-events-none invisible absolute top-0 font-mono text-[15px] whitespace-pre"
          style={{ left: SPACE_AFTER_PREFIX_PX, font: "inherit" }}
        />
        {atStart ? (
          <span
            className="cursor-blink absolute inline-block w-1.5 bg-warp-accent"
            style={{
              left: SPACE_AFTER_PREFIX_PX,
              top: "50%",
              transform: "translateY(-50%)",
              height: "1.15em",
            }}
            aria-hidden
          />
        ) : (
          <span
            className="cursor-blink absolute inline-block h-1 bg-warp-accent"
            style={{
              left: cursorLeft,
              width: cursorWidth,
              bottom: 0,
            }}
            aria-hidden
          />
        )}
      </div>
    </div>
  );
}
