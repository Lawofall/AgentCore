"""Shared long-running CLI detection for ``code_execute`` ↔ ``terminal`` routing.

Models often put ``npm run dev`` (etc.) into ``code_execute``, which waits for
exit and hits the 60s cap. Patterns are command-shaped so imports like
``from 'vite'`` do not false-positive. Both tools import this module — keep the
list the single source of truth.
"""

from __future__ import annotations

import re
from typing import Literal

_LONG_RUNNING_COMMAND_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start)\b",
        r"\b(?:npx|bunx)\s+(?:vite|next|nuxt|webpack-dev-server)\b",
        r"\b(?:npm|pnpm|yarn)\s+exec\s+(?:vite|next|nuxt)\b",
        r"\bnext\s+dev\b",
        r"\bnuxt\s+dev\b",
        r"\buvicorn\b[^\n]{0,80}--reload\b",
        r"\b(?:python3?|py)\s+-m\s+http\.server\b",
        r"\bmanage\.py\s+runserver\b",
        r"\bflask\s+run\b",
        r"\bnodemon\b",
        r"\bcargo\s+watch\b",
        r"\btail\s+-f\b",
    )
)

# Suggested wait_for for the most common JS/Python ready banners.
DEFAULT_DEV_WAIT_FOR = r"Local:|ready in|Listening|Uvicorn running"


def long_running_command_match(code: str) -> str | None:
    """Return the matched long-running snippet, or ``None`` if code looks finite."""
    for pattern in _LONG_RUNNING_COMMAND_RES:
        found = pattern.search(code)
        if found is not None:
            return found.group(0)
    return None


def long_running_redirect_message(
    matched: str, *, location: Literal["server", "local"] | None
) -> str:
    """``code_execute`` refusal: tip the correct tool without running the command."""
    del location
    return (
        f"禁止用 code_execute 启动长驻进程（检测到：{matched}）。"
        "本工具会等待进程退出，约 60s 必超时，无法托管开发服务器。"
        "请改用 terminal：subcommand=start，填入同一命令，并设 wait_for"
        f"（如 {DEFAULT_DEV_WAIT_FOR}）等到就绪信号后再宣称已启动；"
        "用 list/read 确认进程仍在跑。"
    )


def wait_for_required_message(matched: str) -> str:
    """``terminal.start`` refusal when a long-running command lacks ``wait_for``."""
    return (
        f"启动长驻进程（检测到：{matched}）时必须提供 wait_for，"
        "否则无法验证就绪，禁止仅凭首段输出宣称已启动。"
        f"请带 wait_for（建议 `{DEFAULT_DEV_WAIT_FOR}`）重试 start。"
    )


def readiness_footer(
    *,
    status: str,
    matched: bool | None,
    had_wait_for: bool,
    exit_code: object = None,
) -> str:
    """Model-facing ready/not-ready note appended to ``terminal`` start/read output."""
    if status == "exited":
        suffix = f"（exit_code={exit_code}）" if exit_code is not None else ""
        return (
            f"\n\n【就绪判定】进程已退出{suffix}，不得宣称服务仍在运行。"
        )
    if had_wait_for:
        if matched is True:
            return (
                "\n\n【就绪判定】wait_for 已命中，可报告访问地址；"
                "建议 list 确认 status=running。"
            )
        if matched is False:
            return (
                "\n\n【就绪判定】wait_for 超时未命中。"
                "进程可能仍在启动或已失败——禁止宣称已就绪；"
                "请 read 并带 wait_for 重试，或检查上方 output。"
            )
        return (
            "\n\n【就绪判定】已请求 wait_for 但未返回 matched 字段——"
            "禁止宣称已就绪，请 read 复核。"
        )
    return (
        "\n\n【就绪判定】未使用 wait_for，仅收到首段输出——"
        "禁止宣称已就绪；请 read 并带 wait_for 验证后再报告。"
    )
