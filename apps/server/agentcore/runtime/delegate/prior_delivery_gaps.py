"""Cross-turn one-shot soft block: prior-turn durable delivery blocking gaps.

When the previous turn's journal carries a ``delivery_status`` with
``state ∈ {partial, blocked}`` and non-empty blocking gaps (``severity != warning``),
the next fresh CEO turn gets a short ignorable ``<prior_delivery_gaps>`` ledger on the
volatile prompt tail — same one-shot journal shape as ``redispatch_hint`` (newest *other*
turn only; never conversation-global latest sticky gaps).

Hard rules (intercept-discipline):
- Prompt soft block only — never sink.emit / stamp ``current_delivery_verdict``.
- Structured journal payload only — no user-utterance intent scan.
- One-shot; no cumulative counters; no hard reject.
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.delegate.redispatch_hint import _load_latest_prior_journal
from agentcore.runtime.events.types import EventType

# Align caps with delivery_status emission (_MAX_GAPS / _MAX_FILES).
_MAX_GAPS = 12
_MAX_FILES = 24

_INJECT_STATES = frozenset({"partial", "blocked"})


def _blocking_gaps(gaps: Any) -> list[dict[str, Any]]:
    if not isinstance(gaps, list):
        return []
    out: list[dict[str, Any]] = []
    for g in gaps:
        if not isinstance(g, dict):
            continue
        if g.get("severity") == "warning":
            continue
        out.append(g)
    return out


def extract_prior_turn_delivery_status(
    entries: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Latest ``delivery_status`` payload in a prior-turn journal, or ``None``."""
    if not entries:
        return None
    found: dict[str, Any] | None = None
    for entry in entries:
        if (entry.get("kind") or "") != EventType.DELIVERY_STATUS.value:
            continue
        raw = entry.get("payload")
        if isinstance(raw, dict):
            found = raw
    return found


def prior_turn_has_blocking_delivery_gaps(
    entries: list[dict[str, Any]] | None,
) -> bool:
    """True when prior-turn journal delivery is partial/blocked with blocking gaps."""
    payload = extract_prior_turn_delivery_status(entries)
    if not payload:
        return False
    state = str(payload.get("state") or "").strip()
    if state not in _INJECT_STATES:
        return False
    return bool(_blocking_gaps(payload.get("gaps")))


def render_prior_delivery_gaps(payload: dict[str, Any]) -> str:
    """Format the soft ``<prior_delivery_gaps>`` block from a delivery_status payload."""
    state = str(payload.get("state") or "").strip()
    # 不打印 payload.execution_id：CEO 可见提示里出现真实图 id，且参数名正好是
    # append_to_execution_id，模型会把账本上的 UUID 填进去。补缺口用
    # continue_from_run_id，接续图填 "latest"；与 <recent_team_graph> 故意不打印图
    # id 同一产品意图。
    raw_files = payload.get("delivered_files") or []
    files: list[str] = []
    if isinstance(raw_files, list):
        for p in raw_files:
            s = str(p or "").strip()
            if s:
                files.append(s)
            if len(files) >= _MAX_FILES:
                break
    gaps = _blocking_gaps(payload.get("gaps"))[:_MAX_GAPS]

    file_line = "、".join(files) if files else "（无）"
    gap_lines: list[str] = []
    for g in gaps:
        role = str(g.get("role") or "").strip() or "—"
        desc = str(g.get("description") or "").strip() or "—"
        reason = str(g.get("reason") or "").strip()
        if reason:
            gap_lines.append(f"- role={role}; {desc}; reason={reason}")
        else:
            gap_lines.append(f"- role={role}; {desc}")
    gaps_body = "\n".join(gap_lines) if gap_lines else "- （无）"

    return (
        "<prior_delivery_gaps>\n"
        "【上轮交付缺口】上一回合 durable delivery 仍有阻塞缺口。"
        "本提示一次性、可忽略；本轮用户新目标优先于本旧账本。"
        "若短确认只补缺口：只续跑下列未闭合项；可优先同人 `continue_from_run_id`。"
        "【禁止】整锅重派或重写路径已核文件。\n"
        f"state={state}\n"
        f"accepted/delivered_files: {file_line}\n"
        "blocking gaps:\n"
        f"{gaps_body}\n"
        "</prior_delivery_gaps>"
    )


def apply_gaps_vs_redispatch_mutex(
    gaps_block: str,
    redispatch_block: str,
) -> tuple[str, str]:
    """When gaps soft block is non-empty, suppress redispatch (缺口优先)."""
    gaps = (gaps_block or "").strip()
    if gaps:
        return gaps_block, ""
    return gaps_block or "", redispatch_block or ""


async def build_prior_delivery_gaps_hint(
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
) -> str:
    """``<prior_delivery_gaps>`` when the prior turn fingerprints, else ``\"\"``.

    ``exclude_message_id`` drops the in-flight assistant turn (same as redispatch /
    recent-graph). Does not read or branch on the current user message. Does not
    emit or stamp delivery verdict.
    """
    entries = await _load_latest_prior_journal(
        conversation_id=conversation_id,
        exclude_turn_id=exclude_message_id,
    )
    if not prior_turn_has_blocking_delivery_gaps(entries):
        return ""
    payload = extract_prior_turn_delivery_status(entries)
    if not payload:
        return ""
    return render_prior_delivery_gaps(payload)
