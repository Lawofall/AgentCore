import { Button, IconButton } from "@/components/ui";
import {
  type FileNode,
  type FilePreviewResult,
  isArchiveFileName,
} from "@/lib/fileSource";
import { formatBytes } from "@/lib/format";
import { Download, ExternalLink, FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Shared body renderer for a {@link FilePreviewResult} — the source-agnostic
 * inner view used by both file UIs (文件中枢统一). Renders text (with an optional
 * truncation banner), an inline image (contain + lightbox), PDF iframe, or a
 * non-previewable fallback (binary / too-large). Surrounding chrome (header,
 * download / edit actions) belongs to the caller; this draws content only.
 *
 * 图 / PDF 不画 mime·体积底栏、不挂缩放铬条：内联已经认出内容，点图进灯箱。
 * 例外是 binary / too-large 的兜底面：看不见文件，面板内主按钮是出路（空白画布上
 * 顶栏图标不好找）。有按钮就不再复述按钮；过大阈值折进标题；octet-stream 不展示，
 * 体积留下帮人决定要不要外开。截断横幅仍告诉你眼前不是全文。
 */
export function FilePreviewBody({
  result,
  name,
  onOpenWithOsDefaultApp,
  onDownload,
  onOpenEntry,
}: {
  result: FilePreviewResult;
  name: string;
  onOpenWithOsDefaultApp?: () => void;
  onDownload?: () => void;
  onOpenEntry?: (entry: FileNode) => void;
}) {
  if (result.kind === "text") {
    return (
      <div className="flex h-full flex-col">
        {result.truncated && (
          <div className="shrink-0 border-b border-border bg-muted/40 px-4 py-1.5 text-xs text-muted-foreground">
            内容较大，仅显示前一部分，完整内容请下载查看。
          </div>
        )}
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-relaxed text-foreground">
          {result.text}
        </pre>
      </div>
    );
  }

  if (result.kind === "image") {
    return <ImagePreviewBody dataUrl={result.dataUrl} name={name} />;
  }

  if (result.kind === "dir") {
    if (result.entries.length === 0) {
      return (
        <div className="flex h-full items-center justify-center px-4 text-sm text-muted-foreground">
          空文件夹
        </div>
      );
    }
    return (
      <ul className="h-full overflow-auto px-3 py-2 text-sm">
        {result.entries.map((entry) => (
          <li key={entry.path}>
            {onOpenEntry ? (
              <button
                type="button"
                onClick={() => onOpenEntry(entry)}
                className="w-full truncate rounded-md px-2 py-1 text-left text-foreground hover:bg-accent"
              >
                {entry.isDir ? `${entry.name}/` : entry.name}
              </button>
            ) : (
              <span className="block truncate px-2 py-1">
                {entry.isDir ? `${entry.name}/` : entry.name}
              </span>
            )}
          </li>
        ))}
      </ul>
    );
  }

  if (result.kind === "pdf") {
    return (
      <iframe
        src={result.dataUrl}
        title={name}
        className="h-full w-full border-0 bg-muted/20"
      />
    );
  }

  // binary | too-large — not previewable inline.
  const title = fallbackTitle(result, name);
  const meta = fallbackMeta(result);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
      <FileText size={26} className="text-muted-foreground/40" />
      <p className="text-sm text-muted-foreground">{title}</p>
      {(onOpenWithOsDefaultApp || onDownload) && (
        <div className="mt-1 flex items-center gap-2">
          {onOpenWithOsDefaultApp && (
            <Button
              onClick={onOpenWithOsDefaultApp}
              icon={<ExternalLink size={13} />}
            >
              用默认程序打开
            </Button>
          )}
          {onDownload && (
            <Button
              variant={onOpenWithOsDefaultApp ? "neutral" : "primary"}
              onClick={onDownload}
              icon={<Download size={13} />}
            >
              下载
            </Button>
          )}
        </div>
      )}
      {meta && <p className="text-xs text-muted-foreground/60">{meta}</p>}
    </div>
  );
}

function fallbackTitle(
  result: Extract<FilePreviewResult, { kind: "binary" | "too-large" }>,
  name: string,
): string {
  if (result.kind === "too-large") return "文件过大";
  const reason = result.reason;
  if (reason?.startsWith("图片过大") || reason?.startsWith("PDF 过大")) {
    return reason.replace(/，请下载或用系统默认程序打开。?$/, "");
  }
  if (isArchiveFileName(name)) return "这是压缩包";
  return "无法预览此文件";
}

function fallbackMeta(
  result: Extract<FilePreviewResult, { kind: "binary" | "too-large" }>,
): string | null {
  if (result.kind !== "binary") return null;
  const mime =
    result.mime && result.mime !== "application/octet-stream"
      ? result.mime
      : null;
  const size = result.size != null ? formatBytes(result.size) : null;
  if (mime && size) return `${mime} · ${size}`;
  return mime ?? size;
}

function ImagePreviewBody({
  dataUrl,
  name,
}: {
  dataUrl: string;
  name: string;
}) {
  const [lightbox, setLightbox] = useState(false);

  return (
    <div className="flex h-full items-center justify-center overflow-auto p-4">
      <button
        type="button"
        className="cursor-zoom-in border-0 bg-transparent p-0"
        onClick={() => setLightbox(true)}
        aria-label={`放大预览 ${name}`}
      >
        <img
          src={dataUrl}
          alt={name}
          className="max-h-full max-w-full object-contain"
          draggable={false}
        />
      </button>
      {lightbox && (
        <ImageLightbox
          dataUrl={dataUrl}
          name={name}
          onClose={() => setLightbox(false)}
        />
      )}
    </div>
  );
}

function ImageLightbox({
  dataUrl,
  name,
  onClose,
}: {
  dataUrl: string;
  name: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    // biome-ignore lint/a11y/useSemanticElements: lightweight image lightbox — role="dialog" + Esc/backdrop close; native <dialog> would add modal/form semantics we don't need.
    <div
      role="dialog"
      aria-modal="true"
      aria-label={name || "图片"}
      className="fixed inset-0 z-50 flex flex-col bg-background/95"
    >
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-border border-b px-4">
        <span className="min-w-0 truncate text-sm text-muted-foreground">
          {name}
        </span>
        <IconButton onClick={onClose} aria-label="关闭" title="关闭">
          <X size={16} />
        </IconButton>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="flex min-h-0 flex-1 cursor-zoom-out items-center justify-center overflow-auto p-6"
        aria-label="关闭"
      >
        <img
          src={dataUrl}
          alt={name}
          className="max-h-full max-w-full object-contain"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        />
      </button>
    </div>,
    document.body,
  );
}
