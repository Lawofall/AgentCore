"""Shared helpers for conversation turn orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import re

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.gate import BackgroundLlmResult, run_background_llm
from agentcore.core.errors import LLMAuthError
from agentcore.core.log_context import get_log_value, log_context
from agentcore.core.logging import get_logger
from agentcore.core.text import clip_preview
from agentcore.db.base import async_session_factory
from agentcore.db.models import Conversation
from agentcore.db.repositories import ConversationRepository, UserRepository
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.factory import build_provider
from agentcore.llm.model_selection import select_turn_model as resolve_turn_model
from agentcore.llm.model_selection import select_turn_profiles
from agentcore.llm.profiles import TurnProfiles
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.memory import (
    TITLE_MAX_CHARS,
    ChatMessage,
    LLMTitleGenerator,
    TitleInput,
    TitleResult,
)
from agentcore.runtime.events import EventSink, title_generated
from agentcore.workspace.locate import LocalBinding

logger = get_logger(__name__)

# Fire-and-forget early title mint (cloud SSE). In-process like schedule_compaction_if_due:
# ``_inflight`` dedupes a burst; ``_tasks`` holds refs so a pass is not GC'd mid-flight.
_title_inflight: set[str] = set()
_title_tasks: set[asyncio.Task] = set()


def log_cost_recorded(conversation_id: str, message_id: str | None, cost_runs: list[dict]) -> None:
    """Emit ``cost.recorded`` after a turn's ledger rows persist successfully.

    ``by_role`` breaks spend by structural role (captain / member / vision / …)
    so ``log_stats`` and timeline triage can split team burn without joining DB.
    """
    total_nano = sum(int(r.get("cost_total_nano", 0) or 0) for r in cost_runs)
    models = sorted({str(r.get("model", "?")) for r in cost_runs if r.get("model")})
    by_role: dict[str, dict[str, int]] = {}
    for row in cost_runs:
        role = str(row.get("role") or "?")
        bucket = by_role.setdefault(role, {"runs": 0, "total_nano": 0, "input": 0, "output": 0})
        bucket["runs"] += 1
        bucket["total_nano"] += int(row.get("cost_total_nano", 0) or 0)
        tokens = row.get("tokens") or {}
        bucket["input"] += int(tokens.get("input", 0) or 0)
        bucket["output"] += int(tokens.get("output", 0) or 0)
    logger.info(
        "cost.recorded",
        conversation_id=conversation_id,
        message_id=message_id,
        runs=len(cost_runs),
        total_nano=total_nano,
        total_usd=round(total_nano / 1e9, 6),
        models=models,
        by_role=by_role,
    )


# Markdown / list decoration a pasted task card opens with ("## 1. **目标**"),
# dropped so the degraded label reads as a topic rather than as punctuation.
# An ASCII enumerator must be followed by space so「1.5 亿的预算」keeps its number;
# 「1、」 needs no such guard (a Chinese enumeration comma is never a decimal point).
_TITLE_DECORATION_RE = re.compile(r"^(?:[\s>#*\-+•·]|\d+[.)]\s|\d+、)*")
# Where a clause ends. Cutting here leaves a phrase; cutting at the raw character
# cap leaves half a word — which is what turned a task card into a sidebar label
# that read as a torn-off sentence.
_TITLE_BOUNDARY_CHARS = "。．.！!？?；;，,、 "
# A boundary this early would trade half a sentence for two words; below it the
# blunt character cut carries more meaning.
_TITLE_MIN_CHARS = TITLE_MAX_CHARS // 2
_TITLE_TRAILING = " 　。．.！!？?；;，,、:：-—_*#"


def fallback_title(user_message: str) -> str:
    """A readable short label derived from the first user message.

    Title mint failures (429, timeout, spent allowance) **do not** write this
    string to ``conversations.title`` — that column stays empty so a later turn
    can retry. The string is still used for ``chat.title_degraded.title_chars``
    and as the shape-signal for ``auto_cloud_desk_name``. Slicing the first 30
    characters off a pasted task card produced a label torn out of mid-sentence;
    instead take the message's first meaningful line (its topic, minus markdown
    decoration) and, if that still overflows, end it at the last clause boundary
    that fits.

    Anything left behind — later lines as much as a mid-line cut — keeps the
    trailing ``…``. That marker is not decoration: ``auto_cloud_desk_name`` reads
    it to tell「这是用户原话的一截」from a name, and dropping it on a task card
    whose first line happens to be short would quietly turn a user's own words
    (身份证 / 电话 / 住址 and all) into a real directory segment.
    """
    line, dropped = _first_meaningful_line(user_message)
    title = re.sub(r"\s+", " ", line).strip()
    if len(title) > TITLE_MAX_CHARS:
        head = title[:TITLE_MAX_CHARS]
        boundary = max(head.rfind(ch) for ch in _TITLE_BOUNDARY_CHARS)
        title = head[:boundary] if boundary >= _TITLE_MIN_CHARS else head
        dropped = True
    title = title.rstrip(_TITLE_TRAILING) or title
    return f"{title}…" if title and dropped else title


def _first_meaningful_line(user_message: str) -> tuple[str, bool]:
    """``(line, more_followed)`` — the first line that still says something.

    ``more_followed`` reports whether later lines carried content, so the caller
    can mark the label as a fragment. Stripped decoration does not count as
    dropped content: "## 季度复盘" is a whole topic, not half of one.

    Falls back to the whole message when every line is pure decoration (a rule, a
    bare bullet), so the caller never gets "" for a message that plainly has text.
    """
    lines = user_message.splitlines()
    for index, line in enumerate(lines):
        candidate = _TITLE_DECORATION_RE.sub("", line).strip()
        if candidate:
            return candidate, any(rest.strip() for rest in lines[index + 1 :])
    return user_message.strip(), False


# Turn-log message previews: enough of the user prompt / assistant reply to triage
# 「问了什么 / 答得如何」straight from a log line (no DB round-trip), while staying a
# bounded snippet — never the full 正文 (logging.mdc 铁律). ~200 chars ≈ a first paragraph.
LOG_PREVIEW_CHARS = 200


def preview(text: str, *, limit: int = LOG_PREVIEW_CHARS) -> str:
    """Single-line, length-capped preview of message text for a log field."""
    return clip_preview(text, limit)


async def resolve_local_binding(session: AsyncSession, conv: Conversation) -> LocalBinding | None:
    """Resolve a turn's local-mode binding (项目即工作区).

    - **Project chat** (``folder_id`` set): inherit the project's ``local_root_id`` /
      ``local_subpath``. Cloud projects (both NULL) → ``None``.
    - **裸聊**: ``local_root_id`` (explicit) or ``local_container_root_id`` (desktop
      local-first intent). Empty ``local_subpath`` resolves to
      ``conversations/<conversation_id>`` under the container (per-对话隔离；懒建).
      Cloud SSE turns honor both so sidecar-written files stay visible when the
      turn falls back from sidecar to cloud.
    """
    from agentcore.conversation.scratch import (
        bare_chat_local_subpath,
        resolve_conversation_local_binding,
    )
    from agentcore.db.repositories import FolderRepository

    if conv.folder_id:
        folder = await FolderRepository(session).get_by_id_unscoped(conv.folder_id)
        if not folder:
            return None
        return resolve_conversation_local_binding(
            local_root_id=folder.local_root_id,
            local_subpath=folder.local_subpath,
            label=folder.name or "workspace",
        )

    root_id = conv.local_root_id or conv.local_container_root_id
    subpath = conv.local_subpath or (bare_chat_local_subpath(conv.id) if root_id else None)
    return resolve_conversation_local_binding(
        local_root_id=root_id,
        local_subpath=subpath,
        label="workspace",
    )


async def resolve_folder_local_binding(
    session: AsyncSession, folder_id: str
) -> LocalBinding | None:
    """Local binding for a registered Folder (landing / target desk), or None if cloud."""
    from agentcore.conversation.scratch import resolve_conversation_local_binding
    from agentcore.db.repositories import FolderRepository

    cleaned = folder_id.strip() if isinstance(folder_id, str) else ""
    if not cleaned:
        return None
    folder = await FolderRepository(session).get_by_id_unscoped(cleaned)
    if not folder:
        return None
    return resolve_conversation_local_binding(
        local_root_id=folder.local_root_id,
        local_subpath=folder.local_subpath,
        label=folder.name or "workspace",
    )


def resolve_turn_file_workspace(
    *,
    birth_folder_id: str | None,
    auto_desk_folder_id: str | None,
) -> tuple[str | None, str | None]:
    """Pick (file_folder_id, auto_desk) for CEO file tools without changing affiliation.

    Birth ``folder_id`` wins. Bare chat with a persisted landing desk → that Folder.
    Returns ``(workspace_folder_id, auto_desk_for_context)``.
    """
    birth = birth_folder_id.strip() if isinstance(birth_folder_id, str) else None
    if birth:
        return birth, None
    auto = (
        auto_desk_folder_id.strip()
        if isinstance(auto_desk_folder_id, str) and auto_desk_folder_id.strip()
        else None
    )
    return auto, auto


async def generate_title(
    *,
    provider: LLMProvider,
    conversation_id: str,
    user_message: str,
    assistant_reply: str,
    model: str | None = None,
) -> TitleResult:
    """Best-effort title via the fast model; degrades with ``fallback_title``.

    ``LLMTitleGenerator`` already retries once on an empty model body (timeout
    does not retry). An empty result after that — or any non-auth call-level
    error — returns ``fallback_title`` plus ``degraded_reason``. Persist callers
    must not write a degraded result to ``conversations.title``.

    ``LLMAuthError`` is **re-raised** so ``run_background_llm`` can try user BYOK.
    """
    fallback = fallback_title(user_message)
    if not user_message.strip():
        return TitleResult(title=fallback)

    messages: list[ChatMessage] = [{"role": "user", "content": user_message}]
    if assistant_reply.strip():
        messages.append({"role": "assistant", "content": assistant_reply})

    try:
        result = await LLMTitleGenerator(provider, model=model).generate(
            TitleInput(conversation_id=conversation_id, messages=messages)
        )
        if result.title:
            return result
        return TitleResult(title=fallback, degraded_reason="empty_model_title")
    except LLMAuthError:
        # Must surface so ``run_background_llm`` can try user BYOK once.
        raise
    except Exception as e:
        from agentcore.llm.background_failure import classify_background_llm_failure

        reason = classify_background_llm_failure(e)
        logger.warning(
            "chat.title_failed",
            conversation_id=conversation_id,
            error=str(e),
            reason=reason,
        )
        return TitleResult(title=fallback, degraded_reason=reason)


def log_title_degraded(
    *,
    conversation_id: str,
    reason: str,
    title_chars: int,
    persisted: bool,
) -> None:
    """Mint miss: ``persisted`` distinguishes a fallback write from leaving ``title`` empty."""
    logger.info(
        "chat.title_degraded",
        conversation_id=conversation_id,
        reason=reason,
        title_chars=title_chars,
        persisted=persisted,
    )


async def _read_conversation_title(conversation_id: str) -> str | None:
    """Return a non-empty title string, or ``None`` when missing / blank."""
    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    if conv is None:
        return None
    title = str(conv.title).strip() if conv.title else ""
    return title or None


async def _mint_title_core(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    sink: EventSink | None = None,
) -> str | None:
    """Shared early-title mint: user-message-only LLM → persist only a real title → optional SSE.

    Never raises. Skips the LLM when the conversation already has a title (user rename
    race). A degraded ``TitleResult`` (rate limit / timeout / empty body / gate skip)
    is logged and **not** written, so a later empty-title turn can retry. Emit is
    best-effort — a closed sink must not undo a successful DB write.
    Does **not** manage ``_title_inflight`` (caller owns dedupe).
    """
    try:
        existing = await _read_conversation_title(conversation_id)
        if existing is not None:
            return existing
        # Row gone → nothing to mint.
        async with async_session_factory() as session:
            conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
        if conv is None:
            return None

        async def _runner(credentials: LLMCredentials) -> TitleResult:
            model = resolve_turn_model(credentials)
            provider = build_provider(credentials, purpose="platform_internal")
            try:
                # First user message only — do not wait for assistant reply.
                return await generate_title(
                    provider=provider,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    assistant_reply="",
                    model=model,
                )
            finally:
                await provider.close()

        result = await run_background_llm(user_id, purpose="title", runner=_runner)
        # Gate refusal (no platform/BYOK key, platform allowance spent, auth
        # failed both sides) is the same as a model miss: log and leave ``title``
        # empty so a later turn can retry. Do not consult the refusal's cooldown
        # here — this layer does not own retries.
        if isinstance(result, BackgroundLlmResult):
            minted_title = result.value.title
            degraded_reason = result.value.degraded_reason
        else:
            minted_title = fallback_title(user_message)
            degraded_reason = f"gate_{result.reason.value}"

        if degraded_reason:
            log_title_degraded(
                conversation_id=conversation_id,
                reason=degraded_reason,
                title_chars=len(minted_title or ""),
                persisted=False,
            )
            return None
        if not minted_title:
            return None

        async with async_session_factory() as session:
            updated = await ConversationRepository(session).update_title_if_empty(
                conversation_id, minted_title
            )
        if updated is None:
            # Race: user renamed / concurrent mint already wrote.
            return await _read_conversation_title(conversation_id)

        if sink is not None:
            with contextlib.suppress(Exception):
                sink.emit(title_generated(minted_title, conversation_id=conversation_id))
        return minted_title
    except Exception as e:
        from agentcore.llm.background_failure import classify_background_llm_failure

        logger.warning(
            "chat.title_schedule_failed",
            conversation_id=conversation_id,
            error=str(e),
            reason=classify_background_llm_failure(e),
        )
        return None


async def mint_title_if_empty(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    sink: EventSink | None = None,
) -> str | None:
    """Await-able early title mint (REST / shared with cloud schedule).

    Same LLM / ``run_background_llm`` / ``update_title_if_empty`` / ``_title_inflight``
    dedupe as the cloud SSE path. ``assistant_reply`` is always empty. Optional
    ``sink`` emits ``title_generated`` after a successful conditional write.

    Returns the conversation title after the call (existing or freshly minted), or
    ``None`` when the row is missing / mint failed (degraded results are not written).
    """
    existing = await _read_conversation_title(conversation_id)
    if existing is not None:
        return existing

    if conversation_id in _title_inflight:
        # Another path (cloud schedule or concurrent REST) is minting — wait, then
        # return whatever landed (may still be empty if that mint failed).
        for _ in range(200):  # ~10s @ 50ms
            if conversation_id not in _title_inflight:
                break
            await asyncio.sleep(0.05)
        return await _read_conversation_title(conversation_id)

    _title_inflight.add(conversation_id)
    try:
        return await _mint_title_core(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            sink=sink,
        )
    finally:
        _title_inflight.discard(conversation_id)


async def _mint_title_background(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    sink: EventSink,
) -> None:
    """Cloud early-title runner (schedule already armed ``_title_inflight``)."""
    try:
        await _mint_title_core(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=user_message,
            sink=sink,
        )
    finally:
        _title_inflight.discard(conversation_id)


def schedule_title_generation(
    *,
    conversation_id: str,
    user_id: str,
    user_message: str,
    sink: EventSink,
    trace_id: str | None = None,
) -> None:
    """Fire-and-forget early title mint for a cloud SSE turn (sync schedule only).

    Call after the first user message is persisted (``turn_saved``), in parallel with
    the turn pipeline. No-op when a mint for this conversation is already in flight.
    """
    if conversation_id in _title_inflight:
        return
    _title_inflight.add(conversation_id)
    # Same-turn correlation: inherit caller-bound trace_id or an explicit mint from
    # stream_chat so title llm.call shares the turn's trace (contextvars copy into the task).
    bound_trace = trace_id or get_log_value("trace_id")
    with log_context(
        conversation_id=conversation_id,
        user_id=user_id,
        trace_id=bound_trace,
    ):
        task = asyncio.ensure_future(
            _mint_title_background(
                conversation_id=conversation_id,
                user_id=user_id,
                user_message=user_message,
                sink=sink,
            )
        )
    _title_tasks.add(task)
    task.add_done_callback(_title_tasks.discard)


async def resolve_turn_profiles(
    session: AsyncSession,
    conv: Conversation,
    user_id: str,
    credentials: LLMCredentials | None = None,
) -> TurnProfiles:
    """Thin wrapper — strategy lives in :func:`model_selection.select_turn_profiles`."""
    return await select_turn_profiles(session, conv, user_id, credentials)


# Legacy name used by conversation service exports.
resolve_profile_set = resolve_turn_profiles


async def resolve_autonomy_policy(session: AsyncSession, user_id: str):
    """User-global *default recipe* AutonomyPolicy (seeds new conversations only).

    Runtime gates must use :func:`resolve_permission_axes` / the conversation
    column — not this. Kept for settings API and create-time seeding.
    """
    from agentcore.core.types import AutonomyPolicy

    user = await UserRepository(session).get_by_id(user_id)
    raw = (user.autonomy_policy if user else None) or AutonomyPolicy.LESS_INTERRUPT.value
    try:
        return AutonomyPolicy(raw)
    except ValueError:
        return AutonomyPolicy.LESS_INTERRUPT


def parse_permission_axes(raw: dict | None):
    """Coerce a stored / wire permission_axes mapping; unknown → less_interrupt defaults."""
    from agentcore.core.types import PermissionAxes

    return PermissionAxes.from_mapping(raw)


async def resolve_permission_axes(session: AsyncSession, conversation_id: str):
    """This turn's permission axes — conversation column is the single source of truth."""
    from agentcore.db.repositories import ConversationRepository

    conv = await ConversationRepository(session).get_by_id_unscoped(conversation_id)
    return parse_permission_axes(conv.permission_axes if conv else None)


async def default_permission_axes_for_user(session: AsyncSession, user_id: str):
    """Map the user's autonomy recipe → PermissionAxes for a new conversation."""
    from agentcore.core.types import recipe_to_axes

    return recipe_to_axes(await resolve_autonomy_policy(session, user_id))
