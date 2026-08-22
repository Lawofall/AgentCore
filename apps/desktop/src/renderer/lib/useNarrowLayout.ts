import { useEffect, useState } from "react";

/** AppShell 收成 4-tab + 抽屉 + sheet 的视口上限（含）。权威 → 前端技术 §五。 */
export const NARROW_MAX_WIDTH = 767;

const NARROW_QUERY = `(max-width: ${NARROW_MAX_WIDTH}px)`;

export function readNarrowViewport(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(NARROW_QUERY).matches
  );
}

/** Live viewport flag. Missing `matchMedia` (jsdom / SSR) → wide. */
export function useNarrowLayout(): boolean {
  const [narrow, setNarrow] = useState(readNarrowViewport);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(NARROW_QUERY);
    const onChange = () => setNarrow(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

/** IM 线程 / 设置子页 / 全屏图 / 预览：藏窄屏底栏与顶栏。 */
export function shouldHideNarrowChrome(pathname: string): boolean {
  if (pathname === "/preview" || pathname.startsWith("/preview/")) return true;
  if (pathname.startsWith("/simulation")) return true;
  if (pathname === "/float" || pathname.startsWith("/float")) return true;
  if (pathname.includes("/turn/")) return true;
  if (/^\/messages\/[^/]+/.test(pathname)) return true;
  if (pathname.startsWith("/more/")) return true;
  if (pathname.startsWith("/legal")) return true;
  return false;
}

/** 草稿 `/`、会话 `/conversations/:id`、会话管理 `/conversations`。 */
export function isNarrowChatRoute(pathname: string): boolean {
  if (pathname === "/" || pathname === "/conversations") return true;
  return (
    /^\/conversations\/[^/]+$/.test(pathname) && !pathname.includes("/turn/")
  );
}
