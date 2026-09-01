"""Shared project-verify CLI detection for the short-exec path → ``run``.

Models often stuff ``npm install`` / ``pip install`` / ``tsc`` / ``npm test`` into
a short command, which hard-caps at 60s. Same shape as :mod:`long_running`
(dev servers → ``run`` + ``background``): hard refuse with ``contract_failure``,
tip ``run`` with the same command. Keep the list the single source of truth.
Patterns are command-shaped so imports like ``from 'vitest'`` / ``from 'vite'``
do not false-positive.
"""

from __future__ import annotations

import re

_PROJECT_VERIFY_COMMAND_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:ci|install)\b",
        r"\b(?:pip3?|poetry)\s+(?:install|add)\b",
        r"\buv\s+(?:sync|add)\b",
        r"\buv\s+pip\s+install\b",
        r"\b(?:python3?|py)\s+-m\s+pip\s+install\b",
        r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|build|typecheck|lint|ci)\b",
        r"\b(?:npx|bunx)\s+tsc\b",
        r"\b(?:pnpm|yarn)\s+exec\s+tsc\b",
        r"\btsc\s+--",
        r"\b(?:npx|bunx)\s+(?:vitest|jest)\b",
        r"\b(?:pnpm|yarn)\s+exec\s+(?:vitest|jest)\b",
        r"\bpytest(?:\s|$)",
        r"\b(?:python3?|py)\s+-m\s+pytest\b",
    )
)


def project_verify_command_match(code: str) -> str | None:
    """Return the matched project-verify snippet, or ``None`` if code looks short."""
    for pattern in _PROJECT_VERIFY_COMMAND_RES:
        found = pattern.search(code)
        if found is not None:
            return found.group(0)
    return None


def project_verify_redirect_message(
    matched: str,
    *,
    verify_policy: str = "",
) -> str:
    """Short-path refusal: tip ``run`` (or inner diagnostics) without running the command."""
    policy = (verify_policy or "").strip().lower()
    if policy == "inner":
        return (
            f"跑项目级慢验证请改走内环（检测到：{matched}）。"
            "当前队员为调查/审查姿态（verify_policy=inner）："
            "全量 typecheck/build 请改用内环 code_diagnostics，"
            "或 escalate / 交验收员用 run；"
            "运行时 blank-page / 挂载问题优先 browser 与入口链路，勿烧分钟级 tsc 预算。"
        )
    return (
        f"跑项目级慢验证请用 run（检测到：{matched}）。"
        "本路径约 60s 硬顶，不适配 install / tsc / 全量 test·build。"
        "command 写成同一检查命令。"
    )
