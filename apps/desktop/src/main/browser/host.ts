/**
 * LocalChromiumHost —— 主窗口内嵌多页 WebContentsView。
 *
 * 安全不变量（L1b · 对话硬隔离）：
 * - 外网页：**非持久** {@link browserPartitionFor}；工作区 HTML：**非持久**
 *   {@link workspacePartitionFor}——二者按 conversationId 切开，≠ defaultSession；
 * - sandbox:true、**无 preload**、nodeIntegration 关、contextIsolation 开、webviewTag 关；
 * - 导航策略见 navigation.ts（按 web | workspace 模式）；
 * - web `window.open`：popup → 同 partition 子窗；target=_blank → 同壳新页签（见 openTab IPC）。
 *
 * 多页：一 client pageId 一 view；仅激活页 show+bounds，其余 hide；关页销毁 view。
 * 同 pageId 在 http(s) ↔ workspace 间切换时销毁重建以换 partition。
 * Bridge（sidecar）经 {@link bridgeDispatchLocalBrowser} 驱动同一套页（pageId = session_id）。
 */

import {
  BROWSER_CHANNELS,
  type BrowserBounds,
  type BrowserNavState,
  type BrowserResult,
} from "@shared/browser-contract";
import {
  BrowserWindow,
  type WebContents,
  WebContentsView,
  session,
} from "electron";
import type { BridgeAction, BridgeHostResult } from "./bridge-handler";
import { ConsoleRingBuffer } from "./console-buffer";
import {
  LOCAL_BROWSER_BLANK,
  type LocalBrowserNavMode,
  type LocalBrowserWebOpenHooks,
  attachLocalBrowserDownloadGuard,
  isNavigableLocalBrowserUrl,
  lockLocalBrowserNavigation,
} from "./navigation";
import {
  browserPartitionFor,
  normalizeBrowserBounds,
  normalizeBrowserConversationId,
} from "./paths";
import {
  isWorkspaceBrowserUrl,
  resolveWorkspaceHtmlUrl,
  workspacePartitionFor,
} from "./workspace-paths";
import { registerWorkspaceProtocolFor } from "./workspace-protocol";

interface PageView {
  pageId: string;
  conversationId: string;
  view: WebContentsView;
  snapshotVersion: number;
  kind: LocalBrowserNavMode;
  /** Ring buffer: page console-message + main-frame did-fail-load. */
  consoleBuf: ConsoleRingBuffer;
}

/** pageId → 视图。 */
const pages = new Map<string, PageView>();
/** 测试/断言用：与 setVisible 同步的可见性镜像。 */
const pageVisible = new Map<string, boolean>();
/** conversationId → OAuth/登录 popup 子窗（同 partition；关对话时一并关）。 */
const popupsByConversation = new Map<string, Set<BrowserWindow>>();

let hostWin: BrowserWindow | null = null;
/**
 * 一等 Attachment：当前附着（可见）页；`null` = 已脱离。
 * hide/detach 必须清此字段，避免 ensurePageKind 重建时误点亮残影。
 */
let activePageId: string | null = null;
/**
 * 每次 detach 递增；show 入口捕获，落点前若已变则拒（过期 show）。
 * 与 IPC show/hide 串行队列一起保证顺序。
 */
let attachmentGeneration = 0;
/** 升级后清旧全局 partition 活页：首次 browser 路径跑一次。 */
let legacyPagesCleared = false;
/** 测试接缝：show 落点 generation 检查前钩子（模拟 hide 竞态）。 */
let beforeAttachCheckForTests: (() => void) | null = null;

/**
 * 与 sandbox driver 对齐的交互元素快照（data-acref）。
 * 表单控件：placeholder / value 分列；禁用显式标注；尾部附可见非交互文本摘要。
 * 导出供单测直接 eval（jsdom），勿在生产路径外改语义。
 */
export const SNAPSHOT_JS = `(version) => {
  const NAME_MAX = 100;
  const VALUE_MAX = 200;
  const PLACEHOLDER_MAX = 100;
  const TEXT_SUMMARY_MAX = 1200;
  const MAX_ELEMENTS = 200;
  const sel = [
    'a', 'button', 'input', 'textarea', 'select',
    '[contenteditable=""], [contenteditable="true"]',
    '[role=button]', '[role=link]', '[role=textbox]', '[role=checkbox]',
    '[role=tab]', '[role=menuitem]', '[onclick]'
  ].join(',');
  const clip = (s, n) => s.trim().replace(/\\s+/g, ' ').slice(0, n);
  const out = [];
  const interactive = new Set();
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    n++;
    const ref = 'e' + n;
    el.setAttribute('data-acref', ref);
    interactive.add(el);
    const type = (el.getAttribute('type') || '').toLowerCase();
    const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    const isPassword = type === 'password' || ac.includes('password');
    const tag = el.tagName.toLowerCase();
    const role = isPassword
      ? 'password'
      : (el.getAttribute('role') || (el.isContentEditable ? 'textbox' : tag));
    const disabled = !!(el.disabled
      || el.getAttribute('aria-disabled') === 'true'
      || el.hasAttribute('disabled'));
    const isFormControl = tag === 'input' || tag === 'textarea' || tag === 'select'
      || el.isContentEditable || role === 'textbox';
    // 表单控件：name 不含 placeholder/value（二者分列）；password 永不泄露 value。
    let nameSrc = '';
    if (isPassword) {
      nameSrc = el.getAttribute('aria-label') || '';
    } else if (isFormControl) {
      nameSrc = el.getAttribute('aria-label') || el.getAttribute('name') || '';
    } else {
      nameSrc = el.getAttribute('aria-label') || el.textContent || '';
    }
    const name = clip(String(nameSrc || ''), NAME_MAX);
    let line = '[' + ref + '] ' + role + (disabled ? ' disabled' : '')
      + (name ? ': ' + name : '');
    if (isFormControl) {
      const ph = clip(el.getAttribute('placeholder') || '', PLACEHOLDER_MAX);
      if (ph) line += ' | placeholder=' + JSON.stringify(ph);
      if (isPassword) {
        // 仅长度/掩码，绝不回明文
        const len = typeof el.value === 'string' ? el.value.length : 0;
        line += ' | value=' + (len > 0 ? '"***"' : '""') + ' (chars=' + len + ')';
      } else {
        let raw = '';
        if (el.isContentEditable) raw = el.innerText || el.textContent || '';
        else if ('value' in el) raw = String(el.value ?? '');
        const full = String(raw);
        const shown = clip(full, VALUE_MAX);
        const truncated = full.trim().replace(/\\s+/g, ' ').length > VALUE_MAX;
        line += ' | value=' + JSON.stringify(shown)
          + (truncated ? '…' : '');
      }
    }
    out.push(line);
    if (n >= MAX_ELEMENTS) break;
  }
  // 可见非交互文本摘要（聊天气泡等）；尾部优先、硬上限。
  const root = document.querySelector('main, [role=main], #root, body') || document.body;
  const chunks = [];
  if (root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!parent) continue;
      if (interactive.has(parent) || parent.closest('[data-acref]')) continue;
      const tag = parent.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TEXTAREA') continue;
      const st = window.getComputedStyle(parent);
      if (st.visibility === 'hidden' || st.display === 'none') continue;
      const t = String(node.textContent || '').replace(/\\s+/g, ' ').trim();
      if (!t) continue;
      chunks.push(t);
    }
  }
  const joined = chunks.join(' ').trim();
  if (joined) {
    const tail = joined.length > TEXT_SUMMARY_MAX
      ? joined.slice(joined.length - TEXT_SUMMARY_MAX)
      : joined;
    out.push('---');
    out.push('visible_text: ' + (joined.length > TEXT_SUMMARY_MAX ? '…' : '') + tail);
  }
  return out.join('\\n');
}`;

const IS_PASSWORD_JS = `(el) => {
  const type = (el.getAttribute('type') || '').toLowerCase();
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  return type === 'password' || ac.includes('password');
}`;

/** Focus + select-all，供 CDP Input.insertText 替换既有内容。 */
const FOCUS_SELECT_JS = `(ref) => {
  const el = document.querySelector('[data-acref="' + ref + '"]');
  if (!el) throw new Error('ref_not_found');
  el.focus();
  if (typeof el.select === 'function') {
    try { el.select(); } catch (_) { /* type=number 等 */ }
  } else if (el.isContentEditable
      || el.getAttribute('contenteditable') === 'true'
      || el.getAttribute('contenteditable') === '') {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    if (sel) { sel.removeAllRanges(); sel.addRange(range); }
  } else if (typeof el.setSelectionRange === 'function') {
    try {
      const len = String(el.value ?? '').length;
      el.setSelectionRange(0, len);
    } catch (_) { /* 不可选 */ }
  }
  return true;
}`;

/** 回读元素实际内容；password 只给长度，绝不明文。 */
const READ_TYPED_JS = `(ref) => {
  const el = document.querySelector('[data-acref="' + ref + '"]');
  if (!el) throw new Error('ref_not_found');
  const type = (el.getAttribute('type') || '').toLowerCase();
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  const isPassword = type === 'password' || ac.includes('password');
  let raw = '';
  if (el.isContentEditable
      || el.getAttribute('contenteditable') === 'true'
      || el.getAttribute('contenteditable') === '') {
    raw = el.innerText || el.textContent || '';
  } else if ('value' in el) {
    raw = String(el.value ?? '');
  } else {
    raw = String(el.textContent ?? '');
  }
  if (isPassword) {
    return { chars: raw.length, masked: true, text: null };
  }
  return { chars: Array.from(raw).length, masked: false, text: raw };
}`;

/** Click 前探针：disabled / role / name（事实回执，不改 click 行为）。 */
const CLICK_PROBE_JS = `(ref) => {
  const el = document.querySelector('[data-acref="' + ref + '"]');
  if (!el) throw new Error('ref_not_found');
  const type = (el.getAttribute('type') || '').toLowerCase();
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  const isPassword = type === 'password' || ac.includes('password');
  const role = isPassword
    ? 'password'
    : (el.getAttribute('role') || (el.isContentEditable ? 'textbox' : el.tagName.toLowerCase()));
  const was_disabled = !!(el.disabled
    || el.getAttribute('aria-disabled') === 'true'
    || el.hasAttribute('disabled'));
  const nameSrc = el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || '';
  const name = String(nameSrc || '').trim().replace(/\\s+/g, ' ').slice(0, 100);
  el.click();
  return { was_disabled: was_disabled, role: role, name: name };
}`;

/** 每 WebContents 串行化 CDP attach，避免并发 type 互 detach。 */
const cdpChains = new WeakMap<object, Promise<unknown>>();

function enqueueCdp<T>(wc: WebContents, task: () => Promise<T>): Promise<T> {
  const prev = cdpChains.get(wc) ?? Promise.resolve();
  const run = prev.then(task, task);
  // 吞掉后续链错误，避免永久卡死
  cdpChains.set(
    wc,
    run.then(
      () => undefined,
      () => undefined,
    ),
  );
  return run;
}

/**
 * 真实输入：CDP Input.insertText（CJK / emoji / 长文本 / contenteditable 均成立）。
 * Electron 42+ debugger.sendCommand 返回 Promise；attach 仅本调用持有时 detach。
 */
async function typeViaCdpInsertText(
  wc: WebContents,
  text: string,
): Promise<"cdp_insertText"> {
  return enqueueCdp(wc, async () => {
    const dbg = wc.debugger;
    let attachedHere = false;
    if (!dbg.isAttached()) {
      dbg.attach("1.3");
      attachedHere = true;
    }
    try {
      // 替换语义：先清选区（Backspace 删选中），再插入。
      await dbg.sendCommand("Input.dispatchKeyEvent", {
        type: "keyDown",
        key: "Backspace",
        code: "Backspace",
        windowsVirtualKeyCode: 8,
        nativeVirtualKeyCode: 8,
      });
      await dbg.sendCommand("Input.dispatchKeyEvent", {
        type: "keyUp",
        key: "Backspace",
        code: "Backspace",
        windowsVirtualKeyCode: 8,
        nativeVirtualKeyCode: 8,
      });
      if (text.length > 0) {
        await dbg.sendCommand("Input.insertText", { text });
      }
      return "cdp_insertText" as const;
    } finally {
      if (attachedHere) {
        try {
          if (dbg.isAttached()) dbg.detach();
        } catch {
          /* 已脱离 / DevTools 接管 */
        }
      }
    }
  });
}

const hostsWithCleanup = new WeakSet<BrowserWindow>();

function readNavSnapshot(pageId: string): {
  url: string;
  title: string;
  canGoBack: boolean;
  canGoForward: boolean;
} | null {
  const entry = pages.get(pageId);
  if (!entry || entry.view.webContents.isDestroyed()) return null;
  const wc = entry.view.webContents;
  const nav = wc.navigationHistory;
  return {
    url: wc.getURL(),
    title: wc.getTitle() || "",
    canGoBack: nav.canGoBack(),
    canGoForward: nav.canGoForward(),
  };
}

function pushNavState(pageId: string): void {
  const entry = pages.get(pageId);
  if (!entry || !hostWin || hostWin.isDestroyed()) return;
  if (entry.view.webContents.isDestroyed()) return;
  const snap = readNavSnapshot(pageId);
  if (!snap) return;
  const payload: BrowserNavState = {
    pageId,
    url: snap.url,
    title: snap.title,
    canGoBack: snap.canGoBack,
    canGoForward: snap.canGoForward,
  };
  hostWin.webContents.send(BROWSER_CHANNELS.navState, payload);
}

function ensureHostCleanup(win: BrowserWindow): void {
  if (hostsWithCleanup.has(win)) return;
  hostsWithCleanup.add(win);
  win.once("closed", () => {
    if (hostWin === win) closeAllLocalBrowserPages();
  });
}

function clearLegacyPagesOnce(): void {
  if (legacyPagesCleared) return;
  legacyPagesCleared = true;
  closeAllLocalBrowserPages();
}

function partitionFor(
  kind: LocalBrowserNavMode,
  conversationId: string,
): string {
  return kind === "workspace"
    ? workspacePartitionFor(conversationId)
    : browserPartitionFor(conversationId);
}

function trackPopupWindow(conversationId: string, win: BrowserWindow): void {
  let set = popupsByConversation.get(conversationId);
  if (!set) {
    set = new Set();
    popupsByConversation.set(conversationId, set);
  }
  set.add(win);
  win.once("closed", () => {
    const cur = popupsByConversation.get(conversationId);
    if (!cur) return;
    cur.delete(win);
    if (cur.size === 0) popupsByConversation.delete(conversationId);
  });
}

function closePopupsForConversation(conversationId: string): void {
  const set = popupsByConversation.get(conversationId);
  if (!set) return;
  popupsByConversation.delete(conversationId);
  for (const win of [...set]) {
    try {
      if (!win.isDestroyed()) win.close();
    } catch {
      /* 已销毁 */
    }
  }
}

function closeAllPopupWindows(): void {
  const cids = [...popupsByConversation.keys()];
  for (const cid of cids) closePopupsForConversation(cid);
}

/** 通知 renderer：target=_blank → 同壳新页签。 */
function emitShellTabRequest(
  conversationId: string,
  url: string,
  background: boolean,
): void {
  if (!hostWin || hostWin.isDestroyed()) return;
  hostWin.webContents.send(BROWSER_CHANNELS.openTab, {
    conversationId,
    url,
    background,
  });
}

function webOpenHooksFor(
  conversationId: string,
  partition: string,
): LocalBrowserWebOpenHooks {
  return {
    partition,
    getParentWindow: () => (hostWin && !hostWin.isDestroyed() ? hostWin : null),
    requestShellTab: (url, background) =>
      emitShellTabRequest(conversationId, url, background),
    trackPopup: (popupWin) => trackPopupWindow(conversationId, popupWin),
  };
}

function createPageView(
  win: BrowserWindow,
  pageId: string,
  kind: LocalBrowserNavMode,
  conversationId: string,
): WebContentsView {
  if (kind === "workspace") registerWorkspaceProtocolFor(conversationId);
  const partition = partitionFor(kind, conversationId);
  const sess = session.fromPartition(partition);
  attachLocalBrowserDownloadGuard(sess);

  const view = new WebContentsView({
    webPreferences: {
      partition,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      // 刻意不挂 preload —— 浏览页不得拿应用 IPC。
    },
  });
  lockLocalBrowserNavigation(
    view.webContents,
    kind,
    kind === "web" ? webOpenHooksFor(conversationId, partition) : undefined,
  );
  view.webContents.on("did-navigate", () => pushNavState(pageId));
  view.webContents.on("did-navigate-in-page", () => pushNavState(pageId));
  view.webContents.on("page-title-updated", () => pushNavState(pageId));
  setPageVisible(pageId, view, false);
  win.contentView.addChildView(view);
  void view.webContents.loadURL(LOCAL_BROWSER_BLANK);
  return view;
}

/** Attach console-message + did-fail-load → ring buffer (no debugger). */
function attachConsoleCapture(entry: PageView): void {
  const wc = entry.view.webContents;
  const buf = entry.consoleBuf;
  wc.on("console-message", (...args: unknown[]) => {
    const first = args[0] as
      | { message?: string; level?: string | number }
      | undefined;
    if (
      first &&
      typeof first === "object" &&
      typeof first.message === "string"
    ) {
      buf.pushMessage(first.level, first.message);
      return;
    }
    // Legacy positional: (event, level, message, line, sourceId)
    const level = args[1];
    const message = args[2];
    if (typeof message === "string") {
      buf.pushMessage(level, message);
    }
  });
  wc.on(
    "did-fail-load",
    (
      _event: unknown,
      errorCode: number,
      errorDescription: string,
      validatedURL: string,
      isMainFrame: boolean,
    ) => {
      if (!isMainFrame) return;
      // ERR_ABORTED (-3) is common on redirects / superseding navigations.
      if (errorCode === -3) return;
      buf.pushError(
        `did-fail-load: ${errorDescription || errorCode}`,
        validatedURL ? `url=${validatedURL}` : undefined,
      );
    },
  );
}

function setPageVisible(
  pageId: string,
  view: WebContentsView,
  visible: boolean,
): void {
  view.setVisible(visible);
  pageVisible.set(pageId, visible);
}

/**
 * 确保 pageId 视图存在且为指定 kind + conversationId；变更则销毁重建（换 partition）。
 */
function ensurePageKind(
  win: BrowserWindow,
  pageId: string,
  kind: LocalBrowserNavMode,
  conversationId: string,
): PageView {
  const existing = pages.get(pageId);
  if (
    existing &&
    !existing.view.webContents.isDestroyed() &&
    existing.kind === kind &&
    existing.conversationId === conversationId
  ) {
    return existing;
  }
  // 仅当**当前仍附着**该页时重建后恢复可见。
  // 不是「hide 前曾是 active」——hide 已清 activePageId，不得因 wasActive 复活残影。
  const wasAttached = activePageId === pageId;
  const prevBounds =
    existing && !existing.view.webContents.isDestroyed()
      ? existing.view.getBounds()
      : null;
  if (existing) destroyPageView(pageId);

  hostWin = win;
  ensureHostCleanup(win);
  const view = createPageView(win, pageId, kind, conversationId);
  if (prevBounds) view.setBounds(prevBounds);
  if (wasAttached) {
    activePageId = pageId;
    setPageVisible(pageId, view, true);
  }
  const entry: PageView = {
    pageId,
    conversationId,
    view,
    snapshotVersion: 0,
    kind,
    consoleBuf: new ConsoleRingBuffer(),
  };
  pages.set(pageId, entry);
  attachConsoleCapture(entry);
  return entry;
}

function hideAllViews(): void {
  for (const [pageId, { view }] of pages) {
    if (!view.webContents.isDestroyed()) setPageVisible(pageId, view, false);
  }
}

function destroyPageView(pageId: string): void {
  const entry = pages.get(pageId);
  if (!entry) return;
  pages.delete(pageId);
  pageVisible.delete(pageId);
  if (activePageId === pageId) activePageId = null;
  const { view } = entry;
  try {
    if (hostWin && !hostWin.isDestroyed()) {
      hostWin.contentView.removeChildView(view);
    }
  } catch {
    /* 已摘除 */
  }
  try {
    if (!view.webContents.isDestroyed()) view.webContents.close();
  } catch {
    /* 已销毁 */
  }
}

/** 销毁全部本机页（宿主窗口关闭 / 升级清场）。 */
export function closeAllLocalBrowserPages(): void {
  closeAllPopupWindows();
  const ids = [...pages.keys()];
  for (const id of ids) destroyPageView(id);
  hostWin = null;
  activePageId = null;
  attachmentGeneration += 1;
}

/**
 * 销毁某对话全部本机页（含仅本地空白页）。幂等；与 server registry.close 双关。
 */
export function closeConversationBrowserPages(conversationId: string): void {
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return;
  closePopupsForConversation(cid);
  const ids = [...pages.entries()]
    .filter(([, e]) => e.conversationId === cid)
    .map(([id]) => id);
  for (const id of ids) destroyPageView(id);
}

/** 测试接缝：某对话存活 popup 子窗数量。 */
export function localBrowserPopupCountForTests(conversationId: string): number {
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return 0;
  const set = popupsByConversation.get(cid);
  if (!set) return 0;
  let n = 0;
  for (const win of set) {
    if (!win.isDestroyed()) n += 1;
  }
  return n;
}

/** 测试接缝：当前存活页的 conversationId 集合。 */
export function listLocalBrowserConversationIdsForTests(): string[] {
  return [
    ...new Set(
      [...pages.values()]
        .filter((e) => !e.view.webContents.isDestroyed())
        .map((e) => e.conversationId),
    ),
  ];
}

/** 测试接缝：某页当前外网/工作区 partition 名（无页 → null）。 */
export function localBrowserPartitionForTests(pageId: string): string | null {
  const entry = pages.get(pageId);
  if (!entry || entry.view.webContents.isDestroyed()) return null;
  return partitionFor(entry.kind, entry.conversationId);
}

/**
 * 显示（并必要时创建）某 pageId 视图，设为激活并定位 bounds；其余页 hide。
 * 缺 conversationId → fail-closed，不建全局 partition 页。
 * 过期 show：入口捕获 attachmentGeneration，落点前若 hide 已 bump 则拒。
 */
export function showLocalBrowserPage(
  win: BrowserWindow,
  pageId: string,
  boundsIn: BrowserBounds,
  conversationId: string,
): BrowserResult {
  try {
    clearLegacyPagesOnce();
    const bounds = normalizeBrowserBounds(boundsIn);
    if (!bounds) return { ok: false, reason: "无效的预览区域" };
    if (!pageId.trim()) return { ok: false, reason: "无效的页 id" };
    const cid = normalizeBrowserConversationId(conversationId);
    if (!cid) return { ok: false, reason: "缺少 conversationId" };

    if (hostWin && hostWin !== win) closeAllLocalBrowserPages();
    // 换窗清场后锚定；此后 hide/detach 再 bump 则本 show 过期。
    const gen = attachmentGeneration;
    hostWin = win;
    ensureHostCleanup(win);

    // show 不强制换 kind：已有同 cid 页保留（workspace 页再次 show 不掉回 web）。
    let entry = pages.get(pageId);
    if (
      !entry ||
      entry.view.webContents.isDestroyed() ||
      entry.conversationId !== cid
    ) {
      entry = ensurePageKind(win, pageId, entry?.kind ?? "web", cid);
    }

    beforeAttachCheckForTests?.();
    if (attachmentGeneration !== gen) {
      return { ok: false, reason: "attachment_stale" };
    }

    hideAllViews();
    activePageId = pageId;
    entry.view.setBounds(bounds);
    setPageVisible(pageId, entry.view, true);
    pushNavState(pageId);
    const snap = readNavSnapshot(pageId);
    return {
      ok: true,
      url: snap?.url ?? LOCAL_BROWSER_BLANK,
      title: snap?.title ?? "",
      canGoBack: snap?.canGoBack ?? false,
      canGoForward: snap?.canGoForward ?? false,
    };
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "打开本机浏览器失败",
    };
  }
}

/** 同步激活页 bounds（高频）；无激活页则忽略。 */
export function setLocalBrowserBounds(boundsIn: BrowserBounds): void {
  const bounds = normalizeBrowserBounds(boundsIn);
  if (!bounds || !activePageId) return;
  const entry = pages.get(activePageId);
  if (!entry || entry.view.webContents.isDestroyed()) return;
  entry.view.setBounds(bounds);
}

/**
 * 脱离附着（保活）：清 activePageId、全部不可见、bump generation。
 * 关坞 / 关浏览器 tab / 切对话；过期 in-flight show 据此拒。
 */
function detachAttachment(): void {
  hideAllViews();
  activePageId = null;
  attachmentGeneration += 1;
}

/** 隐藏全部本机视图但保活（detach Attachment）。 */
export function hideLocalBrowserPages(): void {
  detachAttachment();
}

/** 测试接缝：当前附着页 id（null = 已脱离）。 */
export function localBrowserActivePageIdForTests(): string | null {
  return activePageId;
}

/** 测试接缝：attachmentGeneration。 */
export function localBrowserAttachmentGenerationForTests(): number {
  return attachmentGeneration;
}

/** 测试接缝：页是否可见（无页 → null）。 */
export function localBrowserPageVisibleForTests(
  pageId: string,
): boolean | null {
  if (!pages.has(pageId)) return null;
  return pageVisible.get(pageId) ?? false;
}

/** 测试接缝：模拟 generation 漂移（过期 show）。 */
export function advanceAttachmentGenerationForTests(): void {
  attachmentGeneration += 1;
}

/** 测试接缝：show 附着前钩子（测过期 show；测完须清 null）。 */
export function setBeforeAttachCheckForTests(fn: (() => void) | null): void {
  beforeAttachCheckForTests = fn;
}

/** 导航某页到 http(s) 或 workspace://（可先于 show；无宿主窗口则拒）。 */
export function navigateLocalBrowserPage(
  pageId: string,
  url: string,
  conversationId: string,
): BrowserResult {
  clearLegacyPagesOnce();
  const cid = normalizeBrowserConversationId(conversationId);
  if (!cid) return { ok: false, reason: "缺少 conversationId" };
  const trimmed = url.trim();
  if (!isNavigableLocalBrowserUrl(trimmed)) {
    return { ok: false, reason: "仅支持 http(s) 或工作区地址" };
  }
  const win = resolveBridgeWindow();
  if (!win) return { ok: false, reason: "页尚未打开" };

  const kind: LocalBrowserNavMode = isWorkspaceBrowserUrl(trimmed)
    ? "workspace"
    : "web";
  const entry = ensurePageKind(win, pageId, kind, cid);
  void entry.view.webContents.loadURL(trimmed);
  pushNavState(pageId);
  return { ok: true };
}

/**
 * 在指定 pageId 加载工作区 HTML（L1b：workspace partition + workspace:// desk host）。
 * 可先于 UI show；无主窗口 → 失败。
 * `workspaceId` 缺省回退 `conv:{conversationId}`。
 */
export function openLocalBrowserWorkspaceHtml(
  pageId: string,
  conversationId: string,
  path: string,
  workspaceId?: string,
): BrowserResult {
  try {
    clearLegacyPagesOnce();
    const id = pageId.trim();
    const conv = normalizeBrowserConversationId(conversationId);
    if (!id) return { ok: false, reason: "无效的页 id" };
    if (!conv) return { ok: false, reason: "无效的会话 id" };
    const target = resolveWorkspaceHtmlUrl(conv, path, workspaceId);
    if (!target) return { ok: false, reason: "无效的工作区路径或 desk" };

    const win = resolveBridgeWindow();
    if (!win) {
      return { ok: false, reason: "无宿主窗口" };
    }

    registerWorkspaceProtocolFor(conv);
    const entry = ensurePageKind(win, id, "workspace", conv);
    void entry.view.webContents.loadURL(target);
    pushNavState(id);
    return { ok: true };
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "打开工作区预览失败",
    };
  }
}

export function reloadLocalBrowserPage(pageId: string): void {
  const entry = pages.get(pageId);
  if (!entry || entry.view.webContents.isDestroyed()) return;
  entry.view.webContents.reload();
}

export function goBackLocalBrowserPage(pageId: string): void {
  const entry = pages.get(pageId);
  if (!entry || entry.view.webContents.isDestroyed()) return;
  const nav = entry.view.webContents.navigationHistory;
  if (nav.canGoBack()) nav.goBack();
}

export function goForwardLocalBrowserPage(pageId: string): void {
  const entry = pages.get(pageId);
  if (!entry || entry.view.webContents.isDestroyed()) return;
  const nav = entry.view.webContents.navigationHistory;
  if (nav.canGoForward()) nav.goForward();
}

/** 关页：销毁对应 view。 */
export function closeLocalBrowserPage(pageId: string): void {
  destroyPageView(pageId);
}

function resolveBridgeWindow(): BrowserWindow | null {
  if (hostWin && !hostWin.isDestroyed()) return hostWin;
  const focused = BrowserWindow.getFocusedWindow();
  if (focused && !focused.isDestroyed()) return focused;
  const all = BrowserWindow.getAllWindows().filter((w) => !w.isDestroyed());
  return all[0] ?? null;
}

/**
 * Bridge：确保 pageId 对应视图存在（可先于 UI show；无主窗口 → host_unavailable）。
 * conversationId 必填（caller 已 fail-closed）。
 */
function ensurePageForBridge(
  pageId: string,
  conversationId: string,
): BridgeHostResult | PageView {
  clearLegacyPagesOnce();
  const id = pageId.trim();
  const cid = normalizeBrowserConversationId(conversationId);
  if (!id)
    return { ok: false, error: "missing_pageId", code: "host_unavailable" };
  if (!cid) {
    return {
      ok: false,
      error: "missing_conversationId",
      code: "missing_conversationId",
    };
  }

  const existing = pages.get(id);
  if (
    existing &&
    !existing.view.webContents.isDestroyed() &&
    existing.conversationId === cid
  ) {
    return existing;
  }
  if (existing) destroyPageView(id);

  const win = resolveBridgeWindow();
  if (!win) {
    return {
      ok: false,
      error: "host_unavailable: 无可用 Desktop 窗口承载本机浏览器",
      code: "host_unavailable",
    };
  }
  if (hostWin && hostWin !== win) closeAllLocalBrowserPages();
  hostWin = win;
  ensureHostCleanup(win);
  const view = createPageView(win, id, "web", cid);
  // 隐藏占位：Agent 驱动时可先不 show；用户打开右坞后再 setBounds。
  view.setBounds({ x: 0, y: 0, width: 1, height: 1 });
  setPageVisible(id, view, false);
  const entry: PageView = {
    pageId: id,
    conversationId: cid,
    view,
    snapshotVersion: 0,
    kind: "web",
    consoleBuf: new ConsoleRingBuffer(),
  };
  pages.set(id, entry);
  attachConsoleCapture(entry);
  return entry;
}

async function pageMeta(
  entry: PageView,
): Promise<{ final_url: string; title: string }> {
  const wc = entry.view.webContents;
  return {
    final_url: wc.getURL(),
    title: wc.getTitle(),
  };
}

/** capturePage → jpeg base64 + device pixel size（live / keyframe 共用）. */
async function captureJpegFrame(
  entry: PageView,
  quality = 70,
): Promise<{ frame_b64: string; width: number; height: number } | undefined> {
  try {
    const img = await entry.view.webContents.capturePage();
    const size = img.getSize();
    const q = Math.min(100, Math.max(1, Math.round(quality)));
    const jpeg = img.toJPEG(q);
    return {
      frame_b64: Buffer.from(jpeg).toString("base64"),
      width: size.width,
      height: size.height,
    };
  } catch {
    return undefined;
  }
}

function jpegQualityFromArgs(
  args: Record<string, unknown>,
  fallback = 70,
): number {
  const raw = args.quality;
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string" && raw.trim()) {
    const n = Number(raw);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

async function bumpSnapshotAndState(
  entry: PageView,
  opts: { capture: boolean; quality?: number },
): Promise<Record<string, unknown>> {
  const quality = opts.quality ?? 70;
  entry.snapshotVersion += 1;
  const meta = await pageMeta(entry);
  // Align with sandbox `_page_state`: mutation bumps version, re-tags refs via
  // SNAPSHOT_JS, and returns elements — new refs are already in the reply, so
  // the model need not force another browser_snapshot.
  const elementsRaw = await entry.view.webContents.executeJavaScript(
    `(${SNAPSHOT_JS})(${entry.snapshotVersion})`,
  );
  const state: Record<string, unknown> = {
    ...meta,
    snapshot_version: entry.snapshotVersion,
    elements: typeof elementsRaw === "string" ? elementsRaw : "",
    aria: "",
  };
  if (opts.capture) {
    const frame = await captureJpegFrame(entry, quality);
    if (frame) {
      state.frame_b64 = frame.frame_b64;
      state.width = frame.width;
      state.height = frame.height;
    }
  }
  return state;
}

async function waitLoad(wc: WebContents, timeoutMs: number): Promise<void> {
  if (wc.isLoadingMainFrame()) {
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        wc.removeListener("did-finish-load", onLoad);
        wc.removeListener("did-fail-load", onFail);
        resolve();
      }, timeoutMs);
      const done = () => {
        clearTimeout(t);
        resolve();
      };
      const onLoad = () => done();
      const onFail = () => done();
      wc.once("did-finish-load", onLoad);
      wc.once("did-fail-load", onFail);
    });
  }
}

/**
 * Bridge 派发：与 sandbox browser driver 动作语义对齐（含 console 只读证据）。
 * pageId = Registry session_id；conversationId 强制。
 */
export async function bridgeDispatchLocalBrowser(
  pageId: string,
  action: BridgeAction,
  args: Record<string, unknown>,
  conversationId: string,
): Promise<BridgeHostResult> {
  const ensured = ensurePageForBridge(pageId, conversationId);
  if ("ok" in ensured && ensured.ok === false) return ensured;
  const entry = ensured as PageView;
  const wc = entry.view.webContents;

  try {
    switch (action) {
      case "navigate": {
        const target = String(args.url ?? "").trim();
        if (!target || !isNavigableLocalBrowserUrl(target)) {
          return {
            ok: false,
            error: "仅支持 http(s) 或本会话工作区地址（workspace://）",
          };
        }
        // 甲：workspace:// 必须切 workspace partition（与 openLocalBrowserWorkspaceHtml
        // 同形）；禁止在 web 页上硬 load workspace://。
        const win = resolveBridgeWindow();
        if (!win) {
          return {
            ok: false,
            error: "host_unavailable: 无可用 Desktop 窗口承载本机浏览器",
            code: "host_unavailable",
          };
        }
        const kind: LocalBrowserNavMode = isWorkspaceBrowserUrl(target)
          ? "workspace"
          : "web";
        const page = ensurePageKind(
          win,
          entry.pageId,
          kind,
          entry.conversationId,
        );
        const wcNav = page.view.webContents;
        const load = wcNav.loadURL(target);
        await Promise.race([
          load,
          new Promise<void>((r) =>
            setTimeout(r, Number(args.timeout_ms ?? 45_000)),
          ),
        ]);
        await waitLoad(wcNav, 5_000);
        const capture = args.capture !== false;
        const data = await bumpSnapshotAndState(page, { capture });
        data.http_status = null;
        pushNavState(page.pageId);
        return { ok: true, data };
      }
      case "click": {
        const ref = String(args.ref ?? "").trim();
        if (!ref)
          return { ok: false, error: "缺少 ref（先调用 browser_snapshot）" };
        const version = args.snapshot_version;
        if (
          version !== undefined &&
          version !== null &&
          Number(version) !== entry.snapshotVersion
        ) {
          return {
            ok: false,
            error: `ref 版本过期（快照 v${version} ≠ 当前 v${entry.snapshotVersion}）：页面已变化，请重新 browser_snapshot 获取最新 ref`,
          };
        }
        const probe = (await wc.executeJavaScript(
          `(${CLICK_PROBE_JS})(${JSON.stringify(ref)})`,
        )) as {
          was_disabled?: boolean;
          role?: string;
          name?: string;
        };
        const data = await bumpSnapshotAndState(entry, {
          capture: args.capture !== false,
        });
        data.clicked = {
          ref,
          was_disabled: !!probe?.was_disabled,
          role: typeof probe?.role === "string" ? probe.role : "",
          name: typeof probe?.name === "string" ? probe.name : "",
        };
        return { ok: true, data };
      }
      case "type": {
        const ref = String(args.ref ?? "").trim();
        if (!ref)
          return { ok: false, error: "缺少 ref（先调用 browser_snapshot）" };
        const version = args.snapshot_version;
        if (
          version !== undefined &&
          version !== null &&
          Number(version) !== entry.snapshotVersion
        ) {
          return {
            ok: false,
            error: `ref 版本过期（快照 v${version} ≠ 当前 v${entry.snapshotVersion}）：页面已变化，请重新 browser_snapshot 获取最新 ref`,
          };
        }
        const isPw = await wc.executeJavaScript(
          `(function(ref){ const el = document.querySelector('[data-acref="' + ref + '"]'); if (!el) throw new Error('ref_not_found'); return (${IS_PASSWORD_JS})(el); })(${JSON.stringify(ref)})`,
        );
        if (isPw) {
          return {
            ok: false,
            error:
              "password_blocked: AI 不得填写密码框；worker 请 escalate(blocking=true, browser_login=true)；CEO 请 ask_user(browser_login=true) 让用户完成登录",
          };
        }
        const text = String(args.text ?? "");
        // 先聚焦并选中全部，再用 CDP 真实输入（替换契约）；禁 el.value= 直写。
        await wc.executeJavaScript(
          `(${FOCUS_SELECT_JS})(${JSON.stringify(ref)})`,
        );
        const method = await typeViaCdpInsertText(wc, text);
        const readback = (await wc.executeJavaScript(
          `(${READ_TYPED_JS})(${JSON.stringify(ref)})`,
        )) as {
          chars?: number;
          masked?: boolean;
          text?: string | null;
        };
        const requestedChars = Array.from(text).length;
        const actualChars =
          typeof readback?.chars === "number" ? readback.chars : 0;
        const matched =
          !readback?.masked &&
          typeof readback?.text === "string" &&
          readback.text === text;
        const data = await bumpSnapshotAndState(entry, {
          capture: args.capture !== false,
        });
        data.typed = {
          ref,
          requested_chars: requestedChars,
          actual_chars: actualChars,
          matched,
          method,
        };
        return { ok: true, data };
      }
      case "scroll": {
        const dy = Number(args.dy ?? 600) || 600;
        await wc.executeJavaScript(`window.scrollBy(0, ${dy})`);
        await new Promise((r) => setTimeout(r, 200));
        const data = await bumpSnapshotAndState(entry, {
          capture: args.capture !== false,
        });
        return { ok: true, data };
      }
      case "snapshot": {
        entry.snapshotVersion += 1;
        const elements = await wc.executeJavaScript(
          `(${SNAPSHOT_JS})(${entry.snapshotVersion})`,
        );
        const meta = await pageMeta(entry);
        return {
          ok: true,
          data: {
            ...meta,
            snapshot_version: entry.snapshotVersion,
            elements: typeof elements === "string" ? elements : "",
            aria: "",
          },
        };
      }
      case "screenshot": {
        const meta = await pageMeta(entry);
        const data: Record<string, unknown> = { ...meta };
        if (args.capture !== false) {
          const frame = await captureJpegFrame(
            entry,
            jpegQualityFromArgs(args),
          );
          if (frame) {
            data.frame_b64 = frame.frame_b64;
            data.width = frame.width;
            data.height = frame.height;
          }
        }
        return { ok: true, data };
      }
      case "console": {
        const meta = await pageMeta(entry);
        const snap = entry.consoleBuf.snapshot();
        return {
          ok: true,
          data: {
            ...meta,
            messages: snap.messages,
            errors: snap.errors,
            truncated: snap.truncated,
          },
        };
      }
      default:
        return { ok: false, error: `unsupported_action:${action}` };
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("host_unavailable")) {
      return { ok: false, error: msg, code: "host_unavailable" };
    }
    return { ok: false, error: msg };
  }
}

/** @deprecated 用 {@link bridgeDispatchLocalBrowser}；保留给旧调用方。 */
export function bridgeNavigateLocalBrowser(
  pageId: string,
  url: string,
  conversationId: string,
): BrowserResult {
  return navigateLocalBrowserPage(pageId, url, conversationId);
}

/** 测试接缝：重置升级清场标记。 */
export function resetLegacyBrowserClearForTests(): void {
  legacyPagesCleared = false;
}
