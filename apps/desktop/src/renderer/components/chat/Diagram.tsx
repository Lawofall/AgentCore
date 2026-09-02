import { IconButton } from "@/components/ui";
import { copyText } from "@/lib/clipboard";
import { clampScale, fitContainView } from "@/lib/diagramView";
import {
  inlineMermaidBoxPx,
  mermaidInlineMaxHeightPx,
} from "@/lib/inlineMermaidBox";
import { mermaidRenderConfig } from "@/lib/mermaidConfig";
import { normalizeMermaidSource } from "@/lib/mermaidNormalize";
import { normalizeMermaidSvg, readMermaidSvgSize } from "@/lib/mermaidSvg";
import { sanitizeMarkmapTree } from "@/lib/sanitizeMarkmap";
import { notifyActionError } from "@/lib/toast";
import { useIsDark } from "@/lib/useIsDark";
import { saveBlob } from "@/services/workspaceHttp";
import {
  Check,
  Copy,
  Download,
  Loader2,
  Maximize2,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/**
 * In-chat diagram rendering — the "表达形态" tier (工具与能力系统 §三): the model
 * writes a fenced ```mermaid / ```markmap / ```vega-lite block and the frontend
 * renders it, with NO tool and NO persisted artifact. Every engine is
 * dynamically imported so it costs nothing until a diagram actually appears in a
 * message.
 *
 * Streaming: a half-written diagram (mermaid syntax / vega-lite JSON) is invalid
 * mid-stream (§3.2), so we defer rendering until the turn finishes
 * (`streaming=false`) and show the source as a code block meanwhile; any render
 * failure falls back to the source too — a diagram never blanks the message.
 */

type DiagramKind = "mermaid" | "markmap" | "vega-lite";

type MermaidApi = typeof import("mermaid").default;
type MermaidModuleLoader = () => Promise<{ default: MermaidApi }>;

/** Actionable copy for dynamic-import failures (Vite deps race / missing prebundle). */
const MERMAID_LOAD_ERROR =
  "图表引擎加载失败。请刷新页面；若在开发环境，也可重启开发服务后再试。";

const MERMAID_LOAD_MAX_ATTEMPTS = 3;

let loadMermaidModule: MermaidModuleLoader = () => import("mermaid");
let mermaidPromise: Promise<MermaidApi> | null = null;
let mermaidRetryDelayMs = 200;

/**
 * Lazy-load mermaid once. A rejected import must NOT stick: caching the failed
 * Promise would permanently break every diagram until full reload. On failure we
 * null the cache so a later call can retry, and cap attempts per load so a
 * broken Vite deps pipe cannot spin forever inside one getMermaid().
 */
function getMermaid(): Promise<MermaidApi> {
  if (mermaidPromise) return mermaidPromise;

  mermaidPromise = (async () => {
    let lastErr: unknown;
    for (let attempt = 1; attempt <= MERMAID_LOAD_MAX_ATTEMPTS; attempt++) {
      try {
        return (await loadMermaidModule()).default;
      } catch (e) {
        lastErr = e;
        if (attempt < MERMAID_LOAD_MAX_ATTEMPTS && mermaidRetryDelayMs > 0) {
          await new Promise((r) =>
            setTimeout(r, mermaidRetryDelayMs * attempt),
          );
        }
      }
    }
    const err = new Error(MERMAID_LOAD_ERROR);
    if (lastErr instanceof Error) err.cause = lastErr;
    throw err;
  })().catch((e) => {
    // Drop the rejected promise so a later call (remount / theme flip) can retry.
    mermaidPromise = null;
    throw e;
  });

  return mermaidPromise;
}

/** @internal vitest — reset cache / inject a failing loader / zero delay. */
export function __resetMermaidLoaderForTests(opts?: {
  load?: MermaidModuleLoader;
  retryDelayMs?: number;
}) {
  mermaidPromise = null;
  loadMermaidModule = opts?.load ?? (() => import("mermaid"));
  mermaidRetryDelayMs =
    opts?.retryDelayMs === undefined ? 200 : opts.retryDelayMs;
}

function formatMermaidRenderError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  // getMermaid already throws MERMAID_LOAD_ERROR; keep it. Syntax/parse/render
  // failures stay as mermaid's message (or a short fallback) — never a raw
  // "Failed to fetch dynamically imported module" TypeError on this path.
  if (!msg) return "图表语法无效";
  if (/Failed to fetch dynamically imported module/i.test(msg)) {
    return MERMAID_LOAD_ERROR;
  }
  return msg;
}

interface MarkmapApi {
  Transformer: new () => { transform: (md: string) => { root: unknown } };
  Markmap: {
    create: (
      svg: SVGSVGElement,
      opts: unknown,
      data: unknown,
    ) => MarkmapInstance;
  };
}
interface MarkmapInstance {
  setData: (data: unknown) => void;
  fit: () => void;
  destroy?: () => void;
}

let markmapPromise: Promise<MarkmapApi> | null = null;
function getMarkmap() {
  if (!markmapPromise) {
    markmapPromise = Promise.all([
      import("markmap-lib"),
      import("markmap-view"),
    ]).then(([lib, view]) => ({
      Transformer: lib.Transformer as unknown as MarkmapApi["Transformer"],
      Markmap: view.Markmap as unknown as MarkmapApi["Markmap"],
    }));
  }
  return markmapPromise;
}

type VegaEmbed = (
  el: HTMLElement,
  spec: unknown,
  opts?: Record<string, unknown>,
) => Promise<{ finalize?: () => void }>;

// SECURITY (PI-001 提示注入·渲染侧外泄 · 图表取数信标): a model-emitted ```vega-lite``` spec is
// untrusted (the same indirect-injection surface PI-001's <img> downgrade guards). Vega's loader
// fetches `data.url` and image-mark urls AT RENDER TIME — a no-click egress beacon that the
// markdown image downgrade never covered, and which the app CSP's intentionally-broad connect-src
// (main/index.ts) does NOT contain. So we hand vega a loader whose `sanitize` refuses every
// network-reaching fetch; only inline schemes (data:/blob:, no network) pass. Self-contained
// charts (inline `data.values`) never hit the loader and render unchanged.
interface VegaLoader {
  sanitize: (uri: string, options: unknown) => Promise<{ href: string }>;
}
interface VegaNamespace {
  loader: () => VegaLoader;
}
const INLINE_ONLY_SCHEME = /^\s*(?:data|blob):/i;
function makeSafeLoader(vega: VegaNamespace): VegaLoader {
  const loader = vega.loader();
  const sanitize = loader.sanitize.bind(loader);
  loader.sanitize = (uri, options) =>
    INLINE_ONLY_SCHEME.test(String(uri))
      ? sanitize(uri, options)
      : Promise.reject(new Error(`已拦截图表的远程资源请求：${uri}`));
  return loader;
}

let vegaPromise: Promise<{ embed: VegaEmbed; loader: VegaLoader }> | null =
  null;
function getVega() {
  if (!vegaPromise) {
    vegaPromise = import("vega-embed").then((m) => ({
      embed: m.default as unknown as VegaEmbed,
      loader: makeSafeLoader(m.vega as unknown as VegaNamespace),
    }));
  }
  return vegaPromise;
}

let renderSeq = 0;

// 经统一下载接缝 saveBlob 落盘（桌面 = 主进程另存为；web = anchor）——裸 anchor+blob
// 在 Electron 打包端不工作（见 saveBlob 文档）。
async function downloadSvg(svg: string, filename: string): Promise<void> {
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  await saveBlob(blob, filename);
}

/** Raw source shown only when a diagram fails to render — a diagram never blanks
 * the message, it degrades to its source plus the error. */
function CodeFallback({
  code,
  lang,
  error,
}: {
  code: string;
  lang: string;
  error: string;
}) {
  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{lang}</span>
        <span className="text-xs text-muted-foreground">渲染失败</span>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
      <div className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
        {error}
      </div>
    </div>
  );
}

/** Loading placeholder shown while a diagram streams in or renders — a soft
 * pulse hinting at a node graph, far less noisy than dumping raw source while
 * the user waits. Source only appears on actual render failure (CodeFallback). */
function DiagramSkeleton({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-2 py-1">
        <span className="px-1 text-xs lowercase text-muted-foreground">
          {label}
        </span>
        <span className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" />
          {hint}
        </span>
      </div>
      <div className="flex h-40 items-center justify-center p-4">
        <div className="flex w-full max-w-xs animate-pulse flex-col items-center gap-3">
          <div className="h-8 w-28 rounded-lg bg-muted" />
          <div className="h-4 w-px bg-border" />
          <div className="flex gap-4">
            <div className="h-8 w-24 rounded-lg bg-muted" />
            <div className="h-8 w-24 rounded-lg bg-muted" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolbarButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <IconButton onClick={onClick} aria-label={label} title={label}>
      {children}
    </IconButton>
  );
}

/** Fullscreen diagram viewer with wheel-zoom (anchored at the cursor),
 * drag-to-pan and zoom controls — plain scroll is useless for big flowcharts /
 * dense charts / wide mind maps. Opens at contain-fit (same idea as the file
 * preview image lightbox `object-contain`), not native 1:1 — mermaid's SVG is
 * often a few hundred px and would otherwise sit as a stamp in the viewport.
 * Content size is offsetWidth/Height (layout px; CSS transform does not affect
 * them). A ResizeObserver re-fits while async SVGs settle, until the user
 * first interacts. Close via X or Esc (mirrors the canvas 放大态) — no
 * click-to-dismiss backdrop, which would need a keyboard equivalent. */
function DiagramLightbox({
  label,
  children,
  onClose,
}: {
  label: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const [ready, setReady] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    x: number;
    y: number;
    ox: number;
    oy: number;
  } | null>(null);
  const interacted = useRef(false);

  const center = useCallback(() => {
    const vp = viewportRef.current;
    const ct = contentRef.current;
    if (!vp || !ct) return;
    const fitted = fitContainView(
      vp.clientWidth,
      vp.clientHeight,
      ct.offsetWidth,
      ct.offsetHeight,
    );
    if (!fitted) return;
    setView(fitted);
    setReady(true);
  }, []);

  const zoomAt = useCallback((px: number, py: number, factor: number) => {
    interacted.current = true;
    setView((p) => {
      const scale = clampScale(p.scale * factor);
      const k = scale / p.scale;
      return { scale, x: px - k * (px - p.x), y: py - k * (py - p.y) };
    });
  }, []);

  const resetView = useCallback(() => {
    interacted.current = false;
    center();
  }, [center]);

  const zoomCentered = useCallback(
    (factor: number) => {
      const vp = viewportRef.current;
      if (vp) zoomAt(vp.clientWidth / 2, vp.clientHeight / 2, factor);
    },
    [zoomAt],
  );

  // Re-fit while the (possibly async) SVG settles and if the viewport resizes,
  // until first interaction. Hidden until the first successful measure so we
  // don't flash native-size mermaid in the corner.
  useEffect(() => {
    const vp = viewportRef.current;
    const ct = contentRef.current;
    if (!vp || !ct) return;
    const ro = new ResizeObserver(() => {
      if (!interacted.current) center();
    });
    ro.observe(vp);
    ro.observe(ct);
    center();
    const fallback = window.setTimeout(() => setReady(true), 250);
    return () => {
      ro.disconnect();
      window.clearTimeout(fallback);
    };
  }, [center]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Native non-passive wheel listener so preventDefault stops page scroll.
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      zoomAt(
        e.clientX - r.left,
        e.clientY - r.top,
        e.deltaY < 0 ? 1.1 : 1 / 1.1,
      );
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const onPointerDown = (e: ReactPointerEvent) => {
    interacted.current = true;
    viewportRef.current?.setPointerCapture(e.pointerId);
    dragRef.current = { x: e.clientX, y: e.clientY, ox: view.x, oy: view.y };
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setView((p) => ({
      ...p,
      x: d.ox + (e.clientX - d.x),
      y: d.oy + (e.clientY - d.y),
    }));
  };
  const endDrag = (e: ReactPointerEvent) => {
    dragRef.current = null;
    viewportRef.current?.releasePointerCapture?.(e.pointerId);
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-background/95">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <span className="text-sm text-muted-foreground">{label}</span>
        <div className="flex items-center gap-0.5">
          <ToolbarButton label="缩小" onClick={() => zoomCentered(1 / 1.25)}>
            <ZoomOut size={16} />
          </ToolbarButton>
          <span className="w-12 text-center text-xs tabular-nums text-muted-foreground">
            {Math.round(view.scale * 100)}%
          </span>
          <ToolbarButton label="放大" onClick={() => zoomCentered(1.25)}>
            <ZoomIn size={16} />
          </ToolbarButton>
          <ToolbarButton label="复位" onClick={resetView}>
            <RotateCcw size={16} />
          </ToolbarButton>
          <ToolbarButton label="关闭" onClick={onClose}>
            <X size={16} />
          </ToolbarButton>
        </div>
      </div>
      <div
        ref={viewportRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        onDoubleClick={resetView}
        className="relative min-h-0 flex-1 cursor-grab touch-none select-none overflow-hidden active:cursor-grabbing"
      >
        <div
          ref={contentRef}
          style={{
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
            transformOrigin: "0 0",
          }}
          className={`absolute left-0 top-0 ${ready ? "" : "opacity-0"}`}
        >
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Card chrome shared by both diagram kinds: toolbar (copy source / export SVG /
 * maximize) + a body whose live <svg> is read for export, and an on-demand
 * fullscreen lightbox. */
function DiagramCard({
  label,
  source,
  renderZoom,
  children,
}: {
  label: string;
  source: string;
  renderZoom: () => ReactNode;
  children: ReactNode;
}) {
  const bodyRef = useRef<HTMLButtonElement>(null);
  const [copied, setCopied] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  const onCopy = async () => {
    if (await copyText(source)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const onExport = () => {
    const svg = bodyRef.current?.querySelector("svg");
    if (svg) {
      void downloadSvg(
        new XMLSerializer().serializeToString(svg),
        `${label}.svg`,
      ).catch((e) => notifyActionError("导出图表失败", e));
    }
  };

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-2 py-1">
        <span className="px-1 text-xs lowercase text-muted-foreground">
          {label}
        </span>
        <div className="flex items-center gap-0.5">
          <ToolbarButton
            label={copied ? "已复制" : "复制源码"}
            onClick={onCopy}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </ToolbarButton>
          <ToolbarButton label="导出 SVG" onClick={onExport}>
            <Download size={14} />
          </ToolbarButton>
          <ToolbarButton label="放大" onClick={() => setZoomed(true)}>
            <Maximize2 size={14} />
          </ToolbarButton>
        </div>
      </div>
      <button
        type="button"
        ref={bodyRef}
        className="flex w-full justify-center overflow-auto border-0 bg-transparent p-3 cursor-zoom-in"
        onClick={() => setZoomed(true)}
      >
        {children}
      </button>
      {zoomed && (
        <DiagramLightbox label={label} onClose={() => setZoomed(false)}>
          {renderZoom()}
        </DiagramLightbox>
      )}
    </div>
  );
}

function mermaidInlineMaxHeightFromViewport(): number {
  const rem = Number.parseFloat(
    getComputedStyle(document.documentElement).fontSize,
  );
  return mermaidInlineMaxHeightPx(
    window.innerHeight,
    Number.isFinite(rem) && rem > 0 ? rem : 16,
  );
}

/**
 * Explicit contain-fit box. Do not put max-height + width/height:auto on the
 * SVG — Chromium resolves that to 0×0 (blank card). Do not stretch with
 * width:100% of the column — that is what enlarged compact TD stacks.
 */
function MermaidInlineSvg({ svg }: { svg: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const native = readMermaidSvgSize(svg);
  const [box, setBox] = useState(() =>
    inlineMermaidBoxPx(native.w, native.h, 0, mermaidInlineMaxHeightPx(0, 16)),
  );

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const apply = () => {
      setBox(
        inlineMermaidBoxPx(
          native.w,
          native.h,
          host.clientWidth,
          mermaidInlineMaxHeightFromViewport(),
        ),
      );
    };
    apply();
    window.addEventListener("resize", apply);
    if (typeof ResizeObserver === "undefined") {
      return () => window.removeEventListener("resize", apply);
    }
    const ro = new ResizeObserver(apply);
    ro.observe(host);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", apply);
    };
  }, [native.w, native.h]);

  return (
    <div ref={hostRef} className="flex w-full justify-center">
      {box ? (
        <div
          className="overflow-hidden [&>svg]:block [&>svg]:h-full [&>svg]:w-full"
          style={{ width: box.w, height: box.h }}
          // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid strict mode sanitizes the SVG via DOMPurify.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <div
          className="max-h-[min(50vh,36rem)] max-w-full overflow-auto [&>svg]:block [&>svg]:h-auto [&>svg]:max-w-full"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid strict mode sanitizes the SVG via DOMPurify.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </div>
  );
}

function MermaidDiagram({ code }: { code: string }) {
  const dark = useIsDark();
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      let mermaid: MermaidApi;
      try {
        mermaid = await getMermaid();
      } catch (e) {
        // Module load failure (Vite deps / network) — actionable copy, not raw TypeError.
        if (!cancelled) {
          setSvg(null);
          setError(formatMermaidRenderError(e));
        }
        return;
      }
      try {
        const normalized = normalizeMermaidSource(code);
        mermaid.initialize(mermaidRenderConfig(dark));
        // parse() is a cheap, side-effect-free syntax gate; render() then throws
        // on any render-time failure — both land in the catch below and degrade
        // to source. We deliberately do NOT sniff the returned SVG for
        // "error-icon" / "Syntax error": mermaid v11 inlines its theme CSS
        // (which contains an `.error-icon{…}` rule) into EVERY diagram's <style>,
        // so that substring is present in 100% of *valid* diagrams — the check
        // rejected every healthy chart. mermaid's real error path throws from
        // render(); it does not silently return an error SVG through this API.
        await mermaid.parse(normalized);
        renderSeq += 1;
        const id = `acmmd-${renderSeq}`;
        const { svg } = await mermaid.render(id, normalized);
        if (!cancelled) setSvg(normalizeMermaidSvg(svg));
      } catch (e) {
        // Syntax / render-time failure — keep mermaid's message when present.
        if (!cancelled) {
          setSvg(null);
          setError(formatMermaidRenderError(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, dark]);

  if (error) return <CodeFallback code={code} lang="mermaid" error={error} />;
  if (svg == null) return <DiagramSkeleton label="mermaid" hint="渲染中…" />;

  const rendered = <MermaidInlineSvg svg={svg} />;
  return (
    <DiagramCard
      label="mermaid"
      source={code}
      renderZoom={() => (
        // Native pixel size — the lightbox contain-fits this to the viewport.
        <div
          className="[&>svg]:block"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: same sanitized SVG as inline.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    >
      {rendered}
    </DiagramCard>
  );
}

/** Reusable markmap mount — used both inline and (fresh) inside the lightbox. */
function MarkmapCanvas({
  code,
  className,
  onError,
}: {
  code: string;
  className?: string;
  onError?: (message: string) => void;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const instanceRef = useRef<MarkmapInstance | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { Transformer, Markmap } = await getMarkmap();
        if (cancelled || !ref.current) return;
        const { root } = new Transformer().transform(code);
        // markmap has no built-in sanitizer (mermaid/vega each do) — strip remote-image
        // egress from node labels before render so a ```markmap block can't beacon out
        // where the CSP isn't enforced (10 P3-3, see sanitizeMarkmap.ts).
        sanitizeMarkmapTree(root);
        if (!instanceRef.current) {
          instanceRef.current = Markmap.create(ref.current, undefined, root);
        } else {
          instanceRef.current.setData(root);
        }
        instanceRef.current.fit();
      } catch (e) {
        if (!cancelled) onError?.(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, onError]);

  useEffect(
    () => () => {
      instanceRef.current?.destroy?.();
    },
    [],
  );

  return <svg ref={ref} className={className} />;
}

function MarkmapDiagram({ code }: { code: string }) {
  const [error, setError] = useState<string | null>(null);
  const onError = useCallback((message: string) => setError(message), []);

  if (error) return <CodeFallback code={code} lang="markmap" error={error} />;

  return (
    <DiagramCard
      label="markmap"
      source={code}
      renderZoom={() => (
        <MarkmapCanvas code={code} className="h-[78vh] w-[86vw]" />
      )}
    >
      <MarkmapCanvas code={code} className="h-80 w-full" onError={onError} />
    </DiagramCard>
  );
}

/** Reusable Vega-Lite mount — used both inline and (fresh) inside the lightbox.
 * The spec is the model-written JSON; `renderer:"svg"` keeps the DiagramCard
 * export (which serializes the live <svg>) working, and the dark theme follows
 * the app (background forced transparent so it inherits the card). */
function VegaCanvas({
  code,
  dark,
  className,
  onError,
}: {
  code: string;
  dark: boolean;
  className?: string;
  onError?: (message: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const finalizeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const spec = JSON.parse(code);
        const { embed, loader } = await getVega();
        if (cancelled || !ref.current) return;
        finalizeRef.current?.();
        ref.current.innerHTML = "";
        const result = await embed(ref.current, spec, {
          renderer: "svg",
          actions: false,
          theme: dark ? "dark" : undefined,
          config: { background: "transparent" },
          loader, // 渲染侧外泄防线：拒绝一切联网取数/取图（见上方 makeSafeLoader）
        });
        finalizeRef.current = result.finalize ?? null;
      } catch (e) {
        if (!cancelled) onError?.(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, dark, onError]);

  useEffect(
    () => () => {
      finalizeRef.current?.();
    },
    [],
  );

  return <div ref={ref} className={className} />;
}

function VegaLiteDiagram({ code }: { code: string }) {
  const dark = useIsDark();
  const [error, setError] = useState<string | null>(null);
  const onError = useCallback((message: string) => setError(message), []);

  if (error) return <CodeFallback code={code} lang="vega-lite" error={error} />;

  return (
    <DiagramCard
      label="vega-lite"
      source={code}
      renderZoom={() => (
        <VegaCanvas code={code} dark={dark} className="h-[78vh] w-[86vw]" />
      )}
    >
      <VegaCanvas
        code={code}
        dark={dark}
        className="w-full"
        onError={onError}
      />
    </DiagramCard>
  );
}

export function DiagramBlock({
  kind,
  code,
  streaming,
}: {
  kind: DiagramKind;
  code: string;
  streaming: boolean;
}) {
  if (streaming) {
    return <DiagramSkeleton label={kind} hint="生成中…" />;
  }
  if (kind === "mermaid") return <MermaidDiagram code={code} />;
  if (kind === "markmap") return <MarkmapDiagram code={code} />;
  return <VegaLiteDiagram code={code} />;
}
