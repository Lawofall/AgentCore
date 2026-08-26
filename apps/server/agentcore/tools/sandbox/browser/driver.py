"""In-sandbox browser driver — the sandbox side of the D9 stdio JSON-RPC channel.

Runs INSIDE the runsc sandbox as a persistent process (staged into the session scratch by
``gvisor_session``). It drives ONE Playwright Chromium and speaks newline-delimited JSON
over stdin/stdout:

    host → driver (stdin):   {"id": <int>, "cmd": "<action>", ...args}\\n
    driver → host (stdout):  {"id": <int>, "ok": <bool>, ...data, "frame_b64"?}\\n

M1 (D14) makes it **async** (``playwright.async_api`` + ``asyncio``) so a CDP
``Page.startScreencast`` can push live frames CONCURRENTLY with command handling. Live
frames ride the same stdout as driver-INITIATED event lines (no request id):

    driver → host (stdout):  {"event": "live_frame", "frame_b64": <b64>, "width", "height"}\\n

The gVisor screencast gate (scripts/poc_browser_gvisor/run_screencast.py) proved this path
(~57fps @ ~14KB/frame @ q60/1280). The M0 command semantics (plus ``console``
evidence), the ``ready`` handshake, inline ``frame_b64`` keyframe replies and the
8MB line limit are all unchanged.

CRITICAL: only JSON lines go to stdout (fd 1); all Playwright/Chromium chatter goes to
stderr so the host's reader never desyncs. Frames are emitted from the CDP callback with a
single atomic write (no ``await`` between write and flush), so a live frame can never
interleave with a command reply. This file is a SELF-CONTAINED script (stdlib + playwright
only — NO agentcore imports): it executes where only the ro-bound system site-packages exist.

Public egress is pinned to the host SSRF proxy via ``BROWSER_PROXY`` (--proxy-server).
Loopback (guest vite) must bypass that proxy — SSRF would refuse 127.0.0.1.

Env: BROWSER_PROXY, BROWSER_WIDTH, BROWSER_HEIGHT, BROWSER_JPEG_Q. Do not inherit
the desk packaging HTTP_PROXY.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import traceback

WIDTH = int(os.environ.get("BROWSER_WIDTH", "1280"))
HEIGHT = int(os.environ.get("BROWSER_HEIGHT", "800"))
JPEG_Q = int(os.environ.get("BROWSER_JPEG_Q", "70"))
PROXY = os.environ.get("BROWSER_PROXY", "").strip()

def chromium_launch_args(proxy: str) -> list[str]:
    """Chromium flags: public traffic via SSRF proxy; guest loopback must bypass it."""
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    if proxy:
        args.append(f"--proxy-server={proxy}")
        # Chromium otherwise sends loopback through the proxy; the SSRF filter
        # refuses 127.0.0.1, so guest vite would be invisible. ``<-loopback>`` is
        # Chromium's bypass token (plus explicit loopback names).
        args.append("--proxy-bypass-list=<-loopback>;127.0.0.1;localhost;[::1]")
    return args


CHROME_ARGS = chromium_launch_args(PROXY)

# The commands the host may invoke (allowlist keeps ``cmd:"start"`` / dunder probing from
# reaching internal methods). ``input`` (M2 · D17) injects user takeover events via CDP Input.
_COMMANDS = frozenset(
    {
        "navigate",
        "click",
        "type",
        "scroll",
        "snapshot",
        "screenshot",
        "console",
        "set_content",
        "set_viewport",
        "ping",
        "start_screencast",
        "stop_screencast",
        "input",
        "close",
    }
)

# Ring-buffer caps for browser_console evidence (hard limits; never return huge blobs).
_CONSOLE_MAX_MESSAGES = 80
_CONSOLE_MAX_ERRORS = 40
_CONSOLE_MAX_TEXT = 500
_CONSOLE_MAX_STACK = 1500
_SECRET_RE = re.compile(
    r"(password|passwd|pwd|token|secret|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _looks_like_blob(text: str) -> bool:
    if len(text) < 400:
        return False
    if len(text) > 4000:
        return True
    # Base64 / data-URL payloads tend to be long runs without whitespace.
    sample = text[:240]
    if any(c.isspace() for c in sample):
        return False
    compact = "".join(sample.split())
    return (
        bool(compact) and len(compact) >= 200 and all(c.isalnum() or c in "+/=_-" for c in compact)
    )


def _scrub_console_text(raw, max_len: int = _CONSOLE_MAX_TEXT) -> str:
    t = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", str(raw or ""))
    if _looks_like_blob(t):
        return t[:80] + "…[truncated blob]"
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


# CDP Input event-type maps (M2 接管注入): our compact wire verbs → CDP domain verbs.
_MOUSE_TYPES = {
    "down": "mousePressed",
    "up": "mouseReleased",
    "move": "mouseMoved",
    "wheel": "mouseWheel",
}
_KEY_TYPES = {"down": "keyDown", "up": "keyUp"}
# DOM MouseEvent.button 0|1|2 → Playwright/CDP button names (schema also normalizes).
_MOUSE_BUTTONS = {
    0: "left",
    1: "middle",
    2: "right",
    "0": "left",
    "1": "middle",
    "2": "right",
    "left": "left",
    "middle": "middle",
    "right": "right",
}
# CDP dispatchKeyEvent modifier bitmask (Alt=1, Ctrl=2, Meta=4, Shift=8).
_MODIFIER_BITS = {
    "alt": 1,
    "control": 2,
    "ctrl": 2,
    "meta": 4,
    "cmd": 4,
    "command": 4,
    "shift": 8,
}


def _normalize_mouse_button(button) -> str:
    """Map DOM 0|1|2 or name strings to CDP left|right|middle; default left."""
    if button is None or isinstance(button, bool):
        return "left"
    if isinstance(button, int):
        return _MOUSE_BUTTONS.get(button, "left")
    key = str(button)
    return _MOUSE_BUTTONS.get(key) or _MOUSE_BUTTONS.get(key.lower()) or "left"


def _modifier_bitmask(mods) -> int:
    """Accept an int bitmask or a list of modifier names → CDP modifier bitmask."""
    if isinstance(mods, bool):  # bool is an int subclass — reject the accidental True/False
        return 0
    if isinstance(mods, int):
        return mods
    if not mods:
        return 0
    bits = 0
    for name in mods:
        bits |= _MODIFIER_BITS.get(str(name).lower(), 0)
    return bits


# Interactive-element snapshot — wire twin of Local host SNAPSHOT_JS (apps/desktop/.../host.ts).
# Form controls: placeholder / value 分列；disabled 显式标注；尾部 visible_text（硬上限）。
# Password: value 仅长度/掩码，绝不明文。Keep Playwright aria_snapshot separately.
_SNAPSHOT_JS = r"""
(version) => {
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
  const clip = (s, n) => s.trim().replace(/\s+/g, ' ').slice(0, n);
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
        const len = typeof el.value === 'string' ? el.value.length : 0;
        line += ' | value=' + (len > 0 ? '"***"' : '""') + ' (chars=' + len + ')';
      } else {
        let raw = '';
        if (el.isContentEditable) raw = el.innerText || el.textContent || '';
        else if ('value' in el) raw = String(el.value ?? '');
        const full = String(raw);
        const shown = clip(full, VALUE_MAX);
        const truncated = full.trim().replace(/\s+/g, ' ').length > VALUE_MAX;
        line += ' | value=' + JSON.stringify(shown)
          + (truncated ? '…' : '');
      }
    }
    out.push(line);
    if (n >= MAX_ELEMENTS) break;
  }
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
      const t = String(node.textContent || '').replace(/\s+/g, ' ').trim();
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
  return out.join('\n');
}
"""

# Evaluated on the resolved element before type — DOM type/autocomplete is authoritative.
_IS_PASSWORD_JS = r"""
(el) => {
  const type = (el.getAttribute('type') || '').toLowerCase();
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  return type === 'password' || ac.includes('password');
}
"""

# Focus + select-all，供 CDP Input.insertText 替换既有内容（与 Local FOCUS_SELECT_JS 对齐）。
_FOCUS_SELECT_JS = r"""
(ref) => {
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
}
"""

# 回读元素实际内容；password 只给长度，绝不明文（与 Local READ_TYPED_JS 对齐）。
_READ_TYPED_JS = r"""
(ref) => {
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
}
"""

# Click 前事实采集 + DOM click（含 disabled / aria-disabled；与 Local CLICK_PROBE_JS 对齐）。
_CLICK_PROBE_JS = r"""
(ref) => {
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
  const nameSrc = el.getAttribute('aria-label')
    || el.textContent || el.getAttribute('placeholder') || '';
  const name = String(nameSrc || '').trim().replace(/\s+/g, ' ').slice(0, 100);
  el.click();
  return { was_disabled: was_disabled, role: role, name: name };
}
"""


def _log(msg: str) -> None:
    sys.stderr.write(f"[browser-driver] {msg}\n")
    sys.stderr.flush()


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Driver:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._snapshot_version = 0
        self._cdp = None
        self._screencast_on = False
        # Last screencast frame's device dims — the coordinate space the host's takeover
        # events live in (帧像素空间). Used to rescale input to the viewport (M2).
        self._last_frame_w = WIDTH
        self._last_frame_h = HEIGHT
        # Read-only runtime evidence for browser_console (page console + pageerror).
        self._console_messages: list[dict] = []
        self._console_errors: list[dict] = []
        self._console_messages_dropped = 0
        self._console_errors_dropped = 0

    def _push_console_message(self, level: str, text: str) -> None:
        entry = {
            "level": (level or "log").lower(),
            "text": _scrub_console_text(text),
            "timestamp": time.time(),
        }
        if len(self._console_messages) >= _CONSOLE_MAX_MESSAGES:
            self._console_messages.pop(0)
            self._console_messages_dropped += 1
        self._console_messages.append(entry)

    def _push_console_error(self, message: str, stack: str | None = None) -> None:
        entry: dict = {
            "message": _scrub_console_text(message),
            "timestamp": time.time(),
        }
        if stack:
            entry["stack"] = _scrub_console_text(stack, _CONSOLE_MAX_STACK)
        if len(self._console_errors) >= _CONSOLE_MAX_ERRORS:
            self._console_errors.pop(0)
            self._console_errors_dropped += 1
        self._console_errors.append(entry)

    def _on_page_console(self, msg) -> None:
        try:
            level = getattr(msg, "type", None) or "log"
            text = msg.text if hasattr(msg, "text") else str(msg)
            self._push_console_message(str(level), text)
        except Exception as exc:  # noqa: BLE001 - never break the page loop
            _log(f"console capture failed: {type(exc).__name__}")

    def _on_page_error(self, err) -> None:
        try:
            message = getattr(err, "message", None) or str(err)
            stack = getattr(err, "stack", None)
            self._push_console_error(str(message), str(stack) if stack else None)
        except Exception as exc:  # noqa: BLE001
            _log(f"pageerror capture failed: {type(exc).__name__}")

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=CHROME_ARGS)
        self._ctx = await self._browser.new_context(viewport={"width": WIDTH, "height": HEIGHT})
        self._page = await self._ctx.new_page()
        self._page.on("console", self._on_page_console)
        self._page.on("pageerror", self._on_page_error)

    # -- helpers ---------------------------------------------------------------
    async def _keyframe_b64(self) -> str:
        png = await self._page.screenshot(type="jpeg", quality=JPEG_Q)
        return base64.b64encode(png).decode("ascii")

    async def _page_state(self, *, capture: bool) -> dict:
        state = {"final_url": self._page.url, "title": await self._page.title()}
        if capture:
            state["frame_b64"] = await self._keyframe_b64()
        # Any page mutation invalidates prior snapshot refs (防错点).
        # Bump version and re-stamp refs (Playwright MCP–style) so click/type/scroll/
        # navigate success already carries a usable elements table — dedicated
        # browser_snapshot remains available for a fuller ARIA pass.
        self._snapshot_version += 1
        state["snapshot_version"] = self._snapshot_version
        state["elements"] = await self._page.evaluate(_SNAPSHOT_JS, self._snapshot_version)
        try:
            aria = await self._page.locator("body").aria_snapshot()
        except Exception:  # noqa: BLE001 - aria is best-effort context
            aria = ""
        # Slightly tighter than dedicated snapshot (6000) to keep mutation payloads lean.
        state["aria"] = (aria or "")[:4000]
        return state

    def _resolve_ref(self, req: dict):
        ref = req.get("ref")
        version = req.get("snapshot_version")
        if not ref:
            raise ValueError("缺少 ref（先调用 browser_snapshot 获取元素 ref）")
        if version is not None and int(version) != self._snapshot_version:
            raise ValueError(
                f"ref 版本过期（快照 v{version} ≠ 当前 v{self._snapshot_version}）：页面已变化，"
                "请重新 browser_snapshot 获取最新 ref"
            )
        return self._page.locator(f'[data-acref="{ref}"]')

    # -- commands --------------------------------------------------------------
    async def navigate(self, req: dict) -> dict:
        resp = await self._page.goto(
            req["url"], wait_until="load", timeout=int(req.get("timeout_ms", 45000))
        )
        state = await self._page_state(capture=bool(req.get("capture", True)))
        state["http_status"] = resp.status if resp else None
        return state

    async def click(self, req: dict) -> dict:
        # Validate ref / snapshot_version; probe+click via DOM (not Playwright actionability)
        # so disabled / aria-disabled still yield a structured ``clicked`` receipt.
        self._resolve_ref(req)
        ref = str(req.get("ref") or "")
        probe = await self._page.evaluate(_CLICK_PROBE_JS, ref)
        if not isinstance(probe, dict):
            probe = {}
        state = await self._page_state(capture=bool(req.get("capture", True)))
        state["clicked"] = {
            "ref": ref,
            "was_disabled": bool(probe.get("was_disabled")),
            "role": probe["role"] if isinstance(probe.get("role"), str) else "",
            "name": probe["name"] if isinstance(probe.get("name"), str) else "",
        }
        return state

    async def _type_via_cdp_insert_text(self, text: str) -> str:
        """Real input via CDP Input.insertText (CJK/emoji/contenteditable) — Local twin.

        Replace semantics: Backspace clears the focused selection, then insertText.
        """
        cdp = await self._ensure_cdp()
        await cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
                "nativeVirtualKeyCode": 8,
            },
        )
        await cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
                "nativeVirtualKeyCode": 8,
            },
        )
        if text:
            await cdp.send("Input.insertText", {"text": text})
        return "cdp_insertText"

    async def type(self, req: dict) -> dict:
        loc = self._resolve_ref(req)
        ref = str(req.get("ref") or "")
        # Hard-reject password fields (DOM-authoritative). Host maps ``password_blocked``
        # in the error string to a machine-readable ToolResult; never type.
        if await loc.evaluate(_IS_PASSWORD_JS):
            raise ValueError(
                "password_blocked: AI 不得填写密码框；"
                "worker 请 escalate(blocking=true, browser_login=true)；"
                "CEO 请 ask_user(browser_login=true) 让用户接管登录"
            )
        text = str(req.get("text", "") or "")
        # Focus + select-all, then CDP insertText (fill 对受控/contenteditable 不可靠).
        await self._page.evaluate(_FOCUS_SELECT_JS, ref)
        method = await self._type_via_cdp_insert_text(text)
        readback = await self._page.evaluate(_READ_TYPED_JS, ref)
        if not isinstance(readback, dict):
            readback = {}
        requested_chars = len(text)
        actual_chars = (
            int(readback["chars"]) if isinstance(readback.get("chars"), (int, float)) else 0
        )
        matched = (
            not readback.get("masked")
            and isinstance(readback.get("text"), str)
            and readback["text"] == text
        )
        state = await self._page_state(capture=bool(req.get("capture", True)))
        state["typed"] = {
            "ref": ref,
            "requested_chars": requested_chars,
            "actual_chars": actual_chars,
            "matched": matched,
            "method": method,
        }
        return state

    async def scroll(self, req: dict) -> dict:
        await self._page.mouse.wheel(0, int(req.get("dy", 600)))
        await self._page.wait_for_timeout(200)
        return await self._page_state(capture=bool(req.get("capture", True)))

    async def snapshot(self, _req: dict) -> dict:
        self._snapshot_version += 1
        tree = await self._page.evaluate(_SNAPSHOT_JS, self._snapshot_version)
        try:
            aria = await self._page.locator("body").aria_snapshot()
        except Exception:  # noqa: BLE001 - aria snapshot is best-effort context
            aria = ""
        return {
            "final_url": self._page.url,
            "title": await self._page.title(),
            "snapshot_version": self._snapshot_version,
            "elements": tree,
            "aria": (aria or "")[:6000],
        }

    async def screenshot(self, req: dict) -> dict:
        state = {"final_url": self._page.url, "title": await self._page.title()}
        if req.get("capture", True):
            state["frame_b64"] = await self._keyframe_b64()
        return state

    async def console(self, _req: dict) -> dict:
        """Return ring-buffered page console + pageerror (read-only; no keyframe)."""
        return {
            "final_url": self._page.url,
            "title": await self._page.title(),
            "messages": list(self._console_messages),
            "errors": list(self._console_errors),
            "truncated": {
                "messages_dropped": self._console_messages_dropped,
                "errors_dropped": self._console_errors_dropped,
            },
        }

    async def set_viewport(self, req: dict) -> dict:
        """Host-only: resize viewport for multi-breakpoint self-test (P1c critic)."""
        w = int(req.get("width") or WIDTH)
        h = int(req.get("height") or HEIGHT)
        await self._page.set_viewport_size({"width": w, "height": h})
        return await self._page_state(capture=bool(req.get("capture", False)))

    async def set_content(self, req: dict) -> dict:
        """Host-only: load assembled HTML (workspace preview) without a URL."""
        html = req.get("html") or ""
        await self._page.set_content(
            str(html),
            wait_until="load",
            timeout=int(req.get("timeout_ms", 45000)),
        )
        return await self._page_state(capture=bool(req.get("capture", False)))

    async def ping(self, _req: dict) -> dict:
        return {"pong": True}

    # -- CDP session (shared by screencast + input) ---------------------------
    async def _ensure_cdp(self):
        """Lazily open the page's CDP session (once), wiring the screencast frame sink."""
        if self._cdp is None:
            self._cdp = await self._ctx.new_cdp_session(self._page)
            self._cdp.on("Page.screencastFrame", self._on_screencast_frame)
        return self._cdp

    # -- live screencast (M1 · D14) -------------------------------------------
    async def start_screencast(self, req: dict) -> dict:
        """Begin CDP screencast; frames flow as ``live_frame`` event lines until stop."""
        if self._screencast_on:
            return {"screencast": "already_on"}
        await self._ensure_cdp()
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": int(req.get("quality", 60)),
                "maxWidth": int(req.get("max_width", WIDTH)),
                "maxHeight": int(req.get("max_height", HEIGHT)),
                "everyNthFrame": max(1, int(req.get("every_nth_frame", 1))),
            },
        )
        self._screencast_on = True
        return {"screencast": "started"}

    async def stop_screencast(self, _req: dict) -> dict:
        if not self._screencast_on:
            return {"screencast": "already_off"}
        try:
            if self._cdp is not None:
                await self._cdp.send("Page.stopScreencast")
        finally:
            self._screencast_on = False
        return {"screencast": "stopped"}

    def _on_screencast_frame(self, params: dict) -> None:
        # CDP delivers the frame ALREADY base64-encoded (jpeg): pass it straight through
        # (no host decode/re-encode). One atomic write ⇒ never interleaves a command reply.
        md = params.get("metadata") or {}
        w = int(md.get("deviceWidth") or WIDTH)
        h = int(md.get("deviceHeight") or HEIGHT)
        # Remember the frame's dims: takeover input coordinates are in this space (M2).
        self._last_frame_w = w or WIDTH
        self._last_frame_h = h or HEIGHT
        _emit(
            {
                "event": "live_frame",
                "frame_b64": params.get("data", ""),
                "width": w,
                "height": h,
            }
        )
        # Ack is REQUIRED or Chromium stops after a few frames; it also IS the backpressure
        # knob (we ack after emitting, so a blocked stdout throttles production).
        asyncio.create_task(self._ack(params.get("sessionId")))

    async def _ack(self, session_id) -> None:
        try:
            if self._cdp is not None:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception as exc:  # noqa: BLE001 - late acks after stop/close are harmless
            _log(f"screencast ack failed: {type(exc).__name__}: {exc}")

    # -- user takeover input injection (M2 · D17) -----------------------------
    async def input(self, req: dict) -> dict:
        """Inject a batch of takeover events via the CDP Input domain.

        Coordinates arrive in frame-pixel space (``browser_live_frame`` dims) and are
        rescaled to the viewport here. Events dispatch in order; a bad single event is
        skipped (never fails the batch). CRITICAL: key/text CONTENT is never logged (D17 —
        it may be a password); only counts/kinds are observable.
        """
        cdp = await self._ensure_cdp()
        events = req.get("events") or []
        fw = float(req.get("frame_width") or self._last_frame_w or WIDTH)
        fh = float(req.get("frame_height") or self._last_frame_h or HEIGHT)
        sx = (WIDTH / fw) if fw else 1.0
        sy = (HEIGHT / fh) if fh else 1.0
        injected = 0
        for ev in events:
            kind = ev.get("kind")
            try:
                if kind == "mouse":
                    ok = await self._inject_mouse(cdp, ev, sx, sy)
                elif kind == "key":
                    ok = await self._inject_key(cdp, ev)
                elif kind == "text":
                    await cdp.send("Input.insertText", {"text": str(ev.get("text") or "")})
                    ok = True
                else:
                    ok = False
            except Exception as exc:  # noqa: BLE001 - skip a bad event; NEVER log its content
                _log(f"input event skipped (kind={kind}): {type(exc).__name__}")
                ok = False
            if ok:
                injected += 1
        return {"injected": injected}

    async def _inject_mouse(self, cdp, ev: dict, sx: float, sy: float) -> bool:
        cdp_type = _MOUSE_TYPES.get(str(ev.get("type")))
        if cdp_type is None:
            return False
        params: dict = {
            "type": cdp_type,
            "x": float(ev.get("x") or 0) * sx,
            "y": float(ev.get("y") or 0) * sy,
        }
        button = ev.get("button")
        if cdp_type in ("mousePressed", "mouseReleased"):
            params["button"] = _normalize_mouse_button(button)
            params["clickCount"] = int(ev.get("click_count") or 1)
        elif cdp_type == "mouseMoved":
            # Include button even when DOM sends 0 (falsy) — held-button moves.
            if button is not None and not isinstance(button, bool):
                params["button"] = _normalize_mouse_button(button)
        elif cdp_type == "mouseWheel":
            params["deltaX"] = float(ev.get("delta_x") or 0)
            params["deltaY"] = float(ev.get("delta_y") or 0)
        await cdp.send("Input.dispatchMouseEvent", params)
        return True

    async def _inject_key(self, cdp, ev: dict) -> bool:
        cdp_type = _KEY_TYPES.get(str(ev.get("type")))
        if cdp_type is None:
            return False
        params: dict = {"type": cdp_type, "key": str(ev.get("key") or "")}
        code = ev.get("code")
        if code:
            params["code"] = str(code)
        mods = _modifier_bitmask(ev.get("modifiers"))
        if mods:
            params["modifiers"] = mods
        await cdp.send("Input.dispatchKeyEvent", params)
        return True

    async def close(self, _req: dict) -> dict:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        return {"closed": True}


async def _stdin_reader() -> asyncio.StreamReader:
    """Async line reader over fd 0 (Linux sandbox) so stdin + screencast run concurrently."""
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader(limit=1024 * 1024)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader


async def _run() -> int:
    driver = Driver()
    try:
        await driver.start()
    except Exception:  # noqa: BLE001 - launch failure must reach the host
        _log("launch failed:\n" + traceback.format_exc())
        _emit({"id": 0, "event": "ready", "ok": False, "error": "browser launch failed"})
        return 2
    _emit({"id": 0, "event": "ready", "ok": True})

    reader = await _stdin_reader()
    while True:
        raw = await reader.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"id": None, "ok": False, "error": f"bad json: {exc}"})
            continue
        rid = req.get("id")
        cmd = req.get("cmd", "")
        if cmd not in _COMMANDS:
            _emit({"id": rid, "ok": False, "error": f"unknown cmd: {cmd}"})
            continue
        handler = getattr(driver, cmd)
        try:
            result = await handler(req)
            _emit({"id": rid, "ok": True, **result})
        except Exception as exc:  # noqa: BLE001 - report, never crash the loop
            _log(f"cmd {cmd} failed:\n" + traceback.format_exc())
            _emit({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        if cmd == "close":
            break
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 - a fatal loop error must be visible on stderr
        _log("fatal:\n" + traceback.format_exc())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
