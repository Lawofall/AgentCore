"""Conversation transcript export for cross-session log tools and ``@`` attachments.

Used by Worker ``search_conversations`` / ``read_conversation`` and by
server-side conversation-attachment reads (``_build_attachment_context``).
Deliberately separate from:

- ``history.py`` — shallow user/assistant bodies for the LLM working window
- ``export.py`` — user-facing shallow Q&A download

Default read is ``dialogue`` (user/assistant visible text). The process layer
(tools / debate / evidence / thinking) is opt-in via ``focus=process``.
Pages are message-index cursors (``m:N``), not a char-slice of a dump.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentcore.conversation.failure_visible import export_visible_text
from agentcore.db.models import Conversation, Message
from agentcore.runtime.journal import KIND_TURN_END

# Pathological page ceiling for ``read_conversation``. Callers must set
# ``ToolResult.output_limit`` ≥ returned chunk length — never lean on the
# default 4000 head+tail truncate. Normal dialogue pages sit far below this;
# it is not a content-shaping budget.
MAX_CHUNK_CHARS = 100_000

FOCUS_DIALOGUE = "dialogue"
FOCUS_PROCESS = "process"
FOCUS_VALUES = frozenset({FOCUS_DIALOGUE, FOCUS_PROCESS})
DEFAULT_FOCUS = FOCUS_DIALOGUE

# Snippet length for search rows (match-centered when query hits).
SEARCH_SNIPPET_CHARS = 240
_SNIPPET_RADIUS = 80

_EXPORTED_ROLES = frozenset({"user", "assistant"})

# Journal kinds we deliberately skip (noise / cost / followups).
_SKIP_KINDS = frozenset(
    {
        "followups",
        "cost",
        "feedback",
        "citations",  # rendered from message.citations instead
        "evidence_ledger",  # rendered from message.evidence_ledger instead
    }
)

_TOOL_KINDS = frozenset({"tool_use_start", "tool_use_end", "tool_progress"})
_DEBATE_PREFIX = "debate_"


@dataclass(frozen=True)
class LogChunk:
    """One message-aligned page of a transcript (cursor-continuable)."""

    title: str
    conversation_id: str
    transcript: str
    truncated: bool
    next_cursor: str | None
    started_at: str | None
    ended_at: str | None
    message_count: int
    message_offset: int
    message_end: int
    focus: str
    query: str | None
    query_hit: bool
    # Wire-compat with older cloud clients: page-local char stats, not a dump offset.
    char_offset: int
    total_chars: int


@dataclass(frozen=True)
class SearchHit:
    """One search-row snippet, aligned with ``read_conversation(query=)`` seek."""

    snippet: str
    message_index: int | None
    message_id: str | None
    role: str | None


def _fmt_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _jsonish(value: Any, *, limit: int = 2000) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(value)
    return _clip(raw, limit)


def _render_journal_entry(entry: Mapping[str, Any]) -> list[str]:
    """Render one journal fact into markdown lines (may be empty = skip)."""
    kind = str(entry.get("kind") or "")
    if not kind or kind in _SKIP_KINDS:
        return []
    payload = entry.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}

    if kind == "tool_use_start":
        name = str(payload.get("tool_name") or payload.get("name") or "tool")
        args = payload.get("arguments")
        lines = [f"#### Tool: {name}", ""]
        if args is not None:
            lines.append("```")
            lines.append(_jsonish(args))
            lines.append("```")
            lines.append("")
        return lines

    if kind == "tool_use_end":
        name = str(payload.get("tool_name") or payload.get("name") or "tool")
        success = payload.get("success")
        status = "ok" if success is True else ("fail" if success is False else "")
        head = f"#### Tool result: {name}"
        if status:
            head += f" ({status})"
        lines = [head, ""]
        output = payload.get("result")
        if output is None:
            output = payload.get("output")
        if output is not None:
            body = output if isinstance(output, str) else _jsonish(output, limit=8000)
            lines.append(_clip(str(body), 8000))
            lines.append("")
        err = payload.get("error")
        if err:
            lines.append(f"*error:* {_clip(str(err), 500)}")
            lines.append("")
        return lines

    if kind == "tool_progress":
        phase = payload.get("phase") or payload.get("message") or ""
        if not phase:
            return []
        return [f"- tool progress: {_clip(str(phase), 240)}", ""]

    if kind.startswith(_DEBATE_PREFIX) or kind in {
        "debate_result",
        "debate_round",
        "debate_round_started",
        "debate_pretrial_started",
        "debate_pretrial_orders",
        "debate_pretrial_completed",
    }:
        lines = ["#### Debate", ""]
        summary = (
            payload.get("summary")
            or payload.get("opening")
            or payload.get("verdict")
            or payload.get("text")
            or payload.get("message")
        )
        lines.append(f"- `{kind}`")
        if summary:
            lines.append(_clip(str(summary), 2000))
        else:
            # Compact payload peek — avoid dumping huge debate state.
            peek_keys = ("round", "side", "side_key", "status", "form")
            bits = [
                f"{k}={payload.get(k)}" for k in peek_keys if payload.get(k) is not None
            ]
            if bits:
                lines.append("- " + "; ".join(bits))
        lines.append("")
        return lines

    if kind == KIND_TURN_END:
        fr = payload.get("finish_reason")
        err = payload.get("error")
        if not fr and not err:
            return []
        note = "finish_reason=" + str(fr) if fr else "turn ended"
        if err:
            note += f"; error={_clip(str(err), 300)}"
        return [f"*system:* {note}", ""]

    if kind.startswith("process_") or kind.startswith("run_process_"):
        label = kind.removeprefix("process_").removeprefix("run_process_")
        text = payload.get("text") or payload.get("summary") or payload.get("kind") or label
        run_id = payload.get("run_id")
        prefix = f"[{run_id}] " if run_id else ""
        return [f"- {prefix}{_clip(str(text), 400)}", ""]

    # Collaboration / delegate short bullets (run_plan, run_started, …).
    if kind in {
        "run_plan",
        "run_started",
        "run_completed",
        "run_failed",
        "graph_append",
        "plan_revised",
        "round_boundary",
    }:
        summary = (
            payload.get("task_summary")
            or payload.get("summary")
            or payload.get("role")
            or kind
        )
        return [f"- `{kind}`: {_clip(str(summary), 400)}", ""]

    return []


def _render_message_block(
    msg: Message,
    journal: Sequence[Mapping[str, Any]] | None,
) -> str:
    """One user/assistant message as markdown (journal process before assistant body)."""
    role = msg.role or ""
    if role not in _EXPORTED_ROLES:
        return ""
    lines: list[str] = []
    if role == "user":
        lines.append("### User")
        lines.append("")
        body = (msg.content or "").strip()
        if body:
            lines.append(body)
            lines.append("")
        # Attachment names only (no binary dump).
        for att in msg.attachments or []:
            if not isinstance(att, Mapping):
                continue
            name = att.get("name") or att.get("path") or "attachment"
            lines.append(f"- attachment: {name}")
        if msg.attachments:
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # assistant
    lines.append("### Assistant")
    lines.append("")
    if journal:
        for entry in journal:
            lines.extend(_render_journal_entry(entry))

    reasoning = (msg.reasoning_content or "").strip()
    if reasoning:
        lines.append("#### Thinking")
        lines.append("")
        lines.append(_clip(reasoning, 12_000))
        lines.append("")

    content = (msg.content or "").strip()
    if content:
        lines.append(content)
        lines.append("")
    else:
        # Pure failure: content stays empty; surface structured error so the log
        # is not a blank Assistant heading.
        fail_text = export_visible_text(msg, journal_entries=journal)
        if fail_text:
            lines.append(fail_text)
            lines.append("")

    evidence = msg.evidence_ledger if isinstance(msg.evidence_ledger, list) else []
    if evidence:
        lines.append("#### Evidence")
        lines.append("")
        for item in evidence[:40]:
            if not isinstance(item, Mapping):
                continue
            eid = item.get("id") or ""
            title = item.get("title") or item.get("url") or ""
            lines.append(f"- {eid} {title}".strip())
        lines.append("")

    citations = msg.citations if isinstance(msg.citations, list) else []
    if citations:
        lines.append("#### Citations")
        lines.append("")
        for item in citations[:40]:
            if not isinstance(item, Mapping):
                continue
            title = item.get("title") or ""
            url = item.get("url") or ""
            lines.append(f"- [{title}]({url})" if url else f"- {title}")
        lines.append("")

    usage = msg.usage if isinstance(msg.usage, dict) else None
    if usage and usage.get("status") in {"error", "cancelled", "interrupted", "failed"}:
        lines.append(f"*system:* turn status={usage.get('status')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_dialogue_message(msg: Message) -> str:
    """User/assistant visible text only — no tools, thinking, evidence, citations."""
    role = msg.role or ""
    if role == "user":
        return _render_message_block(msg, None)
    if role != "assistant":
        return ""
    lines: list[str] = ["### Assistant", ""]
    content = (msg.content or "").strip()
    if content:
        lines.append(content)
        lines.append("")
    else:
        fail_text = export_visible_text(msg)
        if fail_text:
            lines.append(fail_text)
            lines.append("")
    usage = msg.usage if isinstance(msg.usage, dict) else None
    if usage and usage.get("status") in {"error", "cancelled", "interrupted", "failed"}:
        lines.append(f"*system:* turn status={usage.get('status')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _visible_messages(messages: Sequence[Message]) -> list[Message]:
    return [m for m in messages if (m.role or "") in _EXPORTED_ROLES]


def normalize_focus(value: str | None) -> str | None:
    """Return a known focus or ``None`` if the caller sent a junk value."""
    raw = (value or DEFAULT_FOCUS).strip() or DEFAULT_FOCUS
    return raw if raw in FOCUS_VALUES else None


def render_conversation_log(
    conversation: Conversation,
    messages: Sequence[Message],
    journal_map: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    focus: str = FOCUS_PROCESS,
) -> str:
    """Full markdown transcript for one conversation (no paging).

    ``focus=process`` is the forensic dump (tests / explicit opt-in). Tool and
    ``@`` attachment default paging goes through :func:`page_conversation`.
    """
    journal_map = journal_map or {}
    title = (conversation.title or "").strip() or "未命名对话"
    parts: list[str] = [
        f"# {title}",
        "",
        f"- conversation_id: `{conversation.id}`",
        f"- created_at: {_fmt_ts(conversation.created_at) or '—'}",
        f"- updated_at: {_fmt_ts(conversation.updated_at) or '—'}",
        "",
    ]
    for msg in messages:
        if (msg.role or "") not in _EXPORTED_ROLES:
            continue
        if focus == FOCUS_DIALOGUE:
            block = _render_dialogue_message(msg)
        else:
            journal = journal_map.get(msg.id) if msg.role == "assistant" else None
            block = _render_message_block(msg, journal)
        if block.strip():
            parts.append(block)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def encode_cursor(message_index: int) -> str:
    return f"m:{max(0, int(message_index))}"


def decode_cursor(cursor: str | None) -> int:
    """0-based message start index. Legacy ``c:`` char cursors restart at 0."""
    if not cursor:
        return 0
    raw = str(cursor).strip()
    if raw.startswith("c:"):
        return 0
    if raw.startswith("m:"):
        raw = raw[2:]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _message_body(msg: Message) -> str:
    return (export_visible_text(msg) or "").strip()


def find_query_start(messages: Sequence[Message], query: str) -> int | None:
    """Oldest visible message whose body contains ``query``, or ``None``."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for i, msg in enumerate(_visible_messages(messages)):
        body = _message_body(msg)
        if body and q in body.lower():
            return i
    return None


def page_conversation(
    conversation: Conversation,
    messages: Sequence[Message],
    journal_map: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    focus: str = DEFAULT_FOCUS,
    cursor: str | None = None,
    query: str | None = None,
    max_chars: int | None = None,
) -> LogChunk:
    """Message-aligned page. ``query`` seeks on first call (no cursor)."""
    limit = max_chars if max_chars is not None else MAX_CHUNK_CHARS
    limit = max(1, min(int(limit), MAX_CHUNK_CHARS))
    focus_n = normalize_focus(focus) or DEFAULT_FOCUS
    journal_map = journal_map or {}
    visible = _visible_messages(messages)
    q = (query or "").strip() or None
    start = decode_cursor(cursor)
    query_hit = False
    if q and not cursor:
        hit = find_query_start(visible, q)
        if hit is not None:
            start = hit
            query_hit = True
    if start > len(visible):
        start = len(visible)

    transcripts: list[str] = []
    end = start
    clipped = False
    used = 0
    for i in range(start, len(visible)):
        msg = visible[i]
        if focus_n == FOCUS_DIALOGUE:
            block = _render_dialogue_message(msg)
        else:
            journal = journal_map.get(msg.id) if msg.role == "assistant" else None
            block = _render_message_block(msg, journal)
        if not block.strip():
            end = i + 1
            continue
        block = block.rstrip() + "\n"
        if not transcripts and len(block) > limit:
            keep = max(1, limit - 1)
            transcripts.append(block[:keep] + "…\n")
            end = i + 1
            clipped = True
            break
        extra = len(block) + (1 if transcripts else 0)
        if transcripts and used + extra > limit:
            break
        transcripts.append(block)
        used += extra
        end = i + 1

    transcript = "\n".join(t.rstrip("\n") for t in transcripts)
    if transcript:
        transcript += "\n"
    more = end < len(visible)
    truncated = more or clipped
    next_cursor = encode_cursor(end) if more else None
    started = _fmt_ts(visible[0].created_at) if visible else None
    ended = _fmt_ts(visible[-1].created_at) if visible else None
    return LogChunk(
        title=(conversation.title or "").strip() or "未命名对话",
        conversation_id=conversation.id,
        transcript=transcript,
        truncated=truncated,
        next_cursor=next_cursor,
        started_at=started,
        ended_at=ended,
        message_count=len(visible),
        message_offset=start,
        message_end=end,
        focus=focus_n,
        query=q,
        query_hit=query_hit,
        char_offset=0,
        total_chars=len(transcript),
    )


def _match_centered_snippet(content: str, query: str) -> str:
    q = (query or "").strip()
    if not q:
        return _clip(content, SEARCH_SNIPPET_CHARS)
    idx = content.lower().find(q.lower())
    if idx < 0:
        return _clip(content, SEARCH_SNIPPET_CHARS)
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(content), idx + len(q) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def search_hit_from_messages(
    messages: Sequence[Message], query: str
) -> SearchHit | None:
    """Oldest body match when ``query`` is set; else latest readable text.

    Oldest-first matches ``read_conversation(query=)`` seek so the search row's
    「第 N 条」 is where a subsequent read starts.
    """
    q = (query or "").strip()
    visible = _visible_messages(messages)

    def _body(msg: Message) -> str:
        return _message_body(msg)

    if q:
        for i, msg in enumerate(visible):
            body = _body(msg)
            if body and q.lower() in body.lower():
                return SearchHit(
                    snippet=_match_centered_snippet(body, q),
                    message_index=i,
                    message_id=getattr(msg, "id", None),
                    role=msg.role,
                )
    for i in range(len(visible) - 1, -1, -1):
        msg = visible[i]
        if msg.role == "user" and (msg.content or "").strip():
            return SearchHit(
                snippet=_clip(msg.content or "", SEARCH_SNIPPET_CHARS),
                message_index=None if q else i,
                message_id=getattr(msg, "id", None),
                role="user",
            )
    for i in range(len(visible) - 1, -1, -1):
        msg = visible[i]
        body = _body(msg)
        if body:
            return SearchHit(
                snippet=_clip(body, SEARCH_SNIPPET_CHARS),
                message_index=None if q else i,
                message_id=getattr(msg, "id", None),
                role=msg.role,
            )
    return None


def search_snippet_from_messages(messages: Sequence[Message], query: str) -> str | None:
    """Pick a short content snippet matching ``query`` (or latest readable text)."""
    hit = search_hit_from_messages(messages, query)
    return hit.snippet if hit else None

