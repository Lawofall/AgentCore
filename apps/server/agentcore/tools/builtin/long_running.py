"""Shared long-running CLI detection for the short-exec path → ``run`` background.

Models often put ``npm run dev`` (etc.) into a short command, which waits for
exit and hits the 60s cap. Patterns are command-shaped so imports like
``from 'vite'`` do not false-positive. Keep the list the single source of truth.
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

# Default ready banner for JS/Python dev servers when the model omits wait_for.
DEFAULT_DEV_WAIT_FOR = r"Local:|ready in|Listening|Uvicorn running"


def long_running_command_match(code: str) -> str | None:
    """Return the matched long-running snippet, or ``None`` if code looks finite."""
    for pattern in _LONG_RUNNING_COMMAND_RES:
        found = pattern.search(code)
        if found is not None:
            return found.group(0)
    return None


def effective_wait_for(command: str, wait_for: object) -> str:
    """Ready regex for a start: explicit value, else default when long-running."""
    text = str(wait_for or "").strip()
    if text:
        return text
    if command and long_running_command_match(command):
        return DEFAULT_DEV_WAIT_FOR
    return ""


def long_running_redirect_message(
    matched: str, *, location: Literal["server", "local"] | None
) -> str:
    """Short-path refusal: tip ``run`` background without running the command."""
    del location
    return (
        f"请用 run 启动长驻进程（检测到：{matched}）。"
        "本路径会等待进程退出，约 60s 必超时，无法托管开发服务器。"
        "设 background=true，填入同一命令（省略 wait_for 时用默认就绪信号）；"
        "用 action=read|list 确认进程仍在跑。"
    )


def readiness_footer(
    *,
    status: str,
    matched: bool | None,
    had_wait_for: bool,
    exit_code: object = None,
) -> str:
    """Model-facing ready/not-ready note appended to start/read output."""
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
