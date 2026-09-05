import { api } from "@/services/api";

/**
 * 沙箱浏览器输入客户端：直播画面上的指针/键盘经坐标换算后攒批 POST `…/browser/input`。
 * 仅 pending `browser_login`（escalate / ask_user）时由 BrowserLivePanel 挂捕获。
 * 走 `services/api` 以复用 401 刷新 / CSRF。
 *
 * 坐标为帧像素空间（`browser_live_frame` 的 width/height）；前端把展示坐标换算到帧空间。
 * 密码等键入不回显不留存（缓冲仅在飞、不落任何持久缓存）。
 */

/**
 * 一条用户输入事件（**坐标 x/y 为帧像素空间**，非展示坐标）。批量灌进 `…/browser/input`
 * 的 `events`，driver 侧转 CDP Input 域命令注入。三类：
 *  - `mouse`：down/up/move/wheel；`button` 数字（0 左 /1 中 /2 右）；wheel 带 `delta_x/delta_y`。
 *  - `key`：down/up；`key` 为解析后的键值（如 "a" / "Enter"），`code` 物理键，`modifiers` 修饰键名。
 *  - `text`：IME/组合输入兜底——直接灌最终合成文本（不逐键上报）。
 */
export type BrowserInputEvent =
  | {
      kind: "mouse";
      type: "down" | "up" | "move" | "wheel";
      x: number;
      y: number;
      button?: number;
      delta_x?: number;
      delta_y?: number;
      click_count?: number;
    }
  | {
      kind: "key";
      type: "down" | "up";
      key: string;
      code?: string;
      modifiers?: string[];
    }
  | { kind: "text"; text: string };

function inputPath(conversationId: string): string {
  return `/v1/conversations/${encodeURIComponent(conversationId)}/browser/input`;
}

/** 批量注入输入事件（坐标须已换算到帧像素空间）。空批不发。 */
export async function sendBrowserInput(
  conversationId: string,
  events: BrowserInputEvent[],
  opts?: { sessionId?: string | null },
): Promise<void> {
  if (events.length === 0) return;
  const sessionId = opts?.sessionId?.trim() || undefined;
  await api.post<{ ok?: boolean }>(inputPath(conversationId), {
    events,
    ...(sessionId ? { session_id: sessionId } : {}),
  });
}

/** 展示坐标换算所需的最小矩形（`getBoundingClientRect` 的子集）。 */
export interface DisplayRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * 把展示坐标（相对视口的 clientX/clientY）换算到**帧像素空间**。
 *
 * 直播画面按 `object-contain` 等比缩放居中于容器（缩放 + 留白/信箱边），故须：
 * 取 `scale = min(容器宽/帧宽, 容器高/帧高)`、扣掉两侧留白、再除以 scale 还原到帧像素；
 * 最后钳到 [0,帧宽]×[0,帧高] 并取整。帧尺寸非法（≤0）时回落 (0,0)。
 */
export function toFrameSpace(
  clientX: number,
  clientY: number,
  rect: DisplayRect,
  frameWidth: number,
  frameHeight: number,
): { x: number; y: number } {
  if (frameWidth <= 0 || frameHeight <= 0) return { x: 0, y: 0 };
  const scale = Math.min(rect.width / frameWidth, rect.height / frameHeight);
  if (!(scale > 0)) return { x: 0, y: 0 };
  const renderedW = frameWidth * scale;
  const renderedH = frameHeight * scale;
  const padX = (rect.width - renderedW) / 2;
  const padY = (rect.height - renderedH) / 2;
  const fx = (clientX - rect.left - padX) / scale;
  const fy = (clientY - rect.top - padY) / scale;
  const clamp = (v: number, max: number) => Math.max(0, Math.min(max, v));
  return {
    x: Math.round(clamp(fx, frameWidth)),
    y: Math.round(clamp(fy, frameHeight)),
  };
}

/** 从 DOM 键盘事件提取修饰键名数组（空则省略，不发 `modifiers:[]`）。 */
export function modifiersOf(e: {
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): string[] | undefined {
  const mods: string[] = [];
  if (e.altKey) mods.push("alt");
  if (e.ctrlKey) mods.push("ctrl");
  if (e.metaKey) mods.push("meta");
  if (e.shiftKey) mods.push("shift");
  return mods.length > 0 ? mods : undefined;
}

/**
 * 输入批处理器：把高频 DOM 输入攒批后定时 flush（`…/browser/input`），避免事件洪泛。
 * 连续的鼠标 move 就地合并（只留最新一条），故拖拽/悬停不会灌爆。commit 类事件（up/wheel/
 * text）立即 flush 以求手感。`stop()` 收口：flush 残留 + 停表。发送失败即丢弃该批（不重放陈旧
 * 输入）；缓冲仅在飞、绝不落任何持久缓存。
 */
export interface InputBatcher {
  push: (event: BrowserInputEvent) => void;
  flush: () => void;
  stop: () => void;
}

const DEFAULT_FLUSH_MS = 60;

export function createInputBatcher(
  send: (events: BrowserInputEvent[]) => Promise<void>,
  flushMs: number = DEFAULT_FLUSH_MS,
): InputBatcher {
  let buffer: BrowserInputEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  function flush(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (buffer.length === 0) return;
    const batch = buffer;
    buffer = [];
    // Best-effort: a dropped batch = a missed keystroke; replaying stale input
    // would be worse. Never surface / log the content.
    void send(batch).catch(() => {});
  }

  function schedule(): void {
    if (timer !== null || stopped) return;
    timer = setTimeout(() => {
      timer = null;
      flush();
    }, flushMs);
  }

  function isCommit(event: BrowserInputEvent): boolean {
    if (event.kind === "text") return true;
    if (event.kind === "key") return event.type === "up";
    return event.type === "up" || event.type === "wheel";
  }

  return {
    push(event) {
      if (stopped) return;
      const last = buffer[buffer.length - 1];
      if (
        event.kind === "mouse" &&
        event.type === "move" &&
        last?.kind === "mouse" &&
        last.type === "move"
      ) {
        buffer[buffer.length - 1] = event; // coalesce consecutive moves
      } else {
        buffer.push(event);
      }
      if (isCommit(event)) flush();
      else schedule();
    },
    flush,
    stop() {
      stopped = true;
      flush();
    },
  };
}
