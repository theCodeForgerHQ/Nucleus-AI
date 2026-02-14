"use client";

import {
  useRef,
  useEffect,
  useState,
  useCallback,
  useLayoutEffect,
  forwardRef,
  useImperativeHandle,
} from "react";

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

export const TerminalInput = forwardRef<
  TerminalInputHandle,
  TerminalInputProps
>(function TerminalInput(
  {
    value,
    onChange,
    onSubmit,
    disabled = false,
    placeholder = "",
    loading = false,
  },
  ref,
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const cursorSpanRef = useRef<HTMLSpanElement>(null);
  const rafRef = useRef<number | null>(null);

  const [cursorOffset, setCursorOffset] = useState(0);
  const [cursorPos, setCursorPos] = useState<{
    left: number;
    top: number;
  } | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      focus: () => textareaRef.current?.focus(),
    }),
    [],
  );

  const updateCursorOffset = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    setCursorOffset(el.selectionStart ?? 0);
  }, []);

  useEffect(() => {
    const onGlobalKeyDown = (e: KeyboardEvent) => {
      if (disabled) return;
      if (textareaRef.current && document.activeElement === textareaRef.current)
        return;
      const target = e.target as Element;
      if (target?.closest?.("input, textarea, [contenteditable], select"))
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key.length === 1 || e.key === "Backspace") {
        textareaRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onGlobalKeyDown);
    return () => document.removeEventListener("keydown", onGlobalKeyDown);
  }, [disabled]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const onSelect = () => setCursorOffset(el.selectionStart ?? 0);
    el.addEventListener("select", onSelect);
    return () => el.removeEventListener("select", onSelect);
  }, []);

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(
      Math.max(el.scrollHeight, MIN_TEXTAREA_HEIGHT_PX),
      MAX_TEXTAREA_HEIGHT_PX,
    );
    el.style.height = `${newHeight}px`;
    el.style.overflowY =
      el.scrollHeight > MAX_TEXTAREA_HEIGHT_PX ? "auto" : "hidden";
  }, [value]);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    const mirror = mirrorRef.current;
    const span = cursorSpanRef.current;
    if (!textarea || !mirror || !span) {
      setCursorPos(null);
      return;
    }

    if (rafRef.current) cancelAnimationFrame(rafRef.current);

    rafRef.current = requestAnimationFrame(() => {
      const style = getComputedStyle(textarea);
      const paddingLeft = parseFloat(style.paddingLeft) || 0;
      const paddingTop = parseFloat(style.paddingTop) || 0;
      const left = paddingLeft + span.offsetLeft - textarea.scrollLeft;
      const top = paddingTop + span.offsetTop - textarea.scrollTop;
      setCursorPos({ left, top });
    });

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
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

  return (
    <div className="flex items-start gap-0 w-full min-h-[3rem] sm:min-h-[3.5rem] py-3 px-3 sm:py-4 sm:px-4 rounded-lg">
      <span className="text-warp-green shrink-0 select-none pt-2.5 mr-2">
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
          style={{
            minHeight: MIN_TEXTAREA_HEIGHT_PX,
            maxHeight: MAX_TEXTAREA_HEIGHT_PX,
          }}
          autoComplete="off"
          spellCheck={false}
          aria-label="Query input"
        />
        <div
          ref={mirrorRef}
          aria-hidden
          className="pointer-events-none invisible absolute left-0 top-0 w-full overflow-hidden font-mono text-[15px] leading-relaxed whitespace-pre-wrap break-words py-2.5 pr-2 pl-0"
          style={{ minHeight: MIN_TEXTAREA_HEIGHT_PX }}
        >
          {value.slice(0, cursorOffset)}
          <span ref={cursorSpanRef} className="inline">
            _
          </span>
        </div>
        {cursorPos && (
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
        )}
      </div>
    </div>
  );
});
