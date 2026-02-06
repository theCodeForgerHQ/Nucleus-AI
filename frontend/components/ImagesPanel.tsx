"use client";

import type { QueryResponse } from "@/lib/api";

type ImagesPanelProps = {
  images: { url: string; page_id: string; caption: string | null }[];
};

export function ImagesPanel({ images }: ImagesPanelProps) {
  if (images.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-warp-muted text-sm px-2">
        Images from response appear here
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden flex flex-col gap-3 py-2 pr-2">
      {images.map((img, i) => (
        <a
          key={i}
          href={img.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded border border-warp-border overflow-hidden hover:border-warp-accent shrink-0"
        >
          <img
            src={img.url}
            alt={img.caption || "Source"}
            className="w-full h-auto object-cover"
          />
          {img.caption && (
            <div className="px-2 py-1.5 text-xs text-warp-muted truncate bg-warp-surface">
              {img.caption}
            </div>
          )}
        </a>
      ))}
    </div>
  );
}
