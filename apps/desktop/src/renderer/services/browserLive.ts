import { clientHeaders } from "@/lib/clientBuildInfo";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import {
  BASE_URL,
  captureCsrf,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";

/**
 * L3「团队浏览器」M1 直播帧客户端 (提案 D13–D15)。
 *
 * 一条 `GET …/browser/live` fetch 式 SSE 附着通道（可选 `?session_id=` 钉到具体 tab）：观看者
 * 附着才开播（无人看零开销）。帧走**旁路非 journal 通道**——EPHEMERAL、无 id/seq、live-only，
 * 绝不混入回合事件分派 / 回放态（故这里独立于 `services/sse` 的 dispatch，用本地 narrow 信封
 * 自解析）。
 *
 * 断线策略沿 `realtime.ts`：SSE 无法中途换 token，故 401 → 刷新一次后重连，否则跳登录；传输层
 * 掉线按上限指数退避重连。与 `realtime.ts` 的模块级单例不同，直播随「浏览器直播」tab 的挂载/卸载
 * 起停，故建成实例工厂：每次 {@link startBrowserLive} 独立持有连接与重连状态，`stop()` 收口。
 */

/** 一帧直播画面：base64 jpeg + 原始像素尺寸。信封 `type:"browser_live_frame"` 的 payload。 */
export interface BrowserLiveFrame {
  /** jpeg 图像的 base64（不含 data: 前缀）。 */
  frame_b64: string;
  width: number;
  height: number;
}

/**
 * 服务端上报的直播会话态（信封 `type:"browser_live_status"` 的 payload.state）：
 * - `started`：已附着到一个进行中的浏览器会话，帧随后到来；
 * - `no_session`：当前没有进行中的浏览器会话（无直播）；
 * - `session_closed`：浏览器会话已结束（直播终止）。
 */
export type BrowserLiveState = "started" | "no_session" | "session_closed";

/**
 * 客户端本地的连接生命周期（区别于服务端的 {@link BrowserLiveState}）——驱动「连接中 / 断线
 * 重连中」这类纯传输态 UI：
 * - `connecting`：首次建连中；
 * - `open`：流已打开（收到 200 响应体）；
 * - `reconnecting`：掉线，退避重连中；
 * - `closed`：认证失效被收口（`stop()` 不再回调，故不发此态）。
 */
export type BrowserLiveConnection =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

/** 直播事件回调集。onFrame 逐帧到达；onStatus 服务端态切换；onConnection 传输态切换。 */
export interface BrowserLiveHandlers {
  onFrame: (frame: BrowserLiveFrame) => void;
  onStatus: (state: BrowserLiveState) => void;
  onConnection: (connection: BrowserLiveConnection) => void;
}

/** 一个直播连接的把手：`stop()` 幂等收口（中止连接 + 取消待重连 + 静默后续回调）。 */
export interface BrowserLiveClient {
  stop: () => void;
}

/** 可选钉到 Registry 某一 tab（`serverSessionId`）；省略则后端解析会话唯一/激活页。 */
export type BrowserLiveOpts = {
  sessionId?: string | null;
};

/** 本地 narrow 信封：只认直播通道的两类事件，其余（心跳注释 / ready 等）忽略。 */
type BrowserLiveEvent =
  | { type: "browser_live_frame"; payload: BrowserLiveFrame }
  | { type: "browser_live_status"; payload: { state: BrowserLiveState } };

type StreamOutcome = "reconnect" | "stop";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

/**
 * 附着一条会话直播流并把帧/态推给回调，返回可收口的把手。附着即向服务端表明「有观看者」→ 开播。
 * 组件挂载时调用、卸载时 `stop()`，从而实现「无人看零开销」。
 * 传入 {@link BrowserLiveOpts.sessionId} 时 URL 带 `?session_id=`，避免多 tab 串帧。
 */
export function startBrowserLive(
  conversationId: string,
  handlers: BrowserLiveHandlers,
  opts?: BrowserLiveOpts,
): BrowserLiveClient {
  const sid = opts?.sessionId?.trim();
  const qs = sid ? `?session_id=${encodeURIComponent(sid)}` : "";
  const url = `${BASE_URL}/v1/conversations/${encodeURIComponent(
    conversationId,
  )}/browser/live${qs}`;

  let running = true;
  let controller: AbortController | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;

  /** Emit a connection-state change unless already stopped (never touch a dead consumer). */
  function emitConnection(connection: BrowserLiveConnection): void {
    if (running) handlers.onConnection(connection);
  }

  /** Parse one SSE frame (lines split by \n) and route the live-only envelope. */
  function handleFrame(frame: string): void {
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0) return; // heartbeat comment / event-only frame
    let event: BrowserLiveEvent;
    try {
      event = JSON.parse(dataLines.join("\n")) as BrowserLiveEvent;
    } catch {
      return; // malformed frame — skip
    }
    if (!running) return;
    if (event.type === "browser_live_frame") {
      handlers.onFrame(event.payload);
    } else if (event.type === "browser_live_status") {
      handlers.onStatus(event.payload.state);
    }
    // Any other type: no-op (this channel only carries frame + status).
  }

  /** Open the stream and pump frames until it ends; returns how to proceed. */
  async function runStream(signal: AbortSignal): Promise<StreamOutcome> {
    let response: Response;
    try {
      response = await fetch(url, {
        method: "GET",
        credentials: sessionCredentials(),
        headers: {
          Accept: "text/event-stream",
          ...clientHeaders(),
          ...bearerAuthHeader(),
        },
        signal,
      });
      captureCsrf(response);
    } catch {
      return "reconnect"; // transport failure (offline / aborted)
    }

    if (response.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed" || outcome === "transient") return "reconnect";
      notifyUnauthorized();
      return "stop";
    }
    if (!response.ok || !response.body) return "reconnect";

    // Connected: reset backoff and let the UI drop any「重连中」chrome.
    attempts = 0;
    emitConnection("open");

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
      return "reconnect"; // read error (incl. abort — caller checks the signal)
    }
    return "reconnect"; // server closed the stream
  }

  function scheduleReconnect(): void {
    if (!running || reconnectTimer !== null) return;
    emitConnection("reconnecting");
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempts, RECONNECT_MAX_MS);
    attempts += 1;
    reconnectTimer = setTimeout(
      () => {
        reconnectTimer = null;
        void connect();
      },
      delay + Math.random() * 500,
    );
  }

  async function connect(): Promise<void> {
    if (!running) return;
    const ac = new AbortController();
    controller = ac;
    let outcome: StreamOutcome = "reconnect";
    try {
      outcome = await runStream(ac.signal);
    } catch {
      outcome = "reconnect";
    }
    if (ac.signal.aborted || !running) return;
    if (outcome === "stop") {
      // Auth is dead (login handler already fired). Stop retrying; the consumer is
      // about to be torn down with the app's drop-to-login, so no further callbacks.
      running = false;
      return;
    }
    scheduleReconnect();
  }

  emitConnection("connecting");
  void connect();

  return {
    stop(): void {
      running = false;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      controller?.abort();
      controller = null;
    },
  };
}
