import { IconButton } from "@/components/ui";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useCallback, useEffect } from "react";
import { createPortal } from "react-dom";

export type LightboxImage = {
  src: string;
  name: string;
};

/**
 * Shared image lightbox: contain-fit, Esc / backdrop / X close, no zoom chrome.
 * Multi-image next/prev matches IM gallery keys (arrows), without download.
 */
export function ImageLightbox({
  images,
  index,
  onClose,
  onIndexChange,
}: {
  images: readonly LightboxImage[];
  index: number;
  onClose: () => void;
  onIndexChange?: (index: number) => void;
}) {
  const current = images[index];
  const multi = images.length > 1 && Boolean(onIndexChange);

  const goPrev = useCallback(() => {
    if (!multi || !onIndexChange || images.length === 0) return;
    onIndexChange((index - 1 + images.length) % images.length);
  }, [index, images.length, multi, onIndexChange]);

  const goNext = useCallback(() => {
    if (!multi || !onIndexChange || images.length === 0) return;
    onIndexChange((index + 1) % images.length);
  }, [index, images.length, multi, onIndexChange]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (!multi) return;
      if (e.key === "ArrowLeft") goPrev();
      if (e.key === "ArrowRight") goNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, multi, goPrev, goNext]);

  if (!current) return null;

  const label = multi
    ? `${current.name} · ${index + 1}/${images.length}`
    : current.name || "图片";

  return createPortal(
    // biome-ignore lint/a11y/useSemanticElements: lightweight image lightbox — role="dialog" + Esc/backdrop close; native <dialog> would add modal/form semantics we don't need.
    <div
      role="dialog"
      aria-modal="true"
      aria-label={label}
      data-testid="image-lightbox"
      className="fixed inset-0 z-50 flex flex-col bg-background/95"
    >
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-border border-b px-4">
        <span className="min-w-0 truncate text-sm text-muted-foreground">
          {label}
        </span>
        <IconButton onClick={onClose} aria-label="关闭" title="关闭">
          <X size={16} />
        </IconButton>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center">
        <button
          type="button"
          aria-label="关闭"
          className="absolute inset-0 cursor-zoom-out"
          onClick={onClose}
        />
        {multi && (
          <IconButton
            size="md"
            onClick={(e) => {
              e.stopPropagation();
              goPrev();
            }}
            aria-label="上一张"
            title="上一张"
            className="absolute left-3 z-10 bg-background/80"
          >
            <ChevronLeft size={18} />
          </IconButton>
        )}
        <div className="relative z-[1] flex max-h-full max-w-full items-center justify-center p-6">
          <img
            src={current.src}
            alt={current.name}
            className="max-h-[calc(100vh-5rem)] max-w-full object-contain"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          />
        </div>
        {multi && (
          <IconButton
            size="md"
            onClick={(e) => {
              e.stopPropagation();
              goNext();
            }}
            aria-label="下一张"
            title="下一张"
            className="absolute right-3 z-10 bg-background/80"
          >
            <ChevronRight size={18} />
          </IconButton>
        )}
      </div>
    </div>,
    document.body,
  );
}
