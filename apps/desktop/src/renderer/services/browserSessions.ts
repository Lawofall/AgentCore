import { api } from "@/services/api";
import {
  getActiveSidecarTarget,
  resolveConversationLocalTarget,
  resolveSidecarRoot,
} from "@/services/sidecarRouting";

/**
 * 云端浏览器会话 list / create / navigate / close（对齐 `…/browser/sessions`）。
 *
 * Local hydrate：能解析 sidecar target 时走 ``sidecarApi.listBrowserSessions``
 *（同进程 Registry）；本机绑定会话但走不了 sidecar → 空 list（**禁止**云 GET，
 * 云 Registry 对 Local 恒空=假清空）；仅非 Local 绑定才云 GET。
 *
 * 空白页签仍是本地壳态、不自动 POST；Web 地址栏回车才显式
 * {@link createBrowserSession}（sandbox）+ {@link navigateBrowserSession}。
 * 桌面有 `browserApi` 时走 Local WebContents，不经本 create。
 * 走 `services/api` 以复用 401 刷新 / CSRF（与 browserInput 同路）。
 */

export type BrowserHostKind = "sandbox" | "local";
export type BrowserControl = "agent" | "user";

/** 客户端投影（camelCase）。 */
export interface BrowserSessionInfo {
  sessionId: string;
  conversationId: string;
  hostKind: BrowserHostKind;
  control: BrowserControl;
  runId: string | null;
  createdAt: number;
  lastUsed: number;
  url?: string | null;
  title?: string | null;
}

export interface BrowserSessionList {
  sessions: BrowserSessionInfo[];
  activeSessionId: string | null;
}

/** 服务端 wire（snake_case）。 */
interface BrowserSessionWire {
  session_id: string;
  conversation_id: string;
  host_kind: BrowserHostKind;
  control: BrowserControl;
  run_id?: string | null;
  created_at: number;
  last_used: number;
  url?: string | null;
  title?: string | null;
}

interface BrowserSessionListWire {
  data: BrowserSessionWire[];
  active_session_id?: string | null;
}

function sessionsPath(conversationId: string): string {
  return `/v1/conversations/${encodeURIComponent(conversationId)}/browser/sessions`;
}

function sessionPath(conversationId: string, sessionId: string): string {
  return `${sessionsPath(conversationId)}/${encodeURIComponent(sessionId)}`;
}

function fromWire(w: BrowserSessionWire): BrowserSessionInfo {
  return {
    sessionId: w.session_id,
    conversationId: w.conversation_id,
    hostKind: w.host_kind,
    control: w.control,
    runId: w.run_id ?? null,
    createdAt: w.created_at,
    lastUsed: w.last_used,
    url: w.url ?? null,
    title: w.title ?? null,
  };
}

function fromListWire(r: BrowserSessionListWire): BrowserSessionList {
  return {
    sessions: (r.data ?? []).map(fromWire),
    activeSessionId: r.active_session_id ?? null,
  };
}

/** GET …/browser/sessions —— Local→sidecar Registry；本机绑定禁云；否则云端 list。 */
export async function listBrowserSessions(
  conversationId: string,
): Promise<BrowserSessionList> {
  const active = getActiveSidecarTarget(conversationId);
  const target = active ?? (await resolveSidecarRoot(conversationId));
  if (
    target &&
    typeof window !== "undefined" &&
    window.sidecarApi?.listBrowserSessions
  ) {
    const raw = await window.sidecarApi.listBrowserSessions({
      rootId: target.rootId,
      subpath: target.subpath,
      conversationId,
    });
    return fromListWire(raw);
  }
  // 本机绑定会话：云 Registry 无 Local session，GET 恒空会假清空右坞。
  if ((await resolveConversationLocalTarget(conversationId)) != null) {
    return { sessions: [], activeSessionId: null };
  }
  const r = await api.get<BrowserSessionListWire>(sessionsPath(conversationId));
  return fromListWire(r);
}

/** POST …/browser/sessions —— 显式开一页（Web 地址栏 / 壳侧；默认 sandbox）。 */
export function createBrowserSession(
  conversationId: string,
  opts?: { hostKind?: BrowserHostKind; activate?: boolean },
): Promise<BrowserSessionInfo> {
  return api
    .post<BrowserSessionWire>(sessionsPath(conversationId), {
      host_kind: opts?.hostKind ?? "sandbox",
      activate: opts?.activate ?? true,
    })
    .then(fromWire);
}

/** POST …/browser/sessions/{id}/navigate —— 对该 session 发 BrowserCommand navigate。 */
export function navigateBrowserSession(
  conversationId: string,
  sessionId: string,
  url: string,
): Promise<BrowserSessionInfo> {
  return api
    .post<BrowserSessionWire>(
      `${sessionPath(conversationId, sessionId)}/navigate`,
      {
        url,
      },
    )
    .then(fromWire);
}

/** DELETE …/browser/sessions/{session_id} —— 关掉一页服务端会话（会拆 gVisor）。 */
export async function closeBrowserSession(
  conversationId: string,
  sessionId: string,
): Promise<void> {
  await api.delete(sessionPath(conversationId, sessionId));
}

/** PATCH …/browser/sessions/{session_id} —— L7 回写 url/title（地址栏 / 本机导航后）。 */
export async function patchBrowserSessionNav(
  conversationId: string,
  sessionId: string,
  nav: { url?: string | null; title?: string | null },
): Promise<void> {
  await api.patch(sessionPath(conversationId, sessionId), nav);
}
