"""Cross-turn one-shot soft hint: prior-turn futile same-action retries.

When the previous turn's journal carries ``tool_call`` facts stamped
``cross_turn_retry=futile``, the next fresh CEO turn gets a short ignorable
``<上轮徒劳重试>`` list on the volatile prompt tail — same one-shot
journal shape as ``prior_delivery_gaps`` (newest *other* turn only).

Hard rules (intercept-discipline):
- Prompt information only — never reject, intercept, or change the success path.
- Filter is the stamped field only: ``futile`` in; unknown / missing / ``not_futile`` out.
- One-shot; no cumulative counters; no hard reject.
- Empty → ``\"\"`` so the assembler drops the section (prefix-cache bytes unchanged).
"""

from __future__ import annotations

import json
from typing import Any

from agentcore.runtime.delegate.redispatch_hint import _load_latest_prior_journal
from agentcore.runtime.facts import (
    CROSS_TURN_RETRY_KEY,
    CrossTurnRetry,
    FactKind,
    normalize_cross_turn_retry,
)

# Align volume with ``<上轮交付缺口>``.
_MAX_ITEMS = 12
_MAX_LINE_CHARS = 80

_ID_KEYS = ("destination", "path", "source", "url", "pattern", "query")


def _clip_line(text: str) -> str:
    value = " ".join((text or "").split())
    if len(value) > _MAX_LINE_CHARS:
        return value[:_MAX_LINE_CHARS] + "…"
    return value


def _argument_identifier(arguments: object) -> str:
    """Path-like identifier only — never dump the full arguments JSON."""
    data: dict[str, Any] | None = None
    if isinstance(arguments, dict):
        data = arguments
    elif isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError, ValueError):
            return ""
        if isinstance(parsed, dict):
            data = parsed
    if not data:
        return ""
    for key in _ID_KEYS:
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            return f"{key}={raw.strip().replace('\\', '/')}"
    return ""


def extract_prior_futile_retries(
    entries: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """``{name, identifier}`` rows for prior-turn ``futile`` tool_calls, insertion order.

    Unknown / missing / ``not_futile`` are excluded. Duplicate (name, identifier)
    pairs collapse so the item cap is spent on distinct walls, not a retry counter.
    """
    if not entries:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.TOOL_CALL.value:
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            normalize_cross_turn_retry(payload.get(CROSS_TURN_RETRY_KEY))
            != CrossTurnRetry.FUTILE.value
        ):
            continue
        name = str(payload.get("name") or "").strip() or "—"
        ident = _argument_identifier(payload.get("arguments"))
        key = (name, ident)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "identifier": ident})
        if len(out) >= _MAX_ITEMS:
            break
    return out


def render_prior_futile_retries(rows: list[dict[str, str]]) -> str:
    """Format the soft ``<上轮徒劳重试>`` block; empty rows → ``\"\"``."""
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows:
        name = str(row.get("name") or "").strip() or "—"
        ident = str(row.get("identifier") or "").strip()
        body = f"- {name} {ident}".rstrip() if ident else f"- {name}"
        lines.append(_clip_line(body))
    return (
        "<上轮徒劳重试>\n"
        "【上轮原样重试徒劳】上一回合下列工具调用被判定「同一动作再试也不会成功」。"
        "本提示一次性、可忽略；本轮用户新目标优先。"
        "不得据此拒绝本轮其它路径。"
        "若本轮授权或环境已变，按当前实际情况判断。\n"
        + "\n".join(lines)
        + "\n</上轮徒劳重试>"
    )


async def build_prior_futile_retries_hint(
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
) -> str:
    """``<上轮徒劳重试>`` when the prior other turn fingerprints, else ``\"\"``.

    ``exclude_message_id`` drops the in-flight assistant turn (same as redispatch /
    gaps / recent-graph). Does not read or branch on the current user message.
    Does not reject or change the success path.
    """
    entries = await _load_latest_prior_journal(
        conversation_id=conversation_id,
        exclude_turn_id=exclude_message_id,
    )
    rows = extract_prior_futile_retries(entries)
    if not rows:
        return ""
    return render_prior_futile_retries(rows)
