/**
 * 全局协作感知 (前端UX设计.md §一) 的纯逻辑：判定一次收场是「完成」还是「失败」、
 * 以及当前路由是否应静默（跨对话 toast 用）。无 store / React 依赖，便于单测；
 * 订阅接线见 services/teamActivityNotifications.ts。
 */

interface TurnEndSnapshot {
  error: string | null;
  messages: { role: string; error?: unknown }[];
}

/**
 * 这条对话的最近一轮是否失败。两条失败链路盖在不同字段：SSE `error` 事件在回合收口【前】给
 * 最后一条助手消息盖 `error`；传输中断 (transport drop) 则在 finalize【后】写会话级 `error`
 * 字串。任一非空即失败——跨对话通知据此把「已完成」与「执行失败」分开。
 */
export function runtimeHasError(rt: TurnEndSnapshot): boolean {
  if (rt.error != null) return true;
  for (let i = rt.messages.length - 1; i >= 0; i--) {
    const m = rt.messages[i];
    if (m.role === "assistant") return m.error != null;
  }
  return false;
}

/** 当前正在查看的对话 id（解析 hash 路由 `#/conversations/:id`），其它路由返回 null——让
 * 通知器无需 React / router 依赖即可对「正在看的那条对话」保持沉默。 */
export function conversationIdFromHash(hash: string): string | null {
  const path = hash.replace(/^#/, "");
  const m = /^\/conversations\/([^/?#]+)/.exec(path);
  return m ? decodeURIComponent(m[1]) : null;
}

/** 开发 / 回放态路由（#/preview）跑的是合成回合，不弹跨对话通知，让离线预览
 * 自检（frontend-preview）保持安静。 */
export function isTransientRoute(hash: string): boolean {
  const path = hash.replace(/^#/, "");
  return path.startsWith("/preview");
}
