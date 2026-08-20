"""Browser assembly / tool-success latch + open-or-login claim detector.

声称已开页 / 右坞直播 / 已登录——须对本回合 browser 成功对账（禁扫用户气泡意图）。
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from agentcore.runtime.browser.call_identity import is_browser_tool_name

from .core import _positive_hits

_BROWSER_OPEN_OR_LOGIN_CLAIMS = re.compile(
    r"(?:"
    r"(?:已|已经)(?:在)?(?:右坞)?(?:浏览器)?(?:中)?(?:成功)?(?:打开|开页|渲染)|"
    r"(?:右坞|浏览器)(?:已|已经)(?:打开|开页|渲染|就绪)|"
    r"登录页已(?:在右坞)?(?:浏览器)?(?:打开|渲染)|"
    r"(?:已|已经)(?:成功)?(?:登录|登入)(?:成功|完成)?|"
    r"已在右坞(?:浏览器)?(?:打开|渲染)|"
    r"浏览器(?:组件)?(?:已|已经)(?:装配|打开|就绪)"
    r")"
)

_turn_browser_assembled: ContextVar[bool] = ContextVar(
    "turn_browser_assembled", default=False
)
_turn_browser_tool_success: ContextVar[bool] = ContextVar(
    "turn_browser_tool_success", default=False
)


def note_browser_assembled(assembled: bool) -> None:
    """Stamp whether this turn's workspace_context assembled the browser tool."""
    _turn_browser_assembled.set(bool(assembled))


def clear_browser_assembled() -> None:
    _turn_browser_assembled.set(False)


def turn_browser_assembled() -> bool:
    return bool(_turn_browser_assembled.get())


def note_browser_tool_success() -> None:
    """Latch that a browser tool succeeded this turn (sticky until clear)."""
    _turn_browser_tool_success.set(True)


def clear_browser_tool_success() -> None:
    _turn_browser_tool_success.set(False)


def turn_has_browser_tool_success() -> bool:
    return bool(_turn_browser_tool_success.get())


def note_browser_tool_success_from_messages(messages: list[Any] | None) -> None:
    """Scan transcript for successful browser tool results; sticky latch on hit.

    Truth = structured tool trailers / names — not free-text bubble heuristics.
    """
    if turn_has_browser_tool_success():
        return
    if not messages:
        return
    calls: dict[str, str] = {}
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        for tc in getattr(msg, "tool_calls", None) or ():
            tc_id = getattr(tc, "id", None) or ""
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn is not None else None
            if not name and isinstance(tc, dict):
                name = (tc.get("function") or {}).get("name")
                tc_id = str(tc.get("id") or tc_id)
            if tc_id and name:
                calls[str(tc_id)] = str(name)
    for msg in messages:
        if getattr(msg, "role", None) != "tool":
            continue
        tc_id = str(getattr(msg, "tool_call_id", None) or "")
        name = calls.get(tc_id, "")
        if not is_browser_tool_name(name):
            continue
        content = getattr(msg, "content", None) or ""
        if "<!--agentcore:tool_failed-->" in content:
            continue
        note_browser_tool_success()
        return


def claims_browser_open_or_login(content: str) -> bool:
    """True when prose claims browser opened / right-dock live / login succeeded."""
    return _positive_hits(_BROWSER_OPEN_OR_LOGIN_CLAIMS, content or "")


def _browser_claim_rework(content: str) -> str | None:
    """声称已开浏览器/已登录须有 browser tool 成功；未装配同禁."""
    text = content or ""
    if not text.strip() or not claims_browser_open_or_login(text):
        return None
    if turn_has_browser_tool_success():
        return None
    if turn_browser_assembled():
        return (
            "正文声称已打开浏览器/右坞页面或已登录成功，但本回合没有对应 "
            "browser 工具成功结果——"
            "请改为如实说明尚未成功开页/登录，或先调用 browser(action=navigate) 等工具完成后再写；"
            "禁止口头假开浏览器。"
        )
    return (
        "正文声称已打开浏览器/右坞或已登录，但本回合 browser=未装配且无 browser "
        "成功证据——请先如实说明未装配与如何接通，禁止假装已打开或直播页面。"
    )
