import { initPush } from "@/services/push";
import { useEffect } from "react";

/** 冷启动点通知也能进会话（hash 路由，不必等 AuthGate 里的 Router）。 */
export function PushBridge() {
  useEffect(() => {
    if (window.__NATIVE__ !== true) return;
    let cancelled = false;
    let cleanup: (() => void) | undefined;
    void initPush((conversationId) => {
      window.location.hash = `#/conversations/${conversationId}`;
    }).then((stop) => {
      if (cancelled) stop();
      else cleanup = stop;
    });
    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, []);
  return null;
}
