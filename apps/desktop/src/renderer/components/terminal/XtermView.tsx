/**
 * xterm.js 交互视图 —— 主题色读 design tokens（禁硬编码调色板）。
 */
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef } from "react";

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return v || fallback;
}

function buildTheme() {
  const fg = cssVar("--foreground", "oklch(0.93 0.005 255)");
  const mutedFg = cssVar("--muted-foreground", "oklch(0.55 0.02 255)");
  const primary = cssVar("--primary", "oklch(0.55 0.18 255)");
  const destructive = cssVar("--destructive", "oklch(0.55 0.2 25)");
  const success = cssVar("--success", "oklch(0.55 0.14 145)");
  const warning = cssVar("--warning", primary);
  return {
    background: cssVar("--muted", "transparent"),
    foreground: fg,
    cursor: fg,
    cursorAccent: cssVar("--background", "oklch(0.13 0.004 255)"),
    selectionBackground: cssVar("--accent", "oklch(0.9 0.01 255)"),
    selectionForeground: cssVar("--accent-foreground", fg),
    black: cssVar("--background", "oklch(0.13 0.004 255)"),
    red: destructive,
    green: success,
    yellow: warning,
    blue: primary,
    magenta: primary,
    cyan: primary,
    white: fg,
    brightBlack: mutedFg,
    brightRed: destructive,
    brightGreen: success,
    brightYellow: warning,
    brightBlue: primary,
    brightMagenta: primary,
    brightCyan: primary,
    brightWhite: fg,
  };
}

export function XtermView({
  sessionId,
  output,
  writable,
  onData,
  onResize,
}: {
  sessionId: string;
  /** 累计原始 ANSI 输出（挂载回放 + 增量追加）。 */
  output: string;
  writable: boolean;
  onData: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const writtenLenRef = useRef(0);
  const onDataRef = useRef(onData);
  const onResizeRef = useRef(onResize);
  onDataRef.current = onData;
  onResizeRef.current = onResize;

  // 创建 / 销毁终端实例（随 session 切换重建）
  // biome-ignore lint/correctness/useExhaustiveDependencies: sessionId 切换必须重建 Terminal
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: writable,
      convertEol: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 12,
      theme: buildTheme(),
      disableStdin: !writable,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;
    writtenLenRef.current = 0;

    const dataDisp = writable
      ? term.onData((data) => {
          onDataRef.current(data);
        })
      : null;

    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
        onResizeRef.current(term.cols, term.rows);
      } catch {
        /* host 可能已卸载 */
      }
    });
    ro.observe(host);
    onResizeRef.current(term.cols, term.rows);

    return () => {
      ro.disconnect();
      dataDisp?.dispose();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [sessionId, writable]);

  // 回放 + 增量
  // biome-ignore lint/correctness/useExhaustiveDependencies: session 切换后与新 Terminal 对齐
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    if (output.length < writtenLenRef.current) {
      // buffer 被环形截断：重置
      term.reset();
      term.write(output);
      writtenLenRef.current = output.length;
      return;
    }
    if (output.length > writtenLenRef.current) {
      term.write(output.slice(writtenLenRef.current));
      writtenLenRef.current = output.length;
    }
  }, [sessionId, output]);

  return (
    <div
      ref={hostRef}
      className="min-h-0 flex-1 overflow-hidden bg-muted/30 p-1 [&_.xterm]:h-full [&_.xterm-viewport]:rounded-lg"
    />
  );
}
