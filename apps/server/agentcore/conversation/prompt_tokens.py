"""Single-request prompt size from a turn journal (local fit-check watermark)."""

from __future__ import annotations

from typing import Any


def max_prompt_tokens_from_journal(entries: list[dict[str, Any]] | None) -> int:
    """Largest positive ``llm_call`` prompt this turn, or 0 when none landed.

    Prefers ``usage.last_prompt`` (stamped from TokenUsage) then ``usage.input``.
    Empty-fail turns with no call stay 0 so a later read can skip them.
    """
    best = 0
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("kind") != "llm_call":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        last = int(usage.get("last_prompt") or 0)
        inp = int(usage.get("input") or usage.get("input_tokens") or 0)
        n = last if last > 0 else inp
        if n > best:
            best = n
    return best
