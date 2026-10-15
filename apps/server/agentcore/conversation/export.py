"""Conversation export serializers (导出对话: Markdown + JSON).

Turns a conversation's full transcript into a downloadable artifact. Two formats:

- **Markdown** — a clean, human-readable Q&A record: user / assistant turns with
  their text, web sources as links, and attachment names. Deliberately content-only
  (no reasoning / cost / tool internals) — the document a user would actually read
  or paste elsewhere.
- **JSON** — full message fidelity for power users / re-import: every persisted
  field of the user/assistant rows (content, reasoning, citations, attachments,
  usage snapshot, timestamps), minus internal-only ids. ``finish_reason`` is
  projected from turn journal (``turn_end``) or ``usage.finish_reason`` when
  present — Message ORM no longer carries that column; omit honestly when neither
  source has it. Pure-failure empty assistants add ``visible_text`` (same
  projection Markdown uses) without rewriting ``content``.

The route reads the whole transcript (``MessageRepository.list_all_for_conversation``)
and optionally a journal map, then hands them here; these functions are pure
(no DB / IO), so they unit-test directly. Spend is never exported — it lives in
the cost ledger, not the message body.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from agentcore.conversation.failure_visible import export_visible_text
from agentcore.db.models import Conversation, Message
from agentcore.runtime.journal import KIND_TURN_END

# Only conversational turns are exported; system / tool-shaped rows (no human-facing
# body) are skipped so the artifact reads as a clean dialogue.
_EXPORTED_ROLES = ("user", "assistant")

_ROLE_LABELS = {"user": "用户", "assistant": "GAgent"}


def _visible_export_rows(
    messages: Sequence[Message],
    journal_map: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> list[tuple[Message, str]]:
    """User/assistant turns with readable body, in render order.

    Ordinary empty rows (e.g. interrupted tool-only assistants) are dropped.
    Pure-failure assistants (empty content + error metadata) keep a section whose
    body is projected from usage / journal error — not from dual-written content.
    """
    rows: list[tuple[Message, str]] = []
    for m in messages:
        if m.role not in _EXPORTED_ROLES:
            continue
        entries = journal_map.get(m.id) if journal_map is not None else None
        text = export_visible_text(m, journal_entries=entries)
        if text:
            rows.append((m, text))
    return rows


def _fmt_ts(value: datetime | None) -> str:
    """Human-readable minute-precision timestamp for the Markdown header."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def _finish_reason_from_entries(entries: Sequence[dict[str, Any]] | None) -> str | None:
    """Terminal finish_reason from a turn's journal ``turn_end`` fact, if any."""
    if not entries:
        return None
    for entry in reversed(entries):
        if entry.get("kind") != KIND_TURN_END:
            continue
        payload = entry.get("payload") or {}
        fr = payload.get("finish_reason")
        return fr if isinstance(fr, str) and fr else None
    return None


def _export_finish_reason(
    message: Message,
    entries: Sequence[dict[str, Any]] | None,
) -> str | None:
    """Project finish_reason from journal first, then usage — never invent one.

    Message ORM dropped the ``finish_reason`` column; the durable sources are
    ``turn_journal.turn_end`` and (for some salvage / cancel paths) ``usage``.
    Plain successful chat turns often have neither persisted — omit in that case.
    """
    from_journal = _finish_reason_from_entries(entries)
    if from_journal is not None:
        return from_journal
    usage = message.usage if isinstance(message.usage, dict) else None
    if usage is None:
        return None
    fr = usage.get("finish_reason")
    return fr if isinstance(fr, str) and fr else None


def conversation_to_markdown(
    conversation: Conversation,
    messages: Sequence[Message],
    *,
    journal_map: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> str:
    """Render a conversation as a clean Markdown transcript (the default export).

    Each turn becomes a ``##`` section headed by its author and time, followed by
    the message text. Web sources (citations) and attachment names are appended per
    turn as small reference lists when present. Pure-failure empty assistants still
    export a readable error sentence from usage / journal when available.
    """
    title = (conversation.title or "").strip() or "未命名对话"
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"> 由 GAgent 导出 · {_fmt_ts(datetime.now(UTC))} UTC")
    lines.append("")

    for msg, body in _visible_export_rows(messages, journal_map):
        label = _ROLE_LABELS.get(msg.role, msg.role)
        ts = _fmt_ts(msg.created_at)
        heading = f"## {label}" + (f" · {ts}" if ts else "")
        lines.append(heading)
        lines.append("")
        lines.append(body)
        lines.append("")

        citations = msg.citations or []
        if citations:
            lines.append("**来源**")
            lines.append("")
            for c in citations:
                if not isinstance(c, dict):
                    continue
                url = str(c.get("url") or "").strip()
                if not url:
                    continue
                name = str(c.get("title") or c.get("site") or url).strip()
                lines.append(f"- [{name}]({url})")
            lines.append("")

        attachments = msg.attachments or []
        names = [
            str(a.get("name") or a.get("path") or "").strip()
            for a in attachments
            if isinstance(a, dict)
        ]
        names = [n for n in names if n]
        if names:
            lines.append("**附件**：" + "、".join(names))
            lines.append("")

    # Collapse the trailing blank line into a single terminal newline.
    return "\n".join(lines).rstrip() + "\n"


def conversation_to_json(
    conversation: Conversation,
    messages: Sequence[Message],
    *,
    journal_map: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> dict:
    """Render a conversation as a full-fidelity JSON document (power-user export).

    Carries every human-meaningful field of the user/assistant rows so the export
    round-trips far more faithfully than Markdown. Internal-only ids (trace_id, the
    row/conversation UUIDs beyond the conversation id) are kept minimal; spend is
    intentionally absent (it lives in the cost ledger).

    ``journal_map`` is message_id → ordered turn_journal entries (from the export
    route). ``finish_reason`` is included only when journal or usage can supply it.
    Empty failed assistants may also carry ``visible_text`` (readable error
    projection); ``content`` remains the on-disk body.
    """
    exported: list[dict[str, Any]] = []
    for m in messages:
        if m.role not in _EXPORTED_ROLES:
            continue
        row: dict[str, Any] = {
            "role": m.role,
            # Content stays the persisted body (empty for pure failure — never
            # dual-write the error sentence back into the authoritative field).
            "content": m.content,
            "reasoning_content": m.reasoning_content,
            "citations": m.citations or [],
            "attachments": m.attachments or [],
            "usage": m.usage,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        entries = journal_map.get(m.id) if journal_map is not None else None
        finish_reason = _export_finish_reason(m, entries)
        if finish_reason is not None:
            row["finish_reason"] = finish_reason
        # Empty pure-failure: same readable projection Markdown uses, as a
        # sibling field (visible_text), not as content.
        if not (m.content or "").strip():
            visible = export_visible_text(m, journal_entries=entries)
            if visible:
                row["visible_text"] = visible
        exported.append(row)
    return {
        "conversation_id": conversation.id,
        "title": (conversation.title or "").strip() or "未命名对话",
        "exported_at": datetime.now(UTC).isoformat(),
        "messages": exported,
    }
