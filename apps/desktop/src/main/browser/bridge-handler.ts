/**
 * Bridge 鉴权 + HTTP 请求处理（纯 Node，无 electron）——可单测。
 * 进程内 HTTP server / LocalHost 接线见 bridge.ts。
 *
 * 动作面：`GET /health`、`POST /navigate`（兼容）、`POST /command`
 * （navigate/click/type/scroll/snapshot/screenshot/console，与 browser_* 对齐）。
 * Local live：server 在 Hub attach 后周期 POST screenshot，帧含 frame_b64+width+height。
 */

import { randomBytes } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

/** 默认**闲置** TTL：每次校验通过顺延，不是 token 的绝对寿命。 */
const DEFAULT_TTL_MS = 5 * 60_000;

export const BRIDGE_ACTIONS = [
  "navigate",
  "click",
  "type",
  "scroll",
  "snapshot",
  "screenshot",
  "console",
] as const;

export type BridgeAction = (typeof BRIDGE_ACTIONS)[number];

export interface BridgeAuthState {
  token: string | null;
  /** 绝对到期时刻；每次校验通过顺延 {@link BridgeAuthState.idleTtlMs}。 */
  expiresAt: number;
  /** 当前 token 的闲置 TTL，滑动续期按它顺延。 */
  idleTtlMs: number;
}

/**
 * Bridge token 鉴权：**滑动续期**——每次校验通过就把到期时间顺延一个 TTL，
 * 即 TTL 是「闲置上限」而非绝对寿命。
 *
 * 凭证只在回合边界（initialize / startTurn / resume）下发，本回合内无法补发；
 * 绝对过期会让跑满一个 TTL 的长回合从中途开始 401（前面的 navigate 过、后面的
 * snapshot 挂），且重试必然继续失败。跟随使用续期后，只有真闲置超过 TTL 才失效。
 */
export function createBridgeAuth(now: () => number = Date.now): {
  state: BridgeAuthState;
  issueToken: (ttlMs?: number) => string;
  validateToken: (token: string | undefined | null) => boolean;
} {
  const state: BridgeAuthState = {
    token: null,
    expiresAt: 0,
    idleTtlMs: DEFAULT_TTL_MS,
  };
  return {
    state,
    issueToken(ttlMs = DEFAULT_TTL_MS) {
      const token = randomBytes(32).toString("hex");
      state.token = token;
      state.idleTtlMs = ttlMs;
      state.expiresAt = now() + ttlMs;
      return token;
    },
    validateToken(token) {
      if (!token || !state.token) return false;
      const at = now();
      if (at >= state.expiresAt) return false;
      if (token !== state.token) return false;
      state.expiresAt = at + state.idleTtlMs;
      return true;
    },
  };
}

function readBearer(req: IncomingMessage): string | null {
  const raw = req.headers.authorization;
  if (typeof raw !== "string") return null;
  const m = /^Bearer\s+(\S+)$/i.exec(raw.trim());
  return m?.[1] ?? null;
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return undefined;
  }
}

function sendJson(
  res: ServerResponse,
  status: number,
  body: Record<string, unknown>,
): void {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(data),
  });
  res.end(data);
}

export type BridgeValidate = (token: string | null) => boolean;

/** Host 动作结果：ok + data（对齐 BrowserCommandResult.data）；失败带稳定 error/code。 */
export type BridgeHostResult =
  | { ok: true; data?: Record<string, unknown> }
  | { ok: false; error: string; code?: string };

/**
 * 注入的 Host 派发器（避免本模块依赖 electron）。
 * ``pageId`` = Registry ``session_id``（Local 路径同 id）；
 * ``conversationId`` 强制（缺字段由 handler 先 400，不到此回调）。
 */
export type BridgeDispatch = (
  pageId: string,
  action: BridgeAction,
  args: Record<string, unknown>,
  conversationId: string,
) => BridgeHostResult | Promise<BridgeHostResult>;

function isBridgeAction(value: string): value is BridgeAction {
  return (BRIDGE_ACTIONS as readonly string[]).includes(value);
}

function asRecord(body: unknown): Record<string, unknown> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body))
    return null;
  return body as Record<string, unknown>;
}

function pageIdFromBody(body: Record<string, unknown>): string {
  const raw = body.pageId ?? body.session_id ?? body.sessionId;
  return typeof raw === "string" ? raw.trim() : "";
}

function conversationIdFromBody(body: Record<string, unknown>): string {
  const raw = body.conversationId ?? body.conversation_id;
  return typeof raw === "string" ? raw.trim() : "";
}

/**
 * 处理单次 Bridge 请求。`dispatch` 须由调用方注入（避免本模块依赖 electron Host）。
 */
export async function handleBridgeRequest(
  req: IncomingMessage,
  res: ServerResponse,
  validate: BridgeValidate,
  dispatch: BridgeDispatch,
): Promise<void> {
  const token = readBearer(req);
  if (!validate(token)) {
    sendJson(res, 401, { ok: false, error: "unauthorized" });
    return;
  }

  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  const path = url.pathname;

  if (req.method === "GET" && path === "/health") {
    sendJson(res, 200, { ok: true, service: "desktop-browser-bridge" });
    return;
  }

  if (req.method === "POST" && path === "/navigate") {
    const body = await readJsonBody(req);
    if (body === undefined) {
      sendJson(res, 400, { ok: false, error: "invalid_json" });
      return;
    }
    const rec = asRecord(body);
    const pageId = rec ? pageIdFromBody(rec) : "";
    const conversationId = rec ? conversationIdFromBody(rec) : "";
    const target = rec && typeof rec.url === "string" ? rec.url.trim() : "";
    if (!conversationId) {
      sendJson(res, 400, { ok: false, error: "missing_conversationId" });
      return;
    }
    if (!pageId || !target) {
      sendJson(res, 400, { ok: false, error: "missing_pageId_or_url" });
      return;
    }
    const result = await dispatch(
      pageId,
      "navigate",
      { url: target },
      conversationId,
    );
    if (!result.ok) {
      const status = result.code === "host_unavailable" ? 503 : 422;
      sendJson(res, status, {
        ok: false,
        error: result.error,
        ...(result.code ? { code: result.code } : {}),
      });
      return;
    }
    sendJson(res, 200, {
      ok: true,
      ...(result.data ? { data: result.data } : {}),
    });
    return;
  }

  if (req.method === "POST" && path === "/command") {
    const body = await readJsonBody(req);
    if (body === undefined) {
      sendJson(res, 400, { ok: false, error: "invalid_json" });
      return;
    }
    const rec = asRecord(body);
    if (!rec) {
      sendJson(res, 400, { ok: false, error: "invalid_body" });
      return;
    }
    const pageId = pageIdFromBody(rec);
    const conversationId = conversationIdFromBody(rec);
    const actionRaw = typeof rec.action === "string" ? rec.action.trim() : "";
    if (!conversationId) {
      sendJson(res, 400, { ok: false, error: "missing_conversationId" });
      return;
    }
    if (!pageId || !isBridgeAction(actionRaw)) {
      sendJson(res, 400, { ok: false, error: "missing_pageId_or_action" });
      return;
    }
    const args =
      typeof rec.args === "object" &&
      rec.args !== null &&
      !Array.isArray(rec.args)
        ? (rec.args as Record<string, unknown>)
        : (() => {
            const {
              pageId: _p,
              session_id: _s,
              sessionId: _S,
              conversationId: _c,
              conversation_id: _C,
              action: _a,
              args: _args,
              ...rest
            } = rec;
            return rest;
          })();
    const result = await dispatch(pageId, actionRaw, args, conversationId);
    if (!result.ok) {
      const status = result.code === "host_unavailable" ? 503 : 422;
      sendJson(res, status, {
        ok: false,
        error: result.error,
        ...(result.code ? { code: result.code } : {}),
      });
      return;
    }
    sendJson(res, 200, { ok: true, data: result.data ?? {} });
    return;
  }

  sendJson(res, 404, { ok: false, error: "not_found" });
}
