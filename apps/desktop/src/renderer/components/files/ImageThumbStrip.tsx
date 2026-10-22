import { IconButton } from "@/components/ui";
import type { FileSource } from "@/lib/fileSource";
import { cn } from "@/lib/utils";
import { isVisualImageAttachment } from "@/services/messaging";
import { AlertCircle, ImageOff, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ImageLightbox, type LightboxImage } from "./ImageLightbox";

export type ImageThumbAttachment = {
  id: string;
  name: string;
  kind?: string | null;
  workspacePath?: string;
  fileBlob?: File;
  uploadState?: "uploading" | "error";
};

/**
 * Turn-level image thumbs (composer + user bubble). Click opens {@link ImageLightbox}.
 * Bytes: File blob first; else workspace `source.read` when a path is already resident.
 */
export function ImageThumbStrip({
  attachments,
  source = null,
  sourcePending = false,
  onRemove,
  align = "start",
  className,
}: {
  attachments: readonly ImageThumbAttachment[];
  source?: FileSource | null;
  /** True only while workspace ownership is still resolving. */
  sourcePending?: boolean;
  onRemove?: (id: string) => void;
  align?: "start" | "end";
  className?: string;
}) {
  const images = attachments.filter(isVisualImageAttachment);
  const srcById = useRef<Record<string, string>>({});
  const [lightbox, setLightbox] = useState<{
    slides: LightboxImage[];
    index: number;
  } | null>(null);

  if (images.length === 0) return null;

  const openAt = (id: string, src: string) => {
    srcById.current[id] = src;
    const slides: LightboxImage[] = [];
    let index = 0;
    for (const att of images) {
      const url = att.id === id ? src : srcById.current[att.id];
      if (!url) continue;
      if (att.id === id) index = slides.length;
      slides.push({ src: url, name: att.name });
    }
    setLightbox({ slides, index });
  };

  return (
    <div
      data-testid="image-thumb-strip"
      className={cn(
        "flex flex-wrap gap-2",
        align === "end" ? "justify-end" : "justify-start",
        className,
      )}
    >
      {images.map((att) => (
        <ImageThumb
          key={att.id}
          att={att}
          source={source}
          sourcePending={sourcePending}
          onReady={(src) => {
            srcById.current[att.id] = src;
          }}
          onOpen={(src) => openAt(att.id, src)}
          onRemove={onRemove}
        />
      ))}
      {lightbox && (
        <ImageLightbox
          images={lightbox.slides}
          index={lightbox.index}
          onClose={() => setLightbox(null)}
          onIndexChange={
            lightbox.slides.length > 1
              ? (index) =>
                  setLightbox((prev) => (prev ? { ...prev, index } : prev))
              : undefined
          }
        />
      )}
    </div>
  );
}

function ImageThumb({
  att,
  source,
  sourcePending,
  onReady,
  onOpen,
  onRemove,
}: {
  att: ImageThumbAttachment;
  source: FileSource | null;
  sourcePending: boolean;
  onReady: (src: string) => void;
  onOpen: (src: string) => void;
  onRemove?: (id: string) => void;
}) {
  const { src, status } = useAttachmentImageSrc(att, source, sourcePending);
  const uploading = att.uploadState === "uploading";
  const uploadFailed = att.uploadState === "error";

  useEffect(() => {
    if (src) onReady(src);
  }, [src, onReady]);

  return (
    <div className="group/img relative size-16 shrink-0">
      {status === "ready" && src ? (
        <button
          type="button"
          onClick={() => onOpen(src)}
          onMouseDown={(e) => e.preventDefault()}
          aria-label={`查看 ${att.name}`}
          className="size-16 cursor-zoom-in overflow-hidden rounded-lg border border-border bg-muted"
        >
          <img
            src={src}
            alt={att.name}
            className="size-full object-cover"
            draggable={false}
          />
        </button>
      ) : (
        <div
          role="img"
          aria-label={att.name}
          className="flex size-16 items-center justify-center rounded-lg border border-border bg-muted text-muted-foreground"
        >
          {status === "loading" || uploading ? (
            <Loader2 size={16} className="animate-spin" aria-hidden />
          ) : (
            <ImageOff size={16} aria-hidden />
          )}
        </div>
      )}
      {(uploading || uploadFailed) && status === "ready" && (
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-lg bg-background/50">
          {uploading ? (
            <Loader2
              size={16}
              className="animate-spin text-muted-foreground"
              aria-hidden
            />
          ) : (
            <AlertCircle
              size={16}
              className="text-muted-foreground"
              aria-hidden
            />
          )}
        </span>
      )}
      {onRemove && (
        <IconButton
          onMouseDown={(e) => e.preventDefault()}
          onClick={(e) => {
            e.stopPropagation();
            onRemove(att.id);
          }}
          aria-label="移除附件"
          className="absolute -top-1 -right-1 size-5 rounded-full bg-muted text-muted-foreground hover:bg-destructive hover:text-destructive-foreground"
        >
          <X size={11} />
        </IconButton>
      )}
    </div>
  );
}

function useAttachmentImageSrc(
  att: ImageThumbAttachment,
  source: FileSource | null,
  sourcePending: boolean,
): { src: string | null; status: "loading" | "ready" | "failed" } {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!att.fileBlob) {
      setBlobUrl(null);
      return;
    }
    const url = URL.createObjectURL(att.fileBlob);
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [att.fileBlob]);

  const [remote, setRemote] = useState<string | null>(null);
  const [remoteStatus, setRemoteStatus] = useState<
    "idle" | "loading" | "ready" | "failed"
  >("idle");

  useEffect(() => {
    if (att.fileBlob) return;
    const path = att.workspacePath;
    if (!path || !source) {
      setRemote(null);
      setRemoteStatus(path && sourcePending ? "loading" : "failed");
      return;
    }
    let cancelled = false;
    setRemoteStatus("loading");
    void source
      .read(path)
      .then((result) => {
        if (cancelled) return;
        if (result.kind === "image") {
          setRemote(result.dataUrl);
          setRemoteStatus("ready");
        } else {
          setRemote(null);
          setRemoteStatus("failed");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRemote(null);
          setRemoteStatus("failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [att.fileBlob, att.workspacePath, source, sourcePending]);

  if (blobUrl) return { src: blobUrl, status: "ready" };
  if (remoteStatus === "ready" && remote) {
    return { src: remote, status: "ready" };
  }
  if (remoteStatus === "failed" || (!att.fileBlob && !att.workspacePath)) {
    return { src: null, status: "failed" };
  }
  return { src: null, status: "loading" };
}
