/**
 * Vector-script mock backend for desktop e2e (proposal §三–§五).
 *
 * §八 CSRF: X-CSRF-Token rides the same three responses production issues it on —
 * login, refresh, and the 403 that rejects a session holding no usable token — and
 * mutating requests on a cookie session are **verified**, not just echoed, so a
 * client path that forgets the header 403s here exactly as it would in production.
 * Network seam only — SSE bodies come from conformance fixtures via scripts.ts.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import {
  assertPlanHasBoundary,
  buildPlan,
  resolveScriptName,
  type ScriptPlan,
} from "./scripts.ts";
import {
  conversationSummary,
  emailCodeAccepted,
  emptyGrouped,
  emptyMessages,
  emptyRecovery,
  emptyWorkspaces,
  HYDRATE_FAIL_CONV_ID,
  loginOk,
  MOCK_USER,
  readyzOk,
  statusOk,
  type ConversationSummary,
  type TurnRecoveryResponse,
} from "./rest.ts";
import type { ConformanceEvent } from "./fixtures.ts";

const PORT = Number(process.env.E2E_MOCK_PORT ?? 18000);
const HOST = process.env.E2E_MOCK_HOST ?? "127.0.0.1";
const CSRF = "e2e-csrf-token";
const COOKIE = "ac_access=e2e-mock; Path=/; HttpOnly; SameSite=Lax";
/** Inter-event pacing — keeps streams observable without blowing the 3min budget. */
const EVENT_GAP_MS = Number(process.env.E2E_EVENT_GAP_MS ?? 8);

interface ConvState {
  summary: ConversationSummary;
  scriptName?: string;
  plan?: ScriptPlan;
  /** Live recovery snapshot — must not wipe stream-surfaced cards on reopen race. */
  recovery: TurnRecoveryResponse;
}

interface HotHold {
  res: ServerResponse;
  remaining: ConformanceEvent[];
  seq: number;
  closed: boolean;
}

const conversations = new Map<string, ConvState>();
const hotHolds = new Map<string, HotHold>();

/** Cold-open target for hydrate-failure e2e (messages GET → 500, no cache). */
conversations.set(HYDRATE_FAIL_CONV_ID, {
  summary: conversationSummary({
    id: HYDRATE_FAIL_CONV_ID,
    title: "e2e hydrate fail",
    message_count: 1,
  }),
  recovery: emptyConvRecovery(),
});

function emptyConvRecovery(): TurnRecoveryResponse {
  return emptyRecovery();
}

/** After streaming a segment, sync /recovery so a racing loadRecovery cannot wipe UI. */
function syncRecoveryFromEvents(
  conv: ConvState,
  events: ConformanceEvent[],
  opts: { liveRunning: boolean },
): void {
  const recovery = emptyConvRecovery();
  recovery.live_running = opts.liveRunning;

  for (const ev of events) {
    const payload = (ev.payload ?? {}) as Record<string, unknown>;
    if (ev.type === "approval_required") {
      const id = String(payload.approval_id ?? "");
      if (!id) continue;
      recovery.pending_interactions = [
        ...(recovery.pending_interactions ?? []),
        {
          kind: "approval",
          id,
          message_id: String(payload.message_id ?? "m1"),
          payload,
        },
      ];
    }
    if (ev.type === "team_preview_required") {
      const checkpointId = String(payload.checkpoint_id ?? "");
      if (!checkpointId) continue;
      recovery.paused = [
        {
          checkpoint_id: checkpointId,
          kind: "team_preview",
          message_id: "m1",
          user_message: "",
          user_message_id: "",
          question: "",
          context: "",
          assumptions: [],
          questions: [],
          steps: [],
          pending: [],
          workers: Array.isArray(payload.workers)
            ? (payload.workers as Record<string, unknown>[])
            : [],
          tools: Array.isArray(payload.tools)
            ? (payload.tools as string[])
            : [],
          primitive: String(payload.primitive ?? "delegate"),
          motion: String(payload.motion ?? ""),
          form: String(payload.form ?? ""),
          sides: Array.isArray(payload.sides)
            ? (payload.sides as Record<string, unknown>[])
            : [],
          max_rounds: Number(payload.max_rounds ?? 0),
          thorough: payload.thorough !== false,
          intent: "kickoff",
        },
      ];
    }
    if (ev.type === "message_end") {
      const reason = String(
        (payload as { finish_reason?: string }).finish_reason ?? "",
      );
      if (reason !== "paused") {
        recovery.live_running = false;
        recovery.paused = [];
        recovery.pending_interactions = [];
      }
    }
  }
  conv.recovery = recovery;
}

function cors(req: IncomingMessage, res: ServerResponse): void {
  const origin = req.headers.origin ?? "*";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Credentials", "true");
  // Mirror production FastAPI `allow_headers=["*"]` — echo the browser preflight
  // list so X-Client-Platform / X-Client-Version (and future client headers) pass.
  const requested = req.headers["access-control-request-headers"];
  res.setHeader(
    "Access-Control-Allow-Headers",
    typeof requested === "string" && requested.length > 0 ? requested : "*",
  );
  res.setHeader(
    "Access-Control-Allow-Methods",
    "GET, POST, PUT, PATCH, DELETE, OPTIONS",
  );
  res.setHeader("Access-Control-Expose-Headers", "X-CSRF-Token, Content-Disposition");
}

function json(
  req: IncomingMessage,
  res: ServerResponse,
  status: number,
  body: unknown,
  extraHeaders?: Record<string, string>,
): void {
  cors(req, res);
  const headers: Record<string, string> = {
    "Content-Type": "application/json; charset=utf-8",
    ...extraHeaders,
  };
  res.writeHead(status, headers);
  res.end(JSON.stringify(body));
}

function isAuthed(req: IncomingMessage): boolean {
  const cookie = req.headers.cookie ?? "";
  return cookie.includes("ac_access=");
}

const CSRF_SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
/** Mirrors middleware/csrf.py `_EXEMPT_PREFIXES` (login/register/refresh/token). */
const CSRF_EXEMPT_PREFIXES = [
  "/v1/auth/login",
  "/v1/auth/register",
  "/v1/auth/password",
  "/v1/auth/refresh",
  "/v1/auth/token",
];

function csrfRejected(
  req: IncomingMessage,
  method: string,
  path: string,
): boolean {
  if (CSRF_SAFE_METHODS.has(method)) return false;
  if (CSRF_EXEMPT_PREFIXES.some((p) => path.startsWith(p))) return false;
  if (!isAuthed(req)) return false; // no cookie session → nothing to protect
  return req.headers["x-csrf-token"] !== CSRF;
}

function csrfFailed(req: IncomingMessage, res: ServerResponse): void {
  cors(req, res);
  // Production re-arms the client on the rejection itself, so retrying the same
  // request works (middleware/csrf.py).
  res.writeHead(403, {
    "Content-Type": "application/json; charset=utf-8",
    "X-CSRF-Token": CSRF,
  });
  res.end(
    JSON.stringify({
      error: {
        code: "CSRF_FAILED",
        message: "CSRF token missing or invalid. Re-login and retry.",
      },
    }),
  );
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function writeSseFrame(
  res: ServerResponse,
  event: ConformanceEvent,
  seq: number,
): void {
  // Match production `_format_sse`: event + id + envelope data in one chunk.
  const data = JSON.stringify({
    type: event.type,
    timestamp: event.timestamp ?? new Date().toISOString(),
    payload: event.payload ?? {},
  });
  res.write(`event: ${event.type}\nid: ${seq}\ndata: ${data}\n\n`);
}

async function streamEvents(
  res: ServerResponse,
  events: ConformanceEvent[],
  startSeq = 1,
): Promise<number> {
  let seq = startSeq;
  for (const event of events) {
    if (res.writableEnded) break;
    writeSseFrame(res, event, seq);
    seq += 1;
    if (EVENT_GAP_MS > 0) await sleep(EVENT_GAP_MS);
  }
  return seq;
}

function beginSse(req: IncomingMessage, res: ServerResponse): void {
  cors(req, res);
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
  });
  // Flush headers immediately so the client leaves the connect-timeout window.
  res.write(": connected\n\n");
}

async function handleMessagesPost(
  req: IncomingMessage,
  res: ServerResponse,
  conversationId: string,
): Promise<void> {
  const raw = await readBody(req);
  let content = "";
  try {
    content = String((JSON.parse(raw) as { content?: string }).content ?? "");
  } catch {
    content = "";
  }
  const scriptName = resolveScriptName(content);
  const plan = buildPlan(scriptName);
  assertPlanHasBoundary(plan);

  const conv = conversations.get(conversationId);
  if (conv) {
    conv.scriptName = scriptName;
    conv.plan = plan;
    conv.summary.message_count = Math.max(1, conv.summary.message_count);
    conv.summary.updated_at = new Date().toISOString();
    if (!conv.summary.title) {
      conv.summary.title = content.replace(/__e2e_script__:[a-z0-9_]+/gi, "").trim() ||
        "新对话";
    }
  }

  beginSse(req, res);

  if (plan.kind === "hot_gate") {
    const seq = await streamEvents(res, plan.initial, 1);
    if (conv) syncRecoveryFromEvents(conv, plan.initial, { liveRunning: true });
    // Flush comment so the gate frame isn't stuck behind a half-filled TCP buffer.
    res.write(": gate\n\n");
    const hold: HotHold = {
      res,
      remaining: plan.continueSameStream,
      seq,
      closed: false,
    };
    hotHolds.set(conversationId, hold);
    const ping = setInterval(() => {
      if (hold.closed || res.writableEnded) {
        clearInterval(ping);
        return;
      }
      res.write(": ping\n\n");
    }, 15_000);
    res.on("close", () => {
      hold.closed = true;
      clearInterval(ping);
      if (hotHolds.get(conversationId) === hold) hotHolds.delete(conversationId);
    });
    return;
  }

  await streamEvents(res, plan.initial, 1);
  if (conv) {
    syncRecoveryFromEvents(conv, plan.initial, {
      liveRunning: plan.kind === "complete" ? false : false,
    });
    // Cold gate ends paused — keep recovery.paused for racing loadRecovery.
    if (plan.kind === "cold_gate") {
      syncRecoveryFromEvents(conv, plan.initial, { liveRunning: false });
    }
  }
  res.end();
}

async function handleInteractionsPost(
  req: IncomingMessage,
  res: ServerResponse,
  conversationId: string,
): Promise<void> {
  await readBody(req);
  json(req, res, 200, statusOk());

  const hold = hotHolds.get(conversationId);
  if (!hold || hold.closed || hold.res.writableEnded) return;
  try {
    await streamEvents(hold.res, hold.remaining, hold.seq);
    const conv = conversations.get(conversationId);
    if (conv) {
      syncRecoveryFromEvents(conv, hold.remaining, { liveRunning: false });
      conv.recovery = emptyConvRecovery();
    }
  } finally {
    hold.closed = true;
    if (!hold.res.writableEnded) hold.res.end();
    hotHolds.delete(conversationId);
  }
}

async function handleResumePost(
  req: IncomingMessage,
  res: ServerResponse,
  conversationId: string,
): Promise<void> {
  await readBody(req);
  const conv = conversations.get(conversationId);
  const plan =
    conv?.plan ??
    (conv?.scriptName ? buildPlan(conv.scriptName) : null) ??
    buildPlan("team_preview_resolved_continue");
  if (plan.resumeStream.length === 0) {
    json(req, res, 404, {
      error: { code: "not_paused", message: "no resume segment for script" },
    });
    return;
  }
  beginSse(req, res);
  await streamEvents(res, plan.resumeStream, 1);
  if (conv) conv.recovery = emptyConvRecovery();
  res.end();
}

function listGroupedBody(): ReturnType<typeof emptyGrouped> & {
  ungrouped: ConversationSummary[];
} {
  const grouped = emptyGrouped();
  grouped.ungrouped = [...conversations.values()].map((c) => c.summary);
  return grouped;
}

async function route(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<void> {
  const method = (req.method ?? "GET").toUpperCase();
  const url = new URL(req.url ?? "/", `http://${HOST}:${PORT}`);
  const path = url.pathname;

  if (method === "OPTIONS") {
    cors(req, res);
    res.writeHead(204);
    res.end();
    return;
  }

  if (method === "GET" && path === "/readyz") {
    json(req, res, 200, readyzOk());
    return;
  }

  if (method === "GET" && path === "/v1/auth/me") {
    if (!isAuthed(req)) {
      json(req, res, 401, {
        error: { code: "unauthenticated", message: "not logged in" },
      });
      return;
    }
    json(req, res, 200, MOCK_USER);
    return;
  }

  if (method === "POST" && path === "/v1/auth/register/send-code") {
    await readBody(req);
    json(req, res, 202, emailCodeAccepted());
    return;
  }

  if (method === "POST" && path === "/v1/auth/register/verify") {
    await readBody(req);
    json(req, res, 201, MOCK_USER);
    return;
  }

  if (method === "POST" && path === "/v1/auth/password/forgot") {
    await readBody(req);
    json(req, res, 202, emailCodeAccepted());
    return;
  }

  if (method === "POST" && path === "/v1/auth/password/reset") {
    await readBody(req);
    json(req, res, 200, { status: "ok" });
    return;
  }

  if (method === "POST" && path === "/v1/auth/login") {
    await readBody(req);
    // The handshake is where the token is handed out — see §八.
    json(req, res, 200, loginOk(), {
      "Set-Cookie": COOKIE,
      "X-CSRF-Token": CSRF,
    });
    return;
  }

  if (method === "POST" && path === "/v1/auth/refresh") {
    if (!isAuthed(req)) {
      json(req, res, 401, {
        error: { code: "unauthenticated", message: "no session" },
      });
      return;
    }
    json(req, res, 200, statusOk(), {
      "Set-Cookie": COOKIE,
      "X-CSRF-Token": CSRF,
    });
    return;
  }

  // Remaining /v1/* require a cookie (AuthGate + api layer).
  if (path.startsWith("/v1/") && !isAuthed(req)) {
    json(req, res, 401, {
      error: { code: "unauthenticated", message: "not logged in" },
    });
    return;
  }

  if (csrfRejected(req, method, path)) {
    csrfFailed(req, res);
    return;
  }

  if (method === "POST" && path === "/v1/auth/email/send-code") {
    await readBody(req);
    json(req, res, 202, {});
    return;
  }

  if (method === "POST" && path === "/v1/auth/email/verify") {
    await readBody(req);
    json(req, res, 200, MOCK_USER);
    return;
  }

  if (method === "GET" && path === "/v1/conversations") {
    const data = [...conversations.values()].map((c) => c.summary);
    json(req, res, 200, {
      data,
      page: 1,
      page_size: 100,
      total: data.length,
    });
    return;
  }

  if (method === "GET" && path === "/v1/conversations/grouped") {
    json(req, res, 200, listGroupedBody());
    return;
  }

  if (method === "POST" && path === "/v1/conversations") {
    const raw = await readBody(req);
    let folderId: string | null = null;
    let localRoot: string | null = null;
    let axes: NonNullable<ConversationSummary["permission_axes"]> = {
      file_write: "session",
      command: "auto",
      team_kickoff: "rules",
      host: "session",
    };
    try {
      const body = JSON.parse(raw) as {
        folder_id?: string | null;
        local_container_root_id?: string | null;
        permission_axes?: ConversationSummary["permission_axes"];
      };
      folderId = body.folder_id ?? null;
      localRoot = body.local_container_root_id ?? null;
      if (body.permission_axes) axes = body.permission_axes;
    } catch {
      /* defaults */
    }
    const id = randomUUID().replace(/-/g, "");
    const summary = conversationSummary({
      id,
      folder_id: folderId,
      local_container_root_id: localRoot,
      permission_axes: axes,
    });
    conversations.set(id, { summary, recovery: emptyConvRecovery() });
    json(req, res, 200, summary);
    return;
  }

  const msgPost = /^\/v1\/conversations\/([^/]+)\/messages$/.exec(path);
  if (method === "POST" && msgPost) {
    await handleMessagesPost(req, res, msgPost[1]);
    return;
  }

  const msgGet = /^\/v1\/conversations\/([^/]+)\/messages$/.exec(path);
  if (method === "GET" && msgGet) {
    if (msgGet[1] === HYDRATE_FAIL_CONV_ID) {
      json(req, res, 500, {
        error: { code: "internal", message: "e2e hydrate fail" },
      });
      return;
    }
    json(req, res, 200, emptyMessages());
    return;
  }

  const recovery = /^\/v1\/conversations\/([^/]+)\/recovery$/.exec(path);
  if (method === "GET" && recovery) {
    const conversationId = recovery[1];
    // Navigate-after-create races loadRecovery with the first POST .../messages.
    // Wait briefly so the script can sync pending/paused before we answer — otherwise
    // an early empty snapshot wipes live cards (hydratePending / setForConversation).
    const deadline = Date.now() + 2_000;
    while (Date.now() < deadline) {
      const conv = conversations.get(conversationId);
      if (!conv) break;
      if (
        conv.plan ||
        hotHolds.has(conversationId) ||
        (conv.recovery.paused?.length ?? 0) > 0 ||
        (conv.recovery.pending_interactions?.length ?? 0) > 0 ||
        conv.summary.message_count > 0
      ) {
        // Give the initial SSE segment a tick to syncRecoveryFromEvents.
        if (
          !(
            (conv.recovery.paused?.length ?? 0) > 0 ||
            (conv.recovery.pending_interactions?.length ?? 0) > 0 ||
            conv.plan?.kind === "complete"
          ) &&
          conv.plan
        ) {
          await sleep(30);
          continue;
        }
        break;
      }
      await sleep(40);
    }
    const conv = conversations.get(conversationId);
    json(req, res, 200, conv?.recovery ?? emptyRecovery());
    return;
  }

  const streamGet = /^\/v1\/conversations\/([^/]+)\/stream$/.exec(path);
  if (method === "GET" && streamGet) {
    // First-batch pool: no live reattach. Close immediately.
    beginSse(req, res);
    res.end();
    return;
  }

  const interactions =
    /^\/v1\/conversations\/([^/]+)\/interactions\/([^/]+)$/.exec(path);
  if (method === "POST" && interactions) {
    await handleInteractionsPost(req, res, interactions[1]);
    return;
  }

  const resume =
    /^\/v1\/conversations\/([^/]+)\/messages\/([^/]+)\/resume$/.exec(path);
  if (method === "POST" && resume) {
    await handleResumePost(req, res, resume[1]);
    return;
  }

  if (method === "GET" && path === "/v1/workspaces") {
    json(req, res, 200, emptyWorkspaces());
    return;
  }

  if (method === "GET" && path === "/v1/users/me/autonomy") {
    json(req, res, 200, { policy: "less_interrupt" });
    return;
  }

  const permAxes =
    /^\/v1\/conversations\/([^/]+)\/permission-axes$/.exec(path);
  if (method === "PUT" && permAxes) {
    const id = permAxes[1];
    const entry = conversations.get(id);
    if (!entry) {
      json(req, res, 404, {
        error: { code: "not_found", message: "conversation not found" },
      });
      return;
    }
    const raw = await readBody(req);
    try {
      const body = JSON.parse(raw) as {
        permission_axes?: ConversationSummary["permission_axes"];
      };
      if (body.permission_axes) {
        entry.summary = {
          ...entry.summary,
          permission_axes: body.permission_axes,
        };
      }
    } catch {
      /* keep prior */
    }
    json(req, res, 200, entry.summary);
    return;
  }

  // Soft stubs for non-critical polls so the shell stays quiet.
  if (method === "GET" && path === "/v1/capabilities") {
    json(req, res, 200, {});
    return;
  }

  const browserSessions =
    /^\/v1\/conversations\/([^/]+)\/browser\/sessions$/.exec(path);
  if (method === "GET" && browserSessions) {
    json(req, res, 200, { data: [], active_session_id: null });
    return;
  }

  json(req, res, 404, {
    error: { code: "not_found", message: `${method} ${path}` },
  });
}

const server = createServer((req, res) => {
  void route(req, res).catch((err) => {
    console.error("[e2e-mock]", err);
    if (!res.headersSent) {
      json(req, res, 500, {
        error: { code: "internal", message: String(err) },
      });
    } else if (!res.writableEnded) {
      res.end();
    }
  });
});

server.listen(PORT, HOST, () => {
  console.log(`[e2e-mock] listening on http://${HOST}:${PORT}`);
});

function shutdown(): void {
  for (const hold of hotHolds.values()) {
    hold.closed = true;
    if (!hold.res.writableEnded) hold.res.end();
  }
  hotHolds.clear();
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
