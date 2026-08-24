"""OpenAI-compatible ``function.arguments`` must be a JSON string.

Empty / whitespace / non-JSON is not a legal value. OpenCode Go and DeepSeek
reject the *next* request with 400 ``must be valid JSON`` and we classify that
as non-retryable — killing a worker that already landed files (trace 2de211cd:
empty ``grep`` args executed as ``{}``, then the following LLM call 400'd).

Coerce is wire-only and does not invent fields: illegal slot → ``"{}"``. Schema
validation (missing ``pattern`` etc.) stays the tool's job.
"""

from __future__ import annotations

import json

_EMPTY_OBJECT = "{}"


def coerce_openai_tool_arguments(raw: str | None) -> str:
    """Return a JSON string safe to send as ``function.arguments``.

    Does not mutate the in-memory transcript. Valid JSON (object, array, string,
    number, null) is passed through; empty / whitespace / decode failure → ``{}``.
    """
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return _EMPTY_OBJECT
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return _EMPTY_OBJECT
    return text
