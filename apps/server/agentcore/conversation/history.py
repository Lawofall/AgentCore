"""History reconstruction and replay.

Loads conversation history from the database for LLM context injection.
Only user/assistant text messages are replayed — tool I/O is not included
to avoid burning tokens on cross-turn accumulated tool output.

Failed assistant turns (empty content + failed status) are folded into a short
system-framed note so the next turn can attribute prior failures correctly
instead of inventing causes. Error prose stays in the note — never as ordinary
assistant content back to the LLM.

Synthetic harvest user rows (``usage.origin=execution_harvest``, or the
``【系统收口】`` prefix on legacy rows) become a short user-role system note.
The CEO still sees extras (draft / 团队成品) when present; the template lead
that says the wave "already finished" does not re-enter later windows as a
bare user utterance. New turns do not mint these rows.

Empty user turns that carry attachment metadata become a short system note
listing names / workspace paths, so later turns still see that files were sent.
No fake user prose; empty user turns without attachments stay dropped.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.conversation.failure_visible import (
    error_message_from_usage,
    failure_category_label,
    is_failed_empty_assistant,
    usage_of,
)
from agentcore.conversation.inline_body import (
    has_inline_markers,
    render_inline_labels,
)
from agentcore.db.repositories import ConversationRepository, MessageRepository

# Re-export detection helpers for existing tests / callers.
_is_failed_empty_assistant = is_failed_empty_assistant
_failure_category_label = failure_category_label

_DETAIL_CLIP = 120
# Attachment-only user notes enter every later turn's window — keep them short.
_ATTACHMENT_NOTE_MAX_ITEMS = 3
_ATTACHMENT_NOTE_CLIP = 160
# Harvest extras (draft / 团队成品) stay in the note; clip so N waves cannot
# re-inflate the window the way the raw template used to.
_HARVEST_EXTRA_CLIP = 800
_HARVEST_USER_PREFIX = "【系统收口】"
_HARVEST_USER_ORIGIN = "execution_harvest"
_HARVEST_NOTE_LEAD = {
    "success": "后台团队本波已收口。",
    "failure": "后台团队本波已结束，其中有失败。",
    "cancelled": "后台团队本波已取消或中断。",
}

# The whole context a chat gets when it has no rolling summary to lean on — the
# safety cap in :func:`load_chat_context`, not a tuning knob. Named because it is
# also the line past which a stalled compaction starts costing real history
# (conversation/context_gap.py), and the two must not drift apart.
FALLBACK_CONTEXT_MAX_MESSAGES = 40


def _failure_detail(msg: Any) -> str | None:
    """Optional short error_message for the note (category remains primary)."""
    raw = error_message_from_usage(usage_of(msg))
    if not raw:
        return None
    text = raw.strip()
    if len(text) > _DETAIL_CLIP:
        return text[: _DETAIL_CLIP - 1] + "…"
    return text


def _failure_note(categories: list[str], details: list[str] | None = None) -> dict:
    """Merge consecutive failed turns into one short assistant-framed system note.

    Assistant role (not bare system) mirrors the compaction summary block — slots
    cleanly between real turns without stacking multiple system messages mid-chat.
    """
    # Preserve order, drop duplicates for a compact label list.
    seen: set[str] = set()
    unique: list[str] = []
    for c in categories:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    n = len(categories)
    cats = "、".join(unique)
    if n == 1:
        body = (
            f"（系统注记：上一轮 AI 调用失败，未产生有效回复。"
            f"失败原因类别：{cats}。请据此如实说明，不要编造其它原因。）"
        )
    else:
        body = (
            f"（系统注记：此前连续 {n} 轮 AI 调用失败，均未产生有效回复。"
            f"失败原因类别：{cats}。请据此如实说明，不要编造其它原因。）"
        )
    if details:
        # One clipped detail line — still a note, not fake assistant prose.
        seen_d: set[str] = set()
        unique_d: list[str] = []
        for d in details:
            if d and d not in seen_d:
                seen_d.add(d)
                unique_d.append(d)
        if unique_d:
            body = body.removesuffix("）") + f" 详情：{'；'.join(unique_d)}。）"
    return {"role": "assistant", "content": body}


def _attachments_of(msg: Any) -> list[Any]:
    raw = getattr(msg, "attachments", None)
    return raw if isinstance(raw, list) else []


def _mentions_of(msg: Any) -> list[Any]:
    raw = getattr(msg, "agent_mentions", None)
    return raw if isinstance(raw, list) else []


def _user_content_for_history(msg: Any) -> str:
    """Past turns: expand pills to labels. Never re-inject file bodies."""
    content = getattr(msg, "content", None) or ""
    if has_inline_markers(content):
        return render_inline_labels(content, _attachments_of(msg), _mentions_of(msg))
    return content


def _attachment_label(att: Any) -> str | None:
    """``name → workspace_path`` (or whichever side is present)."""
    if not isinstance(att, dict):
        return None
    name = att.get("name")
    name = name.strip() if isinstance(name, str) else ""
    path = att.get("workspace_path") or att.get("path")
    path = path.strip() if isinstance(path, str) else ""
    if name and path:
        return f"{name} → {path}"
    return name or path or None


def _user_attachment_note(attachments: list[Any]) -> dict:
    """Keep an empty user turn that carried files — system note, not fake prose.

    User role so compaction's ``_from_first_user`` still sees a user-turn boundary.
    """
    labels: list[str] = []
    for a in attachments:
        label = _attachment_label(a)
        if label:
            labels.append(label)
    shown = labels[:_ATTACHMENT_NOTE_MAX_ITEMS]
    extra = len(labels) - len(shown)
    if shown:
        listed = "；".join(shown)
        if extra > 0:
            listed = f"{listed}；另有 {extra} 个"
        body = f"（系统注记：用户未写文字，仅上传附件：{listed}。）"
    else:
        body = "（系统注记：用户未写文字，仅上传附件。）"
    if len(body) > _ATTACHMENT_NOTE_CLIP:
        body = body[: _ATTACHMENT_NOTE_CLIP - 1] + "…）"
    return {"role": "user", "content": body}


def _is_harvest_user(msg: Any) -> bool:
    """Structured harvest claim — origin first; prefix is legacy hydrate only."""
    if getattr(msg, "role", None) != "user":
        return False
    origin = usage_of(msg).get("origin")
    if origin == _HARVEST_USER_ORIGIN:
        return True
    content = getattr(msg, "content", None) or ""
    return content.startswith(_HARVEST_USER_PREFIX)


def _harvest_kind(msg: Any) -> str:
    kind = usage_of(msg).get("harvest_kind")
    return kind if kind in _HARVEST_NOTE_LEAD else "success"


def _harvest_extras(content: str) -> str:
    """Keep labeled extras after the template lead; drop the lead itself."""
    text = (content or "").strip()
    if text.startswith(_HARVEST_USER_PREFIX):
        parts = text.split("\n\n", 1)
        extra = parts[1].strip() if len(parts) > 1 else ""
    else:
        extra = ""
    if len(extra) > _HARVEST_EXTRA_CLIP:
        extra = extra[: _HARVEST_EXTRA_CLIP - 1] + "…"
    return extra


def _harvest_note(msg: Any) -> dict:
    """User-role system note so ``_from_first_user`` still sees a boundary.

    Not a bare user utterance of the harvest template — same posture as
    attachment-only notes.
    """
    lead = _HARVEST_NOTE_LEAD[_harvest_kind(msg)]
    extra = _harvest_extras(getattr(msg, "content", None) or "")
    body = (
        f"（系统注记：{lead.rstrip('。')}。\n{extra}）" if extra else f"（系统注记：{lead}）"
    )
    return {"role": "user", "content": body}


def _fold_history_messages(messages: list[Any]) -> list[dict]:
    """Fold ORM message rows into ``[{role, content}]``, merging consecutive failures."""
    history: list[dict] = []
    pending_failures: list[str] = []
    pending_details: list[str] = []

    def flush_failures() -> None:
        if pending_failures:
            history.append(_failure_note(pending_failures, pending_details))
            pending_failures.clear()
            pending_details.clear()

    for msg in messages:
        role = getattr(msg, "role", None)
        content = (
            _user_content_for_history(msg)
            if role == "user"
            else (getattr(msg, "content", None) or "")
        )
        atts = _attachments_of(msg) if role == "user" else []
        if role == "user" and content:
            flush_failures()
            if _is_harvest_user(msg):
                history.append(_harvest_note(msg))
            else:
                history.append({"role": "user", "content": content})
        elif role == "user" and atts:
            flush_failures()
            history.append(_user_attachment_note(atts))
        elif role == "assistant" and content:
            flush_failures()
            item: dict[str, Any] = {"role": "assistant", "content": content}
            # 引擎跨回合 hydrate 用；拼 LLMMessage 时只取 role/content，不带入模型窗口。
            ledger = getattr(msg, "evidence_ledger", None)
            if isinstance(ledger, list) and ledger:
                item["evidence_ledger"] = list(ledger)
            history.append(item)
        elif _is_failed_empty_assistant(msg):
            pending_failures.append(_failure_category_label(msg))
            detail = _failure_detail(msg)
            if detail:
                pending_details.append(detail)
        # else: empty non-failed assistant / other roles — skip
    flush_failures()
    return history


async def load_recent_history(
    session: AsyncSession,
    conversation_id: str,
    *,
    max_messages: int = 40,
    fold_failures: bool = False,
    after: datetime | None = None,
) -> list[dict]:
    """Load the MOST RECENT ``max_messages``, in chronological order.

    Returns a list of {role, content} dicts (oldest-first within the window).
    Only user and assistant messages are included.

    Tails the conversation on purpose — a long chat must feed the LLM its
    LATEST turns, not its opening (the earlier ``load_history`` paged the OLDEST
    ``max_messages`` via ``list_by_conversation(offset=0)``, so a >40-message
    conversation silently dropped every recent turn). Shared by two readers:
    the per-turn LLM context (conversation/service.py) and the offline long-term
    memory consolidation window (memory/consolidation.py).

    ``fold_failures``: when True (chat prompt path), consecutive empty failed
    assistant turns become one short system note. When False (memory
    consolidation), empty assistants stay dropped so synthetic notes never
    enter the memory file.

    ``after``: keep only messages strictly newer than this instant, still capped
    at ``max_messages``. The memory consolidation window passes its watermark here
    so each pass summarizes what is genuinely new; the chat prompt path leaves it
    None and gets the plain recent tail.
    """
    repo = MessageRepository(session)
    messages = (
        await repo.list_recent(conversation_id, limit=max_messages)
        if after is None
        else await repo.list_recent_after(conversation_id, after=after, limit=max_messages)
    )
    if fold_failures:
        return _fold_history_messages(messages)
    history = []
    for msg in messages:
        if msg.role in ("user", "assistant") and msg.content:
            body = (
                _user_content_for_history(msg)
                if msg.role == "user"
                else msg.content
            )
            item: dict[str, Any] = {"role": msg.role, "content": body}
            if msg.role == "assistant":
                ledger = getattr(msg, "evidence_ledger", None)
                if isinstance(ledger, list) and ledger:
                    item["evidence_ledger"] = list(ledger)
            history.append(item)
    return history


def _from_first_user(history: list[dict]) -> list[dict]:
    """The window from its first ``user`` item on — the summary-prefixed tail's own cut.

    A compacted window is cut TWICE: ``compaction._select_fold`` floors the fold to a
    user-turn boundary so the un-folded tail starts on ``user``, and then the loader
    re-cuts that tail from the newest side at ``compaction_context_max_messages``. BOTH
    cuts have to respect that boundary — a cap-driven drop (stalled compaction, tail
    outgrown the cap) that eats the tail's leading ``user`` leaves the assistant-role
    summary block adjacent to an assistant turn, which a strict OpenAI-compatible
    backend 400s.

    Same rule as the fold's, walked the other way: forward to the first ``user``. What
    it drops are replies whose prompt the cap already discarded — orphans either way.
    An all-assistant remainder yields the summary alone.
    """
    for index, item in enumerate(history):
        if item.get("role") == "user":
            return history[index:]
    return []


def _summary_block(summary: str) -> dict:
    """The rolling compaction summary as ONE assistant-role history item.

    Assistant (not user) so it slots between the system prompt and the first real
    user turn without two consecutive user messages; framed so the model reads it as
    a system-made recap of earlier context, not the user's words.
    """
    return {
        "role": "assistant",
        "content": (
            "（以下是本次对话早前内容的摘要，由系统自动压缩以控制上下文长度；"
            "需要更早的精确原文时，可基于此摘要继续。）\n\n" + summary.strip()
        ),
    }


async def load_chat_context(
    session: AsyncSession,
    conversation_id: str,
    *,
    max_messages: int = FALLBACK_CONTEXT_MAX_MESSAGES,
) -> list[dict]:
    """The CEO chat window: the rolling compaction summary (when present) prefixed to
    the un-folded recent tail; otherwise just the plain recent window.

    Same ``[{role, content}]`` shape as :func:`load_recent_history`, so the pipeline is
    unchanged — this only swaps WHAT fills the window. When the conversation has a
    summary, the tail is everything strictly newer than the watermark (recent-biased
    and capped, so a stalled compaction degrades by dropping the oldest un-folded tail,
    never the newest — see ``MessageRepository.list_recent_after``), taken from its first
    ``user`` on so that cap-driven drop keeps the near-end aligned (:func:`_from_first_user`).
    The summary rides as the FIRST item (assistant block) right after the system prompt,
    keeping the stable system prefix cached and re-caching only summary+tail when a
    re-compaction changes the summary (执行引擎架构设计 §三 长对话压缩).

    NB: long-term memory consolidation deliberately still reads raw recent messages via
    :func:`load_recent_history` — it reconciles ACTUAL turns into the memory file and
    must not see a synthetic summary block.
    """
    conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    if conv is not None and conv.compaction_summary and conv.compacted_through:
        rows = await MessageRepository(session).list_recent_after(
            conversation_id,
            after=conv.compacted_through,
            limit=settings.compaction_context_max_messages,
        )
        history: list[dict] = [_summary_block(conv.compaction_summary)]
        history.extend(_from_first_user(_fold_history_messages(rows)))
        return history

    return await load_recent_history(
        session, conversation_id, max_messages=max_messages, fold_failures=True
    )


async def load_history_for_turn(
    session: AsyncSession,
    conversation_id: str,
    *,
    before_user_created_at: datetime,
    history_len: int,
) -> list[dict]:
    """Reconstruct the prior-turn history spliced into a turn's LLM window head.

    Mirrors ``load_chat_context(...)[:-1]`` at send time: the journal stores only
    ``history_len``; the caller supplies the tail of messages strictly older than the
    triggering user message. When compaction was active before that user message, the
    synthetic summary block counts toward ``history_len``.
    """
    if history_len <= 0:
        return []

    msg_repo = MessageRepository(session)
    rows, _ = await msg_repo.list_before(
        conversation_id,
        before=before_user_created_at,
        limit=max(history_len * 2, 40),
    )

    items: list[dict] = []
    conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    if (
        conv is not None
        and conv.compaction_summary
        and conv.compacted_through
        and conv.compacted_through < before_user_created_at
    ):
        items.append(_summary_block(conv.compaction_summary))

    items.extend(_fold_history_messages(rows))

    if len(items) > history_len:
        return items[-history_len:]
    return items
