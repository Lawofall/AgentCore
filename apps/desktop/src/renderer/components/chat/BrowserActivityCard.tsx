import { IconButton } from "@/components/ui";
import { isBrowserTool } from "@/lib/browserActivity";
import { fetchWorkspaceFileBlob } from "@/services/workspace";
import { useBrowserSessionsStore } from "@/stores/browserSessions";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { BrowserDisplay, ProcessStep } from "@/types/events";
import {
  ChevronDown,
  ChevronRight,
  Globe,
  ImageOff,
  type LucideIcon,
  Monitor,
  Radio,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ThinkingDots } from "./message-bubble/Thinking";
import { TOOL_META } from "./message-bubble/constants";

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

/** A single browser step's action-verb chrome. Reuses the shared TOOL_META icon/label
 * (keyed `browser_<action>`) so the per-step verb and the tool row stay in sync; an
 * unknown (newer-backend) verb degrades to a Globe + a Title-cased label. */
function browserActionMeta(action: string): {
  Icon: LucideIcon;
  label: string;
} {
  const known = TOOL_META[`browser_${action}`];
  if (known) return known;
  const label = action
    ? action.charAt(0).toUpperCase() + action.slice(1)
    : "Step";
  return { Icon: Globe, label };
}

/** True when a tool-group is ≥2 consecutive browser steps → render as one activity card
 * (mirrors {@link isReadUrlSourceGroup}). A single browser step stays a normal ToolLine. */
export function isBrowserActivityGroup(tools: ToolStep[]): boolean {
  return tools.length >= 2 && tools.every((t) => isBrowserTool(t.tool_name));
}

/** Narrow a tool step's opaque `display` to a {@link BrowserDisplay} (the DURABLE card
 * data — never sourced from the live-only tool_use_progress). */
export function isBrowserDisplay(d: unknown): d is BrowserDisplay {
  if (!d) return false;
  const x = d as { kind?: unknown; action?: unknown; url?: unknown };
  return (
    x.kind === "browser" &&
    typeof x.action === "string" &&
    typeof x.url === "string"
  );
}

interface BrowserStepView {
  id: string;
  action: string;
  status: ToolStep["status"];
  url: string;
  title?: string;
  detail?: string;
  frame?: string;
}

/** Build the card's step models FROM each step's durable `display` (so a reload /
 * journal replay rebuilds the card verbatim). A step with no display yet (live, before
 * its tool_use_end) keeps a slot derived from the call — the verb from `browser_<action>`
 * and the url from the call arg — so the list doesn't jump when the display lands. */
function browserStepsFromTools(tools: ToolStep[]): BrowserStepView[] {
  return tools.map((t) => {
    if (isBrowserDisplay(t.display)) {
      return {
        id: t.id,
        action: t.display.action,
        status: t.status,
        url: t.display.url,
        title: t.display.title,
        detail: t.display.detail,
        frame: t.display.frame,
      };
    }
    const action = isBrowserTool(t.tool_name)
      ? t.tool_name.slice("browser_".length)
      : t.tool_name;
    const url = typeof t.arguments.url === "string" ? t.arguments.url : "";
    return { id: t.id, action, status: t.status, url };
  });
}

/** A one-line human label for a frame (lightbox caption / img alt). */
function frameAlt(step: {
  action: string;
  detail?: string;
  url?: string;
}): string {
  if (step.detail) return step.detail;
  const { label } = browserActionMeta(step.action);
  return step.url ? `${label} · ${step.url}` : label;
}

/**
 * Lazily fetch a conversation-workspace key-frame as an object URL (only mounts once the
 * card / row is expanded, so the jpeg is pulled on-demand — no thumbnail endpoint, the
 * original is fetched directly). Mirrors the IM ChatImageAttachment blob + objectURL +
 * revoke-on-unmount pattern. Returns `failed` when there is no frame or the fetch errored.
 */
function useWorkspaceFrame(
  conversationId: string | null,
  frame: string | undefined,
): { url: string | null; failed: boolean } {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!conversationId || !frame) {
      setFailed(true);
      setUrl(null);
      return;
    }
    setFailed(false);
    let active = true;
    let objectUrl: string | null = null;
    fetchWorkspaceFileBlob(conversationId, frame)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [conversationId, frame]);

  return { url, failed };
}

/** Compact key-frame thumbnail for a step row — click opens the full frame in a lightbox. */
function BrowserThumb({
  conversationId,
  frame,
  alt,
  onOpen,
}: {
  conversationId: string | null;
  frame: string;
  alt: string;
  onOpen: (src: string, alt: string) => void;
}) {
  const { url, failed } = useWorkspaceFrame(conversationId, frame);
  if (failed) {
    return (
      <div className="flex h-16 w-28 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-muted-foreground">
        <ImageOff size={16} />
      </div>
    );
  }
  if (!url) {
    return (
      <div className="h-16 w-28 shrink-0 animate-pulse rounded-lg bg-muted" />
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(url, alt)}
      className="h-16 w-28 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-border bg-muted/30 focus:outline-none focus:ring-2 focus:ring-ring"
      title={alt}
    >
      <img src={url} alt={alt} className="h-full w-full object-cover" />
    </button>
  );
}

/** Placeholder box for a step with no key-frame — keeps rows aligned with the action icon. */
function BrowserThumbPlaceholder({ Icon }: { Icon: LucideIcon }) {
  return (
    <div
      className="flex h-16 w-28 shrink-0 items-center justify-center rounded-lg border border-border border-dashed bg-muted/30 text-muted-foreground/60"
      aria-hidden
    >
      <Icon size={18} />
    </div>
  );
}

/** Full-resolution key-frame for a single-step browser result — click opens the lightbox. */
function BrowserResultFrame({
  conversationId,
  frame,
  alt,
  onOpen,
}: {
  conversationId: string | null;
  frame: string;
  alt: string;
  onOpen: (src: string, alt: string) => void;
}) {
  const { url, failed } = useWorkspaceFrame(conversationId, frame);
  if (failed) {
    return (
      <div className="flex h-40 items-center justify-center rounded-lg border border-border bg-muted text-muted-foreground">
        <ImageOff size={20} />
      </div>
    );
  }
  if (!url) {
    return <div className="h-40 w-full animate-pulse rounded-lg bg-muted" />;
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(url, alt)}
      className="block w-full cursor-zoom-in overflow-hidden rounded-lg border border-border bg-muted/30 focus:outline-none focus:ring-2 focus:ring-ring"
      title={alt}
    >
      <img
        src={url}
        alt={alt}
        className="mx-auto max-h-80 w-auto object-contain"
      />
    </button>
  );
}

/** Fullscreen key-frame viewer (lightbox). Close via the X button, Esc, or clicking the
 * backdrop — no zoom/pan (M0 keeps it minimal; a screenshot is already 1:1). */
function BrowserFrameLightbox({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt: string;
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
    // biome-ignore lint/a11y/useSemanticElements: a lightweight image lightbox overlay — role="dialog" + Esc/backdrop close is enough; a native <dialog> would add modal/form semantics we don't need.
    <div
      role="dialog"
      aria-modal="true"
      aria-label={alt || "浏览器关键帧"}
      className="fixed inset-0 z-50 flex flex-col bg-background/95"
    >
      <div className="flex h-12 shrink-0 items-center justify-between border-border border-b px-4">
        <span className="min-w-0 truncate text-sm text-muted-foreground">
          {alt}
        </span>
        <IconButton onClick={onClose} aria-label="关闭" title="关闭">
          <X size={16} />
        </IconButton>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="flex min-h-0 flex-1 cursor-zoom-out items-center justify-center overflow-auto p-6"
      >
        <img
          src={src}
          alt={alt}
          className="max-h-full max-w-full object-contain"
        />
      </button>
    </div>,
    document.body,
  );
}

/** One step row inside the activity card: key-frame thumbnail + action / detail / url. */
function BrowserStepRow({
  step,
  index,
  conversationId,
  onOpenFrame,
}: {
  step: BrowserStepView;
  index: number;
  conversationId: string | null;
  onOpenFrame: (src: string, alt: string) => void;
}) {
  const { Icon, label } = browserActionMeta(step.action);
  const alt = frameAlt(step);
  return (
    <div className="flex items-start gap-3 rounded-lg px-2 py-2">
      {step.frame ? (
        <BrowserThumb
          conversationId={conversationId}
          frame={step.frame}
          alt={alt}
          onOpen={onOpenFrame}
        />
      ) : (
        <BrowserThumbPlaceholder Icon={Icon} />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-sm">
          <span className="w-4 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
            {index + 1}
          </span>
          <Icon size={14} className="shrink-0 text-muted-foreground" />
          <span className="font-medium text-foreground">{label}</span>
          {step.status === "running" && <ThinkingDots />}
          {step.status === "error" && (
            <X size={13} className="shrink-0 text-destructive" />
          )}
        </div>
        {step.detail && (
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {step.detail}
          </p>
        )}
        {step.url && (
          <p className="mt-0.5 truncate text-xs text-muted-foreground/70">
            {step.url}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Merged view for a tool-group of ≥2 consecutive `browser_*` steps — the browser activity
 * card. Collapses to a bare「浏览器 · N 步」header (aligned with the read_url source
 * collection / tool-group chrome); expands into a step list (action / detail / url) with
 * lazy key-frame thumbnails, each opening the full frame in a lightbox.
 * Card data comes ONLY from each step's durable `display`, so it rebuilds on journal replay.
 * Reuses ReadUrlSourceCollection's `${turnKey}:tgrp:${groupKey}` disclosure key.
 */
export function BrowserActivityCard({
  tools,
  isStreaming,
  turnKey,
  groupKey,
  conversationId,
}: {
  tools: ToolStep[];
  isStreaming: boolean;
  turnKey?: string;
  groupKey?: string;
  conversationId: string | null;
}) {
  const [expanded, toggleExpanded] = useStreamAwareDisclosure(
    turnKey != null && groupKey != null ? `${turnKey}:tgrp:${groupKey}` : null,
    isStreaming,
  );
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(
    null,
  );
  const openFrame = useCallback(
    (src: string, alt: string) => setLightbox({ src, alt }),
    [],
  );
  const showBrowser = useSidePanelStore((s) => s.showBrowser);

  const steps = browserStepsFromTools(tools);
  const running = tools.some((t) => t.status === "running");
  const errorCount = tools.reduce(
    (n, t) => n + (t.status === "error" ? 1 : 0),
    0,
  );
  const count = steps.length;
  const title = `浏览器 · ${count} 步`;

  // browser_* 步进出现/结束 → 预 hydrate，打开坞时对齐 server session 页。
  const hydrateKey = tools.map((t) => `${t.id}:${t.status}`).join("|");
  useEffect(() => {
    if (!conversationId || !hydrateKey) return;
    void useBrowserSessionsStore.getState().hydrateConversation(conversationId);
  }, [conversationId, hydrateKey]);

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <button
          type="button"
          onClick={toggleExpanded}
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          {running ? (
            <ThinkingDots />
          ) : (
            <Monitor size={14} className="shrink-0" />
          )}
          <span className="min-w-0 truncate text-left">{title}</span>
          {errorCount > 0 && (
            <span className="shrink-0 rounded-full bg-destructive/10 px-1.5 text-xs font-normal text-destructive">
              {errorCount} failed
            </span>
          )}
          {expanded ? (
            <ChevronDown size={14} className="shrink-0" />
          ) : (
            <ChevronRight size={14} className="shrink-0" />
          )}
        </button>
        {/* 揭示右坞「浏览器」tab。**不按 running 收起**：接管默认只能在 turn 之间做，若入口只在
            运行中出现，用户就必须提前抢点开才有路子接管（沙箱在 turn 后仍存活 idle TTL，页面状态
            还在，正是最该上手的时刻）。跑着时是直播、停下后是最后一帧/接管入口，故文案随态切。 */}
        {conversationId && (
          <button
            type="button"
            onClick={showBrowser}
            className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/15"
          >
            <Radio size={12} className="shrink-0" />
            {running ? "查看直播" : "打开浏览器"}
          </button>
        )}
      </div>

      {expanded && (
        <div className="flex max-h-[28rem] flex-col gap-0.5 overflow-y-auto pr-1">
          {steps.map((s, i) => (
            <BrowserStepRow
              key={s.id}
              step={s}
              index={i}
              conversationId={conversationId}
              onOpenFrame={openFrame}
            />
          ))}
        </div>
      )}

      {lightbox && (
        <BrowserFrameLightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

/**
 * Single browser step's rich result (the ToolResultView branch for one `browser_*` call):
 * an action / detail / url header + the full key-frame (lazy-loaded, click → lightbox).
 * The aggregated form for ≥2 consecutive steps is {@link BrowserActivityCard}.
 */
export function BrowserResult({
  display,
  conversationId,
}: {
  display: BrowserDisplay;
  conversationId: string | null;
}) {
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(
    null,
  );
  const showBrowser = useSidePanelStore((s) => s.showBrowser);
  const { Icon, label } = browserActionMeta(display.action);
  const alt = frameAlt(display);

  useEffect(() => {
    if (!conversationId) return;
    void useBrowserSessionsStore.getState().hydrateConversation(conversationId);
  }, [conversationId]);

  return (
    <div className="mt-1 overflow-hidden rounded-lg border border-border">
      <div className="flex items-start gap-2 border-border/60 border-b bg-muted/40 px-2.5 py-1.5">
        <Icon size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5 text-xs">
            <span className="font-medium text-foreground">{label}</span>
            {display.title && (
              <span className="min-w-0 truncate text-muted-foreground">
                · {display.title}
              </span>
            )}
          </div>
          {display.detail && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {display.detail}
            </p>
          )}
          {display.url && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground/70">
              {display.url}
            </p>
          )}
        </div>
        {/* 单步富卡也挂入口：≥2 步活动卡已有 CTA，单步此前无路开浏览器 tab。
            无可靠 running 信号 → 固定「打开浏览器」（与活动卡 turn 结束后文案一致）。 */}
        {conversationId && (
          <button
            type="button"
            onClick={showBrowser}
            className="mt-0.5 flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/15"
          >
            <Radio size={12} className="shrink-0" />
            打开浏览器
          </button>
        )}
      </div>
      <div className="bg-muted/30 p-2">
        {display.frame ? (
          <BrowserResultFrame
            conversationId={conversationId}
            frame={display.frame}
            alt={alt}
            onOpen={(src, a) => setLightbox({ src, alt: a })}
          />
        ) : (
          <p className="px-1 py-2 text-xs text-muted-foreground/60">
            （无关键帧）
          </p>
        )}
      </div>
      {lightbox && (
        <BrowserFrameLightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

/** Collapsed ToolLine chip for one browser step — human detail, else page title, else url. */
export function browserResultTail(display: BrowserDisplay): string {
  return (display.detail || display.title || display.url).trim();
}

/** A compact one-line label for a frame / leftover peek (title row uses {@link browserResultTail}). */
export function browserResultPeek(display: BrowserDisplay): string {
  const { label } = browserActionMeta(display.action);
  const tail = browserResultTail(display);
  const line = tail ? `${label} · ${tail}` : label;
  return line.length > 140 ? `${line.slice(0, 140)}…` : line;
}
