"use client";

import { useMemo } from "react";

/** 5×7 pixel font – each letter is 5 wide, 7 tall (rows) */
const FONT: Record<string, number[][]> = {
  N: [
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 1, 0, 0, 1],
    [1, 0, 1, 0, 1],
    [1, 0, 0, 1, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
  ],
  U: [
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [0, 1, 1, 1, 0],
  ],
  C: [
    [0, 1, 1, 1, 0],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 1],
    [0, 1, 1, 1, 0],
  ],
  L: [
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 1, 1, 1, 1],
  ],
  E: [
    [1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 1, 1, 1, 0],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [1, 1, 1, 1, 1],
  ],
  S: [
    [0, 1, 1, 1, 1],
    [1, 0, 0, 0, 0],
    [1, 0, 0, 0, 0],
    [0, 1, 1, 1, 0],
    [0, 0, 0, 0, 1],
    [0, 0, 0, 0, 1],
    [1, 1, 1, 1, 0],
  ],
  A: [
    [0, 0, 1, 0, 0],
    [0, 1, 0, 1, 0],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
    [1, 1, 1, 1, 1],
    [1, 0, 0, 0, 1],
    [1, 0, 0, 0, 1],
  ],
  I: [
    [1, 1, 1, 1, 1],
    [0, 0, 1, 0, 0],
    [0, 0, 1, 0, 0],
    [0, 0, 1, 0, 0],
    [0, 0, 1, 0, 0],
    [0, 0, 1, 0, 0],
    [1, 1, 1, 1, 1],
  ],
  " ": [
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
  ],
};

const CHAR_WIDTH = 5;
const CHAR_GAP = 1;
const LETTER_SPACING = CHAR_WIDTH + CHAR_GAP;

function buildPixelMap(line1: string, line2: string): { row: number; col: number; index: number }[] {
  const pixels: { row: number; col: number; index: number }[] = [];
  let index = 0;

  function addLine(line: string, rowOffset: number) {
    let col = 0;
    for (const char of line) {
      const glyph = FONT[char] ?? FONT[" "];
      for (let r = 0; r < glyph.length; r++) {
        for (let c = 0; c < CHAR_WIDTH; c++) {
          if (glyph[r][c]) {
            pixels.push({ row: rowOffset + r, col: col + c, index: index++ });
          }
        }
      }
      col += LETTER_SPACING;
    }
  }

  addLine(line1, 0);
  addLine(line2, 8);
  return pixels;
}

const PIXELS = buildPixelMap("NUCLEUS", "AI");
const STAGGER_MS = 12;
const CELL_PX = 8;
const CELL_PX_COMPACT = 5;
const MAX_COL = Math.max(...PIXELS.map((p) => p.col)) + 1;
const MAX_ROW = Math.max(...PIXELS.map((p) => p.row)) + 1;

type PixelLogoProps = {
  /** When true, smaller and left-aligned for corner above first query (no movement animation) */
  compact?: boolean;
};

export function PixelLogo({ compact = false }: PixelLogoProps) {
  const pixelList = useMemo(() => PIXELS, []);
  const cellPx = compact ? CELL_PX_COMPACT : CELL_PX;
  const gap = compact ? 1 : 2;

  return (
    <div
      className={
        compact
          ? "flex flex-col items-start py-2 pb-1"
          : "flex flex-col items-center justify-center py-8 sm:py-10 px-2"
      }
      role="img"
      aria-label="Nucleus AI"
    >
      <div
        className="inline-grid"
        style={{
          gridTemplateColumns: `repeat(${MAX_COL}, ${cellPx}px)`,
          gridTemplateRows: `repeat(${MAX_ROW}, ${cellPx}px)`,
          gap,
        }}
      >
        {pixelList.map(({ row, col, index }) => (
          <div
            key={`${row}-${col}`}
            className="rounded-sm bg-[var(--warp-accent)] border border-[rgba(56,189,248,0.7)] shadow-[0_0_6px_rgba(56,189,248,0.35)]"
            style={{
              gridRow: row + 1,
              gridColumn: col + 1,
              width: cellPx,
              height: cellPx,
              animation: compact ? "none" : "pixel-in 0.2s ease-out forwards",
              animationDelay: compact ? undefined : `${index * (STAGGER_MS / 1000)}s`,
              opacity: compact ? 1 : 0,
              transform: compact ? undefined : "scale(0)",
            }}
          />
        ))}
      </div>
    </div>
  );
}
