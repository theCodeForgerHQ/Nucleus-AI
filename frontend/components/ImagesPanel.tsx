"use client";

import { useState, useRef, useEffect } from "react";

export type ImageWithBlock = {
  url: string;
  page_id: string;
  caption: string | null;
};

type ImagesPanelProps = {
  images: ImageWithBlock[];
  scrollToBlockIndex?: number | null;
  isLoading?: boolean;
};

function ImageSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={`image-skeleton rounded border border-warp-border/60 shrink-0 ${className ?? ""}`}
      aria-hidden
    />
  );
}

export function ImagesPanel({
  images,
  scrollToBlockIndex = null,
  isLoading = false,
}: ImagesPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollToBlockIndex == null || !scrollRef.current) return;
    const first = scrollRef.current.querySelector(
      `[data-block-index="${scrollToBlockIndex}"]`,
    );
    first?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [scrollToBlockIndex]);

  if (images.length === 0) {
    if (isLoading) {
      return (
        <div className="h-full overflow-y-auto overflow-x-hidden flex flex-col gap-3 py-2 pr-2">
          <ImageSkeleton className="w-full aspect-[4/3] min-h-[80px]" />
          <ImageSkeleton className="w-full aspect-[3/4] min-h-[100px]" />
          <ImageSkeleton className="w-full aspect-[16/10] min-h-[70px]" />
        </div>
      );
    }
    return (
      <div className="h-full flex items-center justify-center text-warp-muted text-sm px-2">
        Images from response appear here
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="h-full overflow-y-auto overflow-x-hidden flex flex-col gap-3 py-2 pr-2"
    >
      {images.map((img, i) => (
        <div key={`${img.page_id}-${i}`} data-block-index={img.page_id}>
          <ImageCard img={img} />
        </div>
      ))}
    </div>
  );
}

function ImageCard({ img }: { img: ImageWithBlock }) {
  const [loaded, setLoaded] = useState(false);

  return (
    <a
      href={img.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded border border-warp-border overflow-hidden hover:border-warp-accent shrink-0 group"
    >
      <div className="relative w-full bg-warp-bg min-h-[60px] flex items-center justify-center">
        {!loaded && (
          <div
            className="absolute inset-0 image-skeleton rounded-none"
            aria-hidden
          />
        )}
        <img
          src={img.url}
          alt={img.caption || "Source"}
          className={`max-w-full w-auto h-auto object-contain transition-opacity duration-300 ${
            loaded ? "opacity-100" : "opacity-0"
          }`}
          onLoad={() => setLoaded(true)}
        />
      </div>
      {img.caption && (
        <div className="px-2 py-1.5 text-xs text-warp-muted truncate bg-warp-bg">
          {img.caption}
        </div>
      )}
    </a>
  );
}
