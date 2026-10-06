/**
 * 全局协作感知 (前端UX设计.md §一) 的纯逻辑：判定一次收场是「完成」还是「失败」、
 * 场面是否在眼前、以及壳×场面选哪条出口。无 store / React 依赖，便于单测；
 * 订阅接线见 services/teamActivityNotifications.ts。
 */

interface TurnEndSnapshot {
  error: string | null;
  messages: { role: string; error?: unknown }[];
}

/**
 * 这条对话的最近一轮是否失败。两条失败链路盖在不同字段：SSE `error` 事件在回合收口【前】给
 * 最后一条助手消息盖 `error`；传输中断 (transport drop) 则在 finalize【后】写会话级 `error`
 * 字串。任一非空即失败——协作感知据此把「已完成」与「执行失败」分开。
 */
export function runtimeHasError(rt: TurnEndSnapshot): boolean {
  if (rt.error != null) return true;
  for (let i = rt.messages.length - 1; i >= 0; i--) {
    const m = rt.messages[i];
    if (m.role === "assistant") return m.error != null;
  }
  return false;
}

/** 当前正在查看的对话 id（解析 hash 路由 `#/conversations/:id`，含回合详情），
 * 其它路由返回 null。场面闸用路由，不用切走仍留的运行时指针。 */
export function conversationIdFromHash(hash: string): string | null {
  const path = hash.replace(/^#/, "");
  const m = /^\/conversations\/([^/?#]+)/.exec(path);
  return m ? decodeURIComponent(m[1]) : null;
}

/** 开发 / 回放态路由（#/preview）跑的是合成回合，不弹协作感知，让离线预览
 * 自检（frontend-preview）保持安静。 */
export function isTransientRoute(hash: string): boolean {
  const path = hash.replace(/^#/, "");
  return path.startsWith("/preview");
}

/**
 * 这场协作事件的场面是否已在眼前：主画布就是这条对话，并排时旁边那栏也算，
 * 或已打开跟它的浮窗。浮窗 id、旁边栏 id 由调用方传入（真窗走主进程表）。
 */
export function isConversationOnScene(
  conversationId: string,
  hash: string,
  floatConversationIds: readonly string[],
  besideConversationIds: readonly string[] = [],
): boolean {
  if (isTransientRoute(hash)) return false;
  if (conversationIdFromHash(hash) === conversationId) return true;
  if (besideConversationIds.includes(conversationId)) return true;
  return floatConversationIds.includes(conversationId);
}

export type AmbientOutlet = "silence" | "toast" | "os";

/**
 * 壳 × 场面选出口。手机在线没有 Electron 系统通知，不在场仍走应用内提示
 * （服务端有意 skipped_mobile_online）。网页隐藏没有系统通道 → 静默。
 */
export function pickAmbientOutlet(input: {
  shellPresent: boolean;
  onScene: boolean;
  hasOsNotification: boolean;
  nativeMobile: boolean;
}): AmbientOutlet {
  if (input.shellPresent && input.onScene) return "silence";
  if (!input.shellPresent) {
    if (input.hasOsNotification) return "os";
    if (input.nativeMobile) return "toast";
    return "silence";
  }
  return "toast";
}
