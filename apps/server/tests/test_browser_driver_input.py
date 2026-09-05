"""In-sandbox driver CDP Input injection: mouse/key/text mapping + coord rescale.

Drives ``Driver.input`` with a fake CDP session (no Playwright / no Chromium), asserting the
compact wire verbs map to the right CDP Input calls, frame-pixel coordinates rescale to the
viewport, unknown events are skipped, and the modifier bitmask converts. Content (key/text)
is dispatched but never returned/logged (D17) — the reply carries only the injected count.
"""

from __future__ import annotations

import pytest

from agentcore.tools.sandbox.browser.driver import (
    Driver,
    _modifier_bitmask,
    _normalize_mouse_button,
)


class FakeCdp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict | None = None) -> None:
        self.calls.append((method, params or {}))


def _driver(frame_w: int = 1280, frame_h: int = 800) -> Driver:
    d = Driver()
    d._cdp = FakeCdp()  # _ensure_cdp returns this without creating a real CDP session
    d._last_frame_w = frame_w
    d._last_frame_h = frame_h
    return d


@pytest.mark.asyncio
async def test_input_maps_mouse_key_text_to_cdp():
    d = _driver()
    events = [
        {"kind": "mouse", "type": "move", "x": 100, "y": 50},
        {"kind": "mouse", "type": "down", "x": 100, "y": 50, "button": "left", "click_count": 2},
        {"kind": "mouse", "type": "up", "x": 100, "y": 50, "button": "left"},
        {"kind": "mouse", "type": "wheel", "x": 10, "y": 20, "delta_x": 0, "delta_y": 120},
        {"kind": "key", "type": "down", "key": "Enter", "code": "Enter"},
        {"kind": "text", "text": "hunter2"},
    ]
    res = await d.input({"events": events})
    assert res == {"injected": 6}
    methods = [m for m, _ in d._cdp.calls]
    assert methods == [
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchKeyEvent",
        "Input.insertText",
    ]
    press = d._cdp.calls[1][1]
    assert press["button"] == "left" and press["clickCount"] == 2
    wheel = d._cdp.calls[3][1]
    assert wheel["deltaY"] == 120
    assert d._cdp.calls[4][1]["key"] == "Enter" and d._cdp.calls[4][1]["code"] == "Enter"
    assert d._cdp.calls[5][1]["text"] == "hunter2"


@pytest.mark.asyncio
async def test_input_dom_button_numbers_map_to_cdp_names():
    """Desktop/mobile wire DOM 0|1|2 → CDP left|middle|right (not str(0))."""
    d = _driver()
    events = [
        {"kind": "mouse", "type": "down", "x": 1, "y": 1, "button": 0},
        {"kind": "mouse", "type": "up", "x": 1, "y": 1, "button": 0},
        {"kind": "mouse", "type": "down", "x": 2, "y": 2, "button": 1},
        {"kind": "mouse", "type": "down", "x": 3, "y": 3, "button": 2},
        {"kind": "mouse", "type": "move", "x": 4, "y": 4, "button": 0},
    ]
    res = await d.input({"events": events})
    assert res == {"injected": 5}
    assert d._cdp.calls[0][1]["button"] == "left"
    assert d._cdp.calls[1][1]["button"] == "left"
    assert d._cdp.calls[2][1]["button"] == "middle"
    assert d._cdp.calls[3][1]["button"] == "right"
    assert d._cdp.calls[4][1]["button"] == "left"


def test_normalize_mouse_button_aliases():
    assert _normalize_mouse_button(None) == "left"
    assert _normalize_mouse_button(0) == "left"
    assert _normalize_mouse_button(1) == "middle"
    assert _normalize_mouse_button(2) == "right"
    assert _normalize_mouse_button("left") == "left"
    assert _normalize_mouse_button("RIGHT") == "right"
    assert _normalize_mouse_button("1") == "middle"


@pytest.mark.asyncio
async def test_input_rescales_frame_coords_to_viewport():
    # Frame half the viewport (640x400 vs 1280x800) ⇒ coords double.
    d = _driver(frame_w=640, frame_h=400)
    res = await d.input({"events": [{"kind": "mouse", "type": "move", "x": 100, "y": 50}]})
    assert res == {"injected": 1}
    params = d._cdp.calls[0][1]
    assert params["x"] == 200 and params["y"] == 100


@pytest.mark.asyncio
async def test_input_identity_when_frame_matches_viewport():
    d = _driver(frame_w=1280, frame_h=800)
    await d.input({"events": [{"kind": "mouse", "type": "move", "x": 640, "y": 400}]})
    params = d._cdp.calls[0][1]
    assert params["x"] == 640 and params["y"] == 400


@pytest.mark.asyncio
async def test_input_skips_unknown_events_and_counts_valid_only():
    d = _driver()
    events = [
        {"kind": "mouse", "type": "teleport", "x": 1, "y": 1},  # bad mouse verb
        {"kind": "gamepad"},  # unknown kind
        {"kind": "text", "text": "ok"},
    ]
    res = await d.input({"events": events})
    assert res == {"injected": 1}
    assert [m for m, _ in d._cdp.calls] == ["Input.insertText"]


@pytest.mark.asyncio
async def test_input_key_modifiers_bitmask_applied():
    d = _driver()
    await d.input(
        {"events": [{"kind": "key", "type": "down", "key": "a", "modifiers": ["shift", "ctrl"]}]}
    )
    assert d._cdp.calls[0][1]["modifiers"] == 10  # shift(8) | ctrl(2)


def test_modifier_bitmask_accepts_int_list_and_rejects_bool():
    assert _modifier_bitmask(None) == 0
    assert _modifier_bitmask(0) == 0
    assert _modifier_bitmask(4) == 4
    assert _modifier_bitmask(["shift"]) == 8
    assert _modifier_bitmask(["Meta", "Alt"]) == 5  # meta(4) | alt(1)
    assert _modifier_bitmask(True) == 0  # bool is not a real bitmask


class _FakeLocator:
    """Minimal Playwright-like locator for Driver.type password gate (no Chromium)."""

    def __init__(self, *, is_password: bool) -> None:
        self.is_password = is_password
        self.filled: str | None = None

    async def evaluate(self, _js):
        return self.is_password

    async def fill(self, text: str, timeout: int = 0) -> None:
        self.filled = text


class _FakePageForType:
    """Page stub: evaluate routes FOCUS/READ JS; CDP insertText recorded on FakeCdp."""

    def __init__(self, *, readback: dict) -> None:
        self.url = "https://example.com/"
        self._readback = readback
        self.focus_refs: list[str] = []
        self.evaluate_calls: list[tuple[object, object]] = []

    async def title(self) -> str:
        return "Example"

    async def evaluate(self, js, arg=None):
        self.evaluate_calls.append((js, arg))
        src = str(js)
        if "selectNodeContents" in src or "setSelectionRange" in src or "el.select" in src:
            self.focus_refs.append(str(arg))
            return True
        if "masked" in src or "Array.from(raw)" in src:
            return dict(self._readback)
        if "querySelectorAll" in src:
            return '[e1] textarea: composer | placeholder="…" | value="x"'
        return None

    def locator(self, _sel):
        class _Body:
            async def aria_snapshot(self):
                return ""

        return _Body()


@pytest.mark.asyncio
async def test_type_hard_rejects_password_without_fill():
    d = Driver()
    loc = _FakeLocator(is_password=True)

    def _resolve(_req):
        return loc

    d._resolve_ref = _resolve  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="password_blocked"):
        await d.type({"ref": "e1", "text": "hunter2"})
    assert loc.filled is None


@pytest.mark.asyncio
async def test_type_receipt_matched_true_via_cdp_insert_text():
    """写入成功：typed.matched=true，method=cdp_insertText（与 Local 同形）。"""
    d = Driver()
    loc = _FakeLocator(is_password=False)
    d._resolve_ref = lambda _req: loc  # type: ignore[method-assign]
    text = "你好👋"
    page = _FakePageForType(
        readback={"chars": len(text), "masked": False, "text": text},
    )
    d._page = page  # type: ignore[assignment]
    cdp = FakeCdp()
    d._cdp = cdp  # type: ignore[assignment]

    res = await d.type({"ref": "e1", "text": text, "capture": False})
    assert page.focus_refs == ["e1"]
    methods = [m for m, _ in cdp.calls]
    assert "Input.insertText" in methods
    assert methods.count("Input.dispatchKeyEvent") >= 2
    insert = next(p for m, p in cdp.calls if m == "Input.insertText")
    assert insert == {"text": text}
    assert res["typed"] == {
        "ref": "e1",
        "requested_chars": len(text),
        "actual_chars": len(text),
        "matched": True,
        "method": "cdp_insertText",
    }
    assert loc.filled is None  # 不再走 Playwright fill


@pytest.mark.asyncio
async def test_type_receipt_matched_false_when_readback_diverges():
    """写入未生效：回读与请求不一致 → matched=false（执行器只报事实）。"""
    d = Driver()
    loc = _FakeLocator(is_password=False)
    d._resolve_ref = lambda _req: loc  # type: ignore[method-assign]
    page = _FakePageForType(
        readback={"chars": 0, "masked": False, "text": ""},
    )
    d._page = page  # type: ignore[assignment]
    d._cdp = FakeCdp()  # type: ignore[assignment]

    res = await d.type({"ref": "e2", "text": "hello draft", "capture": False})
    assert res["typed"]["matched"] is False
    assert res["typed"]["requested_chars"] == len("hello draft")
    assert res["typed"]["actual_chars"] == 0
    assert res["typed"]["method"] == "cdp_insertText"


@pytest.mark.asyncio
async def test_click_receipt_was_disabled_true():
    """禁用元素（disabled / aria-disabled）→ clicked.was_disabled=true。"""
    d = Driver()
    d._resolve_ref = lambda _req: object()  # type: ignore[method-assign]

    class _Page:
        url = "https://example.com/"

        async def title(self):
            return "Example"

        async def evaluate(self, js, arg=None):
            src = str(js)
            if "was_disabled" in src:
                assert arg == "e2"
                return {"was_disabled": True, "role": "button", "name": "Send"}
            if "querySelectorAll" in src:
                return "[e2] button disabled: Send"
            return None

        def locator(self, _sel):
            class _Body:
                async def aria_snapshot(self):
                    return "- document"

            return _Body()

    d._page = _Page()  # type: ignore[assignment]
    res = await d.click({"ref": "e2", "snapshot_version": 0, "capture": False})
    assert res["clicked"] == {
        "ref": "e2",
        "was_disabled": True,
        "role": "button",
        "name": "Send",
    }
    assert "elements" in res
    assert "aria" in res  # Sandbox 保留 Playwright aria_snapshot


@pytest.mark.asyncio
async def test_click_receipt_aria_disabled():
    d = Driver()
    d._resolve_ref = lambda _req: object()  # type: ignore[method-assign]

    class _Page:
        url = "u"

        async def title(self):
            return "t"

        async def evaluate(self, js, arg=None):
            if "was_disabled" in str(js):
                return {"was_disabled": True, "role": "button", "name": "Go"}
            return "[e1] button disabled: Go"

        def locator(self, _sel):
            class _Body:
                async def aria_snapshot(self):
                    return ""

            return _Body()

    d._page = _Page()  # type: ignore[assignment]
    res = await d.click({"ref": "e1", "capture": False})
    assert res["clicked"]["was_disabled"] is True


@pytest.mark.asyncio
async def test_page_state_returns_bumped_snapshot_version():
    """Mutations must return post-bump snapshot_version + fresh elements (MCP-style)."""
    d = Driver()
    d._snapshot_version = 4

    class _Body:
        async def aria_snapshot(self):
            return "- document\n  - link: More"

    class _Page:
        url = "https://example.com/"

        async def title(self):
            return "Example"

        async def evaluate(self, _js, version):
            return f"[e1] link: More (v{version})"

        def locator(self, _sel):
            return _Body()

    d._page = _Page()  # type: ignore[assignment]
    state = await d._page_state(capture=False)
    assert state["snapshot_version"] == 5
    assert d._snapshot_version == 5
    assert state["elements"] == "[e1] link: More (v5)"
    assert state["aria"].startswith("- document")


@pytest.mark.asyncio
async def test_page_state_aria_best_effort_when_aria_fails():
    """elements still land when aria_snapshot raises (same as dedicated snapshot)."""
    d = Driver()
    d._snapshot_version = 0

    class _Body:
        async def aria_snapshot(self):
            raise RuntimeError("aria unavailable")

    class _Page:
        url = "https://example.com/"

        async def title(self):
            return "Example"

        async def evaluate(self, _js, version):
            return "[e1] button: Go"

        def locator(self, _sel):
            return _Body()

    d._page = _Page()  # type: ignore[assignment]
    state = await d._page_state(capture=False)
    assert state["snapshot_version"] == 1
    assert state["elements"] == "[e1] button: Go"
    assert state["aria"] == ""
