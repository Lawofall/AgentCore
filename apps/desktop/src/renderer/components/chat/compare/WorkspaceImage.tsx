import type { FileSource } from "@/lib/fileSource";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * 从工作区源读取一张图片并内联展示（compare fence 专用）。
 * 失败时显示占位，不降级为外链。
 */
export function WorkspaceImage({
  source,
  path,
  alt,
}: {
  source: FileSource | null;
  path: string;
  alt: string;
}) {
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDataUrl(null);
    setFailed(false);
    if (!source) {
      setFailed(true);
      return;
    }
    void (async () => {
      try {
        const result = await source.read(path);
        if (cancelled) return;
        if (result.kind === "image") {
          setDataUrl(result.dataUrl);
        } else {
          setFailed(true);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, path]);

  if (failed) {
    return (
      <div className="flex min-h-32 items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 px-3 py-6 text-center text-xs text-muted-foreground">
        无法加载工作区图片
        <span className="mt-1 block font-mono text-xs opacity-70">{path}</span>
      </div>
    );
  }

  if (!dataUrl) {
    return (
      <div className="flex min-h-32 items-center justify-center rounded-lg border border-border bg-muted/20">
        <Loader2 size={18} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <img
      src={dataUrl}
      alt={alt}
      className="max-h-[min(60vh,28rem)] w-full object-contain"
      draggable={false}
    />
  );
}
