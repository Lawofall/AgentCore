import {
  conversationHasBrowserActivity,
  conversationHasPendingBrowserLogin,
} from "@/lib/browserActivity";
import {
  type InputBatcher,
  createInputBatcher,
  modifiersOf,
  sendBrowserInput,
  toFrameSpace,
} from "@/services/browserInput";
import {
  type BrowserLiveConnection,
  type BrowserLiveState,
  startBrowserLive,
} from "@/services/browserLive";
import { useBrowserSessionsStore } from "@/stores/browserSessions";
import { useConversationStore } from "@/stores/conversation";
import { runtimeOf } from "@/stores/conversation/runtime";
import { useExecutionStore } from "@/stores/execution";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import {
  Loader2,
  type LucideIcon,
  MonitorOff,
  Radio,
  WifiOff,
} from "lucide-react";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useEffect,
  useRef,
  useState,
} from "react";

/**
 * 浏览器直播 tab body——桌面首个「按帧刷新」组件。
 *
 * 附着一条 `…/browser/live` SSE 直播流（{@link startBrowserLive}），把 base64 jpeg 帧转成
 * objectURL **逐帧换图**：每来一帧换 `<img src>`、并回收上一帧的 objectURL（防内存泄漏）。覆盖
 * 连接中 / 无直播(no_session) / 会话已结束(session_closed) / 断线重连各态。挂载即附着（开播）、
 * 卸载即收口（停播）——由 SidePanel 仅在「浏览器」tab 激活时挂载本组件，实现「无人看零开销」。
 * tab 自身条件常驻（{@link useBrowserRegion}），故本组件在「本会话用过浏览器、但此刻无直播」时
 * 也会被挂载 —— `no_session` 占位态正是这条常态路径的正文，不是异常。
 *
 * 非登录只看。仅 pending `browser_login`（escalate 或 CEO ask_user）且有活直播时，画面变可交互
 * 面——捕获点击/键盘/滚轮，把展示坐标 {@link toFrameSpace} 换算到帧像素空间，经
 * {@link createInputBatcher} 攒批 POST（避免事件洪泛）。登录中短提示引导回对话点「已登录，继续」。
 * 密码等键入不回显不留存（缓冲仅在飞、不落任何持久缓存）。
 */

/** base64（不含 data: 前缀）→ Blob，供 `URL.createObjectURL` 逐帧换图。 */
function base64ToBlob(b64: string, mime: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

interface Placeholder {
  Icon: LucideIcon;
  spin?: boolean;
  tone: "muted" | "warning";
  title: string;
  hint?: string;
}

/** No-frame body: choose the icon + copy for the current transport / session state. */
function placeholderFor(
  connection: BrowserLiveConnection,
  status: BrowserLiveState | null,
): Placeholder {
  if (status === "no_session") {
    return {
      Icon: MonitorOff,
      tone: "muted",
      title: "当前没有进行中的直播",
      hint: "AI 开始使用浏览器后，画面会实时出现在这里",
    };
  }
  if (status === "session_closed") {
    return {
      Icon: MonitorOff,
      tone: "muted",
      title: "直播已结束",
      hint: "浏览器会话已关闭",
    };
  }
  if (connection === "reconnecting") {
    return { Icon: WifiOff, tone: "warning", title: "连接已断开，正在重连…" };
  }
  if (status === "started") {
    return { Icon: Loader2, spin: true, tone: "muted", title: "等待画面…" };
  }
  return { Icon: Loader2, spin: true, tone: "muted", title: "连接中…" };
}

function LivePlaceholder({
  connection,
  status,
}: {
  connection: BrowserLiveConnection;
  status: BrowserLiveState | null;
}) {
  const { Icon, spin, tone, title, hint } = placeholderFor(connection, status);
  const toneClass =
    tone === "warning" ? "text-warning" : "text-muted-foreground";
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 text-center">
      <Icon
        size={26}
        className={`${toneClass} ${spin ? "animate-spin" : ""}`}
      />
      <p className={`text-sm ${toneClass}`}>{title}</p>
      {hint && <p className="text-xs text-muted-foreground/70">{hint}</p>}
    </div>
  );
}

export function BrowserLivePanel({
  conversationId,
  sessionId,
}: {
  conversationId: string;
  /** Registry tab pin — live SSE 带上。 */
  sessionId?: string | null;
}) {
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<BrowserLiveState | null>(null);
  const [connection, setConnection] =
    useState<BrowserLiveConnection>("connecting");
  const pendingEscalationLogin = useExecutionStore((s) =>
    conversationHasPendingBrowserLogin(
      runtimeOf(useConversationStore.getState(), conversationId).messages,
      s.byId,
    ),
  );
  const pendingAskUserLogin = usePausedTurnStore((s) =>
    s.pending.some(
      (p) =>
        p.conversationId === conversationId &&
        p.kind === "ask_user" &&
        p.browserLogin === true,
    ),
  );
  const pendingBrowserLogin = pendingEscalationLogin || pendingAskUserLogin;
  // Track the live object URL outside React state so the cleanup / next-frame swap can
  // revoke the previous one synchronously (state is async, and a stale closure would leak).
  const frameUrlRef = useRef<string | null>(null);
  // Latest frame's pixel dimensions — the coordinate space input events must map into.
  const frameDimRef = useRef<{ width: number; height: number } | null>(null);

  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const batcherRef = useRef<InputBatcher | null>(null);
  const draggingRef = useRef(false);
  const composingRef = useRef(false);

  useEffect(() => {
    const client = startBrowserLive(
      conversationId,
      {
        onFrame: (frame) => {
          frameDimRef.current = { width: frame.width, height: frame.height };
          const next = URL.createObjectURL(
            base64ToBlob(frame.frame_b64, "image/jpeg"),
          );
          const prev = frameUrlRef.current;
          frameUrlRef.current = next;
          setFrameUrl(next);
          // 换帧即回收上一帧：旧帧已绘制、<img> 已切到新 src，旧 objectURL 无人再引用 → revoke 防泄漏。
          if (prev) URL.revokeObjectURL(prev);
        },
        onStatus: setStatus,
        onConnection: setConnection,
      },
      sessionId ? { sessionId } : undefined,
    );
    return () => {
      client.stop();
      if (frameUrlRef.current) {
        URL.revokeObjectURL(frameUrlRef.current);
        frameUrlRef.current = null;
      }
    };
  }, [conversationId, sessionId]);

  // 有帧且非「无直播」→ 放画面（会话结束时保留最后一帧、叠加结束提示；重连时保留最后一帧、叠加重连提示）。
  const showFrame = frameUrl !== null && status !== "no_session";
  const isLive =
    showFrame && status !== "session_closed" && connection === "open";
  // 仅 pending 登录 + 活直播 → 画面可点；否则只看。
  const inputEnabled =
    pendingBrowserLogin &&
    showFrame &&
    status === "started" &&
    connection === "open";

  useEffect(() => {
    if (!inputEnabled) return;
    const batcher = createInputBatcher((events) =>
      sendBrowserInput(
        conversationId,
        events,
        sessionId ? { sessionId } : undefined,
      ),
    );
    batcherRef.current = batcher;
    return () => {
      batcher.stop();
      if (batcherRef.current === batcher) batcherRef.current = null;
    };
  }, [inputEnabled, conversationId, sessionId]);

  useEffect(() => {
    if (inputEnabled) surfaceRef.current?.focus();
  }, [inputEnabled]);

  // ---- 输入捕获（仅 pending 登录时挂到交互面）→ 换算帧空间 → 攒批 ----------------
  const pushMouse = (
    type: "down" | "up" | "move" | "wheel",
    e: { clientX: number; clientY: number },
    extra?: {
      button?: number;
      delta_x?: number;
      delta_y?: number;
      click_count?: number;
    },
  ): void => {
    const batcher = batcherRef.current;
    const dim = frameDimRef.current;
    const el = surfaceRef.current;
    if (!batcher || !dim || !el) return;
    const rect = el.getBoundingClientRect();
    const { x, y } = toFrameSpace(
      e.clientX,
      e.clientY,
      rect,
      dim.width,
      dim.height,
    );
    batcher.push({ kind: "mouse", type, x, y, ...extra });
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    e.preventDefault();
    surfaceRef.current?.focus();
    draggingRef.current = true;
    try {
      surfaceRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* capture unsupported — dragging still tracked via ref */
    }
    pushMouse("down", e, { button: e.button, click_count: 1 });
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    // 仅拖拽时发送 move（避免悬停洪泛）；批处理器再就地合并连续 move。
    if (!draggingRef.current) return;
    pushMouse("move", e);
  };

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    draggingRef.current = false;
    try {
      surfaceRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* nothing captured */
    }
    pushMouse("up", e, { button: e.button });
  };

  const onPointerCancel = (): void => {
    draggingRef.current = false;
  };

  const onWheel = (e: ReactWheelEvent<HTMLDivElement>): void => {
    pushMouse("wheel", e, { delta_x: e.deltaX, delta_y: e.deltaY });
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (composingRef.current) return; // IME 组合中 → 交给 compositionend 兜底
    e.preventDefault();
    batcherRef.current?.push({
      kind: "key",
      type: "down",
      key: e.key,
      code: e.code,
      modifiers: modifiersOf(e),
    });
  };

  const onKeyUp = (e: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (composingRef.current) return;
    e.preventDefault();
    batcherRef.current?.push({
      kind: "key",
      type: "up",
      key: e.key,
      code: e.code,
      modifiers: modifiersOf(e),
    });
  };

  const onCompositionStart = (): void => {
    composingRef.current = true;
  };

  const onCompositionEnd = (
    e: React.CompositionEvent<HTMLDivElement>,
  ): void => {
    composingRef.current = false;
    // IME/组合输入兜底：只灌最终合成文本，不逐键上报（不缓存键入内容）。
    if (e.data) batcherRef.current?.push({ kind: "text", text: e.data });
  };

  return (
    <div className="flex h-full flex-col bg-muted/20">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3 text-xs">
        <Radio
          size={13}
          className={`shrink-0 ${isLive ? "text-primary" : "text-muted-foreground/50"}`}
        />
        <span className={isLive ? "text-foreground" : "text-muted-foreground"}>
          {isLive ? "直播中" : "浏览器直播"}
        </span>
      </div>

      {pendingBrowserLogin && (
        <div className="flex shrink-0 items-center gap-1.5 border-b border-primary/20 bg-primary/5 px-3 py-1.5 text-xs text-foreground">
          请在此完成登录，然后回到对话点「已登录，继续」
        </div>
      )}

      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden p-2">
        {showFrame && frameUrl ? (
          inputEnabled ? (
            <div
              ref={surfaceRef}
              // biome-ignore lint/a11y/noNoninteractiveTabindex: a login surface must be focusable to capture keyboard input injected into the sandbox browser; there is no semantic element for "proxy the user's device input to another screen".
              tabIndex={0}
              aria-label="登录中的浏览器画面（点击 / 键入 / 滚动即操作远端）"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerCancel}
              onWheel={onWheel}
              onKeyDown={onKeyDown}
              onKeyUp={onKeyUp}
              onCompositionStart={onCompositionStart}
              onCompositionEnd={onCompositionEnd}
              className="flex h-full w-full cursor-crosshair items-center justify-center outline-none ring-2 ring-primary/40 ring-inset"
            >
              <img
                src={frameUrl}
                alt="浏览器直播画面"
                draggable={false}
                className="pointer-events-none max-h-full max-w-full select-none object-contain"
              />
            </div>
          ) : (
            <>
              <img
                src={frameUrl}
                alt="浏览器直播画面"
                className={`max-h-full max-w-full object-contain ${
                  status === "session_closed" ? "opacity-60" : ""
                }`}
              />
              {status === "session_closed" && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-center gap-1.5 bg-background/80 py-1.5 text-xs text-muted-foreground">
                  <MonitorOff size={13} /> 直播已结束
                </div>
              )}
              {status !== "session_closed" && connection === "reconnecting" && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-center gap-1.5 bg-background/80 py-1.5 text-xs text-warning">
                  <WifiOff size={13} /> 连接已断开，正在重连…
                </div>
              )}
            </>
          )
        ) : (
          <LivePlaceholder connection={connection} status={status} />
        )}
      </div>
    </div>
  );
}

/**
 * 右坞「浏览器」tab 的显隐 + 目标会话（同 `useTerminalRegion` 先例）：
 * - 本会话**曾有** `browser_*` 活动 → 显示（常驻，便于回头看直播）；
 * - 或本会话仍有带 URL / serverSession 的页签（用户页 / 冷恢复）→ 自动带上内容 tab。
 *
 * **不做 auto-surface 面板**：AI 用浏览器是高频常态，自动弹面板是打扰；唯一需要抢注意力的是 pending
 * `browser_login`，那条由登录卡「打开浏览器」CTA 负责（点按才 `showBrowser()`）。
 *
 * 收窄订阅（同 SidePanel 纪律）：execution 选择器内算布尔；pages 只看本对话是否有实质页签。
 */
export function useBrowserRegion(): {
  show: boolean;
  conversationId: string | null;
} {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const showFromActivity = useExecutionStore((s) =>
    conversationHasBrowserActivity(
      runtimeOf(useConversationStore.getState(), conversationId).messages,
      s.byId,
    ),
  );
  const showFromPages = useBrowserSessionsStore((s) => {
    if (!conversationId) return false;
    return s.pages.some(
      (p) =>
        p.conversationId === conversationId &&
        (!!p.url || (p.serverSessionId != null && p.serverSessionId !== "")),
    );
  });
  return { show: showFromActivity || showFromPages, conversationId };
}
