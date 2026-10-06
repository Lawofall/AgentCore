import { isWebRuntime } from "@/lib/capabilities";
import { clientHeaders } from "@/lib/clientBuildInfo";
import { isWebPreview } from "@/lib/preview";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import {
  BASE_URL,
  captureCsrf,
  getCsrfHeaders,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import { failInflightClientToolsForReconnect } from "@/services/clientToolFulfill";
import {
  getDeviceId,
  resetDeviceIdentityForTests,
} from "@/services/deviceIdentity";

/**
 * Device-level fulfill firehose client (`GET /v1/fulfill`).
 *
 * Long-lived SSE carries CLIENT_TOOL `*_required` frames (and
 * `client_tool_cancelled`) to the machine that can actually run them — independent
 * of which conversation SSE the UI is watching. It also carries account state
 * that belongs to no single conversation (queue, settled decision cards), folded
 * by {@link installAccountStateIngress}. Mirrors {@link startRealtime}'s transport
 * posture (401→refresh→reconnect, capped exponential backoff) but is a
 * **separate** connection with `device_id` + caps + permanent roots.
 *
 * Only the **permanent** roots are declared here. A conversation grant is bound
 * to this device by the server when the desktop registers it, and re-seeded from
 * that binding on every reconnect — the client re-declaring its whole grant set
 * was a second source for a fact the server already owns, and the window before
 * it landed was where a new mount's first op met an empty hub.
 *
 * **Two shapes of connection ride this one endpoint.** An Electron install
 * connects as a *fulfiller*: durable `device_id`, caps, roots — ops land here.
 * The browser client connects as an *observer*: no caps, no roots, no durable
 * identity. The account state on this stream is the account's, not a machine's,
 * so a web tab has the same claim on it as a desktop does; what a web tab must
 * never do is look like somewhere a local op could land. Declaring zero caps is
 * that line — the server's selection filters on them, and its presence answers
 * skip a session that can fulfil nothing. Web is also the reason there is no
 * reconcile fallback to fall back to: the frames carry whole facts, and this is
 * the only channel that delivers them.
 *
 * Transport only: op execution / settle is owned by the fulfill consumer (D2)
 * via {@link onFulfillFrame}.
 *
 * One live socket per page. A newer subscribe for the same device sends
 * `superseded`; that copy stops instead of reconnecting (it would only kill
 * the socket that just took over). Backoff resets after the stream has been
 * quiet-healthy — a heartbeat, or open longer than one server heartbeat —
 * not when the HTTP status first comes back 200.
 */

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
/** Server fulfill heartbeat (`_HEARTBEAT_SECONDS`). */
const HEARTBEAT_MS = 25_000;
/** Two heartbeat intervals plus a margin, with no bytes at all. */
const IDLE_TIMEOUT_MS = HEARTBEAT_MS * 2 + 10_000;
const SUPERSEDED_TYPE = "superseded";

type FulfillSlot = {
  running: boolean;
  controller: AbortController | null;
  reconnectTimer: number | null;
  attempts: number;
  lastKnownRoots: string[] | null;
  observerId: string | null;
  listeners: Set<FulfillFrameListener>;
};

const SLOT_KEY = "__agentcoreFulfillStream";

function slot(): FulfillSlot {
  const g = globalThis as typeof globalThis & { [SLOT_KEY]?: FulfillSlot };
  if (!g[SLOT_KEY]) {
    g[SLOT_KEY] = {
      running: false,
      controller: null,
      reconnectTimer: null,
      attempts: 0,
      lastKnownRoots: null,
      observerId: null,
      listeners: new Set(),
    };
  }
  return g[SLOT_KEY];
}

/** Caps advertised on every connect (comma-joined query param). */
export const FULFILL_CAPS = [
  "workspace",
  "host",
  "mcp",
  "external_mount",
] as const;

/**
 * Parsed fulfill SSE payload.
 * - `{ type: "ready" }` on connect
 * - existing CLIENT_TOOL `*_required` shapes (type string unchanged)
 * - `{ type: "client_tool_cancelled", request_id }`
 */
export type FulfillFrame = {
  type: string;
  request_id?: string;
  payload?: unknown;
  [key: string]: unknown;
};

export type FulfillFrameListener = (frame: FulfillFrame) => void;

type StreamOutcome = "reconnect" | "stop" | "superseded";

/** True when this runtime only reads account state and fulfils nothing. */
function isObserver(): boolean {
  return isWebRuntime();
}

/**
 * The `device_id` a browser tab connects under.
 *
 * Per page load rather than persisted: the hub keys one live session per
 * `(user, device_id)`, so two tabs sharing an id would take turns closing each
 * other's stream. Nothing is lost by minting a fresh one — connect replays the
 * account state a session could have missed, and this id never reaches
 * `X-Client-Device` (see `clientBuildInfo`), which is what pins a turn's local
 * ops to a machine.
 */
function observerConnectionId(): string {
  const state = slot();
  if (!state.observerId) state.observerId = `web-${crypto.randomUUID()}`;
  return state.observerId;
}

function emitFrame(frame: FulfillFrame): void {
  for (const cb of slot().listeners) {
    try {
      cb(frame);
    } catch {
      /* listener errors must not kill the stream */
    }
  }
}

/**
 * Outcome of reading the local grant set. `ok: false` means **unknown**, which
 * is not the same fact as "this device holds no root".
 */
type RootsRead = { ok: true; roots: string[] } | { ok: false };

/**
 * Read this device's permanent authorized root ids from the main process.
 *
 * Permanent roots are the ones the server cannot know on its own: they are
 * created by the user in settings, not by a registration request. Conversation
 * grants are deliberately absent — the server binds each of those to this device
 * as it records them.
 *
 * A rejected / unavailable read must never surface as `[]`: declaring the empty
 * set tells the hub this device fulfils nothing rooted, and the connect that
 * said so stands until the next reconnect. Callers act on `ok: false` by
 * re-declaring the last set they actually saw.
 */
async function readRootIds(): Promise<RootsRead> {
  try {
    const fsApi = window.fsApi;
    if (!fsApi?.listRoots) {
      throw new Error("fsApi.listRoots 不可用");
    }
    const roots = await fsApi.listRoots();
    if (!Array.isArray(roots)) {
      throw new Error("fs:listRoots 返回了非数组");
    }
    const ids = roots
      .map((r) => r?.id)
      .filter((id): id is string => typeof id === "string" && id.length > 0)
      .sort();
    slot().lastKnownRoots = ids;
    return { ok: true, roots: ids };
  } catch (err) {
    console.warn("[fulfill] 读取本地永久根失败：沿用上次读到的那份", err);
    return { ok: false };
  }
}

/** Parse one SSE frame and fan out to listeners (skips heartbeat comments). */
function handleFrame(frame: string): void {
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return;
  try {
    const event = JSON.parse(dataLines.join("\n")) as FulfillFrame;
    if (typeof event?.type !== "string" || !event.type) return;
    emitFrame(event);
  } catch {
    /* malformed frame — skip */
  }
}

function buildFulfillUrl(
  deviceId: string,
  caps: readonly string[],
  roots: readonly string[],
): string {
  const params = new URLSearchParams();
  params.set("device_id", deviceId);
  params.set("caps", caps.join(","));
  params.set("roots", roots.join(","));
  return `${BASE_URL}/v1/fulfill?${params.toString()}`;
}

/** What this connection declares it can do — nothing at all, for an observer. */
async function declaration(): Promise<{
  caps: readonly string[];
  roots: readonly string[];
}> {
  if (isObserver()) return { caps: [], roots: [] };
  // `hub.register` rebuilds this device's session from whatever `roots` carries
  // (plus the grants the server has bound to it), so an unreadable local set
  // re-declares the last one we actually saw rather than retracting roots the
  // device can still fulfil. Stale ids cost nothing: the main process
  // re-authorizes every op against the real grant store.
  const read = await readRootIds();
  return {
    caps: FULFILL_CAPS,
    roots: read.ok ? read.roots : (slot().lastKnownRoots ?? []),
  };
}

function isKeepAlive(frame: string): boolean {
  const lines = frame
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  return lines.length > 0 && lines.every((line) => line.startsWith(":"));
}

function frameType(frame: string): string | null {
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  try {
    const event = JSON.parse(dataLines.join("\n")) as { type?: unknown };
    return typeof event.type === "string" ? event.type : null;
  } catch {
    return null;
  }
}

/**
 * Fetch signal that follows `parent` (user stop) and can also die on idle
 * without looking like a stop. Idle abort must still reconnect.
 */
function linkedSignal(parent: AbortSignal): {
  signal: AbortSignal;
  abortIdle: () => void;
  dispose: () => void;
} {
  const link = new AbortController();
  const follow = () => link.abort(parent.reason);
  if (parent.aborted) follow();
  else parent.addEventListener("abort", follow);
  return {
    signal: link.signal,
    abortIdle: () => link.abort("idle"),
    dispose: () => parent.removeEventListener("abort", follow),
  };
}

async function runStream(
  signal: AbortSignal,
  deviceId: string,
): Promise<StreamOutcome> {
  const { caps, roots } = await declaration();
  const link = linkedSignal(signal);
  let response: Response;
  try {
    response = await fetch(buildFulfillUrl(deviceId, caps, roots), {
      method: "GET",
      credentials: sessionCredentials(),
      headers: {
        Accept: "text/event-stream",
        ...clientHeaders(),
        ...bearerAuthHeader(),
        ...getCsrfHeaders("GET"),
      },
      signal: link.signal,
    });
    captureCsrf(response); // 履约长连接是本端最常开的一条，令牌从这里续
  } catch {
    link.dispose();
    return "reconnect";
  }

  if (response.status === 401) {
    link.dispose();
    const outcome = await tryRefresh();
    if (outcome === "renewed" || outcome === "transient") return "reconnect";
    notifyUnauthorized();
    return "stop";
  }
  if (!response.ok || !response.body) {
    link.dispose();
    return "reconnect";
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawSuperseded = false;
  let idleTimer: number | null = null;
  let stableTimer: number | null = null;
  const markStable = () => {
    slot().attempts = 0;
  };
  const armIdle = () => {
    if (idleTimer !== null) window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => link.abortIdle(), IDLE_TIMEOUT_MS);
  };
  const clearWatch = () => {
    if (idleTimer !== null) window.clearTimeout(idleTimer);
    if (stableTimer !== null) window.clearTimeout(stableTimer);
    idleTimer = null;
    stableTimer = null;
  };
  armIdle();
  stableTimer = window.setTimeout(markStable, HEARTBEAT_MS);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      armIdle();
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        if (isKeepAlive(frame)) markStable();
        if (frameType(frame) === SUPERSEDED_TYPE) sawSuperseded = true;
        handleFrame(frame);
      }
    }
  } catch {
    return sawSuperseded ? "superseded" : "reconnect";
  } finally {
    clearWatch();
    link.dispose();
  }
  return sawSuperseded ? "superseded" : "reconnect";
}

function scheduleReconnect(): void {
  const state = slot();
  if (!state.running || state.reconnectTimer !== null) return;
  const delay = Math.min(
    RECONNECT_BASE_MS * 2 ** state.attempts,
    RECONNECT_MAX_MS,
  );
  state.attempts += 1;
  state.reconnectTimer = window.setTimeout(
    () => {
      slot().reconnectTimer = null;
      void connect();
    },
    delay + Math.random() * 500,
  );
}

async function connect(): Promise<void> {
  const state = slot();
  if (!state.running) return;
  let deviceId: string;
  if (isObserver()) {
    deviceId = observerConnectionId();
  } else {
    try {
      deviceId = await getDeviceId();
    } catch {
      // Electron shell with no durable identity (missing preload) — a fulfiller
      // that cannot name itself has nothing to reconnect for.
      slot().running = false;
      return;
    }
  }
  const ac = new AbortController();
  slot().controller = ac;
  let outcome: StreamOutcome = "reconnect";
  try {
    outcome = await runStream(ac.signal, deviceId);
  } catch {
    outcome = "reconnect";
  }
  if (ac.signal.aborted || !slot().running) return;
  if (outcome === "stop" || outcome === "superseded") {
    // A newer connect may already own the slot. Only the copy that was
    // replaced should stand down.
    if (slot().controller === ac) slot().running = false;
    return;
  }
  // Already-running workspace ops: fail-fast so the server does not wait out
  // the settle deadline. Not-yet-delivered ops still use reconnect grace.
  // A superseded stream must not fail them — the connection that took over
  // rehangs those ops.
  failInflightClientToolsForReconnect("cloud");
  scheduleReconnect();
}

/**
 * Open the fulfill firehose for the current session (idempotent).
 *
 * Skipped only under `#/preview`, which replays vectors with no backend behind
 * it. The web client runs the real thing, as an observer.
 */
export function startFulfillStream(): void {
  if (isWebPreview()) return;
  const state = slot();
  if (state.running) return;
  state.running = true;
  state.attempts = 0;
  void connect();
}

/** Close the fulfill firehose and cancel pending reconnect / polls (idempotent). */
export function stopFulfillStream(): void {
  const state = slot();
  state.running = false;
  if (state.reconnectTimer !== null) {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  state.controller?.abort();
  state.controller = null;
}

/**
 * Subscribe to parsed fulfill frames (`ready`, `*_required`, `client_tool_cancelled`).
 * Returns an unsubscribe function.
 */
export function onFulfillFrame(cb: FulfillFrameListener): () => void {
  const listeners = slot().listeners;
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Test-only: reset module state between cases. */
export function resetFulfillStreamForTests(): void {
  stopFulfillStream();
  resetDeviceIdentityForTests();
  const g = globalThis as typeof globalThis & { [SLOT_KEY]?: FulfillSlot };
  delete g[SLOT_KEY];
}
