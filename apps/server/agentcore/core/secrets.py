"""Shared secret-shape scrub (logs, tool echoes, write gates).

Heuristic defence-in-depth — not a complete credential detector. Opaque
prefix-less keys (e.g. some domestic LLM vendors) are intentionally out of
scope to avoid over-redacting prose. Same pattern family as SEC-001 LLM body
redaction.
"""

from __future__ import annotations

import re

# OpenAI/DeepSeek/Anthropic/Moonshot/Stripe ``sk[-_]…``, Tavily ``tvly-…``, Groq
# ``gsk_…``, xAI ``xai-…``, Google ``AIza…``, GitHub ``gh?_…``, plus ``Bearer <token>``.
# ``(?<![A-Za-z0-9])`` avoids mid-token false positives (e.g. ``task_created_at`` → ``sk_…``).
SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|tvly|gsk|xai)[-_][A-Za-z0-9._-]{8,}"
    r"|(?<![A-Za-z0-9])AIza[A-Za-z0-9._-]{16,}"
    r"|(?<![A-Za-z0-9])gh[opsru]_[A-Za-z0-9]{16,}"
    r"|[Bb]earer\s+[A-Za-z0-9._-]{8,}"
)

REDACTED = "[REDACTED]"

# Honest copy for write / replace denies (案 20260803-image-gen-byok-egress-boundary B).
SECRET_WRITE_DENY_REASON = (
    "拒绝写入：内容疑似含 API Key / Bearer 等凭据明文（启发式兜底，并非完整拦截）。"
    "【禁止】把第三方 Key 写入工作区；请改用执行工具 env 带入当次进程（不落盘、不回显）。"
)


def redact_secrets(text: str) -> str:
    """Replace known secret shapes with ``[REDACTED]``."""
    if not text:
        return text
    return SECRET_RE.sub(REDACTED, text)


def contains_secret(text: str) -> bool:
    """True when ``text`` matches a known secret shape."""
    if not text:
        return False
    return SECRET_RE.search(text) is not None
