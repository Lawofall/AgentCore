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
 */

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

/** Caps advertised on every connect (comma-joined query param). */
export const FULFILL_CAPS = [
  "workspace",
  "host",
  "mcp",
  "board",
  "board_read",
  "notify",
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

type StreamOutcome = "reconnect" | "stop";

let running = false;
let controller: AbortController | null = null;
let reconnectTimer: number | null = null;
let attempts = 0;
/** Last root set actually read off the main process (`null` = never read one). */
let lastKnownRoots: string[] | null = null;
/** Observer connection id, minted on first connect (`null` = not minted yet). */
let observerId: string | null = null;
const listeners = new Set<FulfillFrameListener>();

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
  if (!observerId) observerId = `web-${crypto.randomUUID()}`;
  return observerId;
}

function emitFrame(frame: FulfillFrame): void {
  for (const cb of listeners) {
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
    lastKnownRoots = ids;
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
    roots: read.ok ? read.roots : (lastKnownRoots ?? []),
  };
}

async function runStream(
  signal: AbortSignal,
  deviceId: string,
): Promise<StreamOutcome> {
  const { caps, roots } = await declaration();
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
      signal,
    });
    captureCsrf(response); // 履约长连接是本端最常开的一条，令牌从这里续
  } catch {
    return "reconnect";
  }

  if (response.status === 401) {
    const outcome = await tryRefresh();
    if (outcome === "renewed" || outcome === "transient") return "reconnect";
    notifyUnauthorized();
    return "stop";
  }
  if (!response.ok || !response.body) return "reconnect";

  attempts = 0;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) handleFrame(frame);
    }
  } catch {
    return "reconnect";
  }
  return "reconnect";
}

function scheduleReconnect(): void {
  if (!running || reconnectTimer !== null) return;
  const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempts, RECONNECT_MAX_MS);
  attempts += 1;
  reconnectTimer = window.setTimeout(
    () => {
      reconnectTimer = null;
      void connect();
    },
    delay + Math.random() * 500,
  );
}

async function connect(): Promise<void> {
  if (!running) return;
  let deviceId: string;
  if (isObserver()) {
    deviceId = observerConnectionId();
  } else {
    try {
      deviceId = await getDeviceId();
    } catch {
      // Electron shell with no durable identity (missing preload) — a fulfiller
      // that cannot name itself has nothing to reconnect for.
      running = false;
      return;
    }
  }
  const ac = new AbortController();
  controller = ac;
  let outcome: StreamOutcome = "reconnect";
  try {
    outcome = await runStream(ac.signal, deviceId);
  } catch {
    outcome = "reconnect";
  }
  if (ac.signal.aborted || !running) return;
  if (outcome === "stop") {
    running = false;
    return;
  }
  // Already-running workspace ops: fail-fast so the server does not wait out
  // the settle deadline. Not-yet-delivered ops still use reconnect grace.
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
  if (running) return;
  running = true;
  attempts = 0;
  void connect();
}

/** Close the fulfill firehose and cancel pending reconnect / polls (idempotent). */
export function stopFulfillStream(): void {
  running = false;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  controller?.abort();
  controller = null;
}

/**
 * Subscribe to parsed fulfill frames (`ready`, `*_required`, `client_tool_cancelled`).
 * Returns an unsubscribe function.
 */
export function onFulfillFrame(cb: FulfillFrameListener): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Test-only: reset module state between cases. */
export function resetFulfillStreamForTests(): void {
  stopFulfillStream();
  attempts = 0;
  resetDeviceIdentityForTests();
  lastKnownRoots = null;
  observerId = null;
  listeners.clear();
}
