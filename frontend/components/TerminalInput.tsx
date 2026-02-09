"use client";

import { useRef, useEffect, useState, useCallback, useLayoutEffect, forwardRef, useImperativeHandle } from "react";

const PROMPT_PREFIX = "you@nucleus ~ % ";
const MIN_TEXTAREA_HEIGHT_PX = 40;
const MAX_TEXTAREA_HEIGHT_PX = 200;

export type TerminalInputHandle = { focus: () => void };

type TerminalInputProps = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
  loading?: boolean;
};

export const TerminalInput = forwardRef<TerminalInputHandle, TerminalInputProps>(function TerminalInput(
  {
    value,
    onChange,
    onSubmit,
    disabled = false,
    placeholder = "",
    loading = false,
  },
  ref
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const cursorSpanRef = useRef<HTMLSpanElement>(null);
  const [cursorOffset, setCursorOffset] = useState(0);
  const [cursorPos, setCursorPos] = useState<{ left: number; top: number } | null>(null);

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }), []);

  const updateCursorOffset = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    setCursorOffset(el.selectionStart ?? 0);
  }, []);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    const onDocumentClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (textareaRef.current?.contains(target)) return;
      if ((target as Element).closest?.("a, button, [contenteditable], [role='button']")) return;
      textareaRef.current?.focus();
    };
    document.addEventListener("click", onDocumentClick);
    return () => document.removeEventListener("click", onDocumentClick);
  }, []);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const onSelect = () => setCursorOffset(el.selectionStart ?? 0);
    el.addEventListener("select", onSelect);
    return () => el.removeEventListener("select", onSelect);
  }, []);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(
      Math.max(el.scrollHeight, MIN_TEXTAREA_HEIGHT_PX),
      MAX_TEXTAREA_HEIGHT_PX
    );
    el.style.height = `${newHeight}px`;
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT_PX ? "auto" : "hidden";
  }, [value]);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    const mirror = mirrorRef.current;
    const span = cursorSpanRef.current;
    if (!textarea || !mirror || !span) {
      setCursorPos(null);
      return;
    }
    const style = getComputedStyle(textarea);
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    const paddingTop = parseFloat(style.paddingTop) || 10;
    const left = paddingLeft + span.offsetLeft - textarea.scrollLeft;
    const top = paddingTop + span.offsetTop - textarea.scrollTop;
    setCursorPos({ left, top });
  }, [value, cursorOffset]);

  const prevLoadingRef = useRef(loading);
  useEffect(() => {
    if (prevLoadingRef.current && !loading) {
      textareaRef.current?.focus();
    }
    prevLoadingRef.current = loading;
  }, [loading]);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange(e.target.value);
    setCursorOffset(e.target.selectionStart ?? 0);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim()) {
        onSubmit();
        requestAnimationFrame(() => textareaRef.current?.focus());
      }
    }
  };

  const handleSelect = () => updateCursorOffset();
  const handleKeyUp = () => updateCursorOffset();
  const handleClick = () => updateCursorOffset();

  const atStart = cursorOffset === 0;

  return (
    <div className="flex items-start gap-0 w-full min-h-[3rem] sm:min-h-[3.5rem] py-3 px-3 sm:py-4 sm:px-4 rounded-lg">
      <span className="text-warp-green shrink-0 select-none pt-2.5">
        {PROMPT_PREFIX}
      </span>
      <div className="relative flex-1 min-w-0 flex min-h-[2.5rem]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onKeyUp={handleKeyUp}
          onClick={handleClick}
          onSelect={handleSelect}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          className="w-full min-w-0 min-h-[2.5rem] max-h-[200px] resize-none overflow-y-auto bg-transparent text-warp-fg placeholder-warp-muted outline-none font-mono text-[15px] py-2.5 pr-2 pl-0 leading-relaxed caret-transparent"
          style={{ minHeight: MIN_TEXTAREA_HEIGHT_PX, maxHeight: MAX_TEXTAREA_HEIGHT_PX }}
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-label="Query input"
        />
        <div
          ref={mirrorRef}
          aria-hidden
          className="pointer-events-none invisible absolute left-0 top-0 w-full overflow-hidden font-mono text-[15px] leading-relaxed whitespace-pre-wrap break-words py-2.5 pr-2 pl-0"
          style={{ visibility: "hidden", minHeight: MIN_TEXTAREA_HEIGHT_PX }}
        >
          {value.slice(0, cursorOffset)}
          <span ref={cursorSpanRef} className="inline">
            _
          </span>
        </div>
        {atStart ? (
          <span
            className="cursor-blink absolute inline-block w-1.5 bg-warp-accent"
            style={{
              left: 0,
              top: "50%",
              transform: "translateY(-50%)",
              height: "1.15em",
              marginTop: "-0.075em",
            }}
            aria-hidden
          />
        ) : cursorPos ? (
          <span
            className="cursor-blink absolute inline-block bg-warp-accent"
            style={{
              left: cursorPos.left,
              top: cursorPos.top,
              marginTop: "0.5em",
              width: "0.6em",
              height: "0.32em",
              backgroundColor: "var(--warp-accent)",
            }}
            aria-hidden
          />
        ) : null}
      </div>
    </div>
  );
});
