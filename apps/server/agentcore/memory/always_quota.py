"""Write-side always-entry quota (闸在写侧，读侧全量).

Meters injectable always-on rule bodies by character count. User edits of an
existing always entry may exceed the cap (allow + warning); AI create/merge that
would grow past the cap is refused and may push one ``memory_updates`` card per
pending fingerprint (same state + same refused entries → one card; user fix /
content change resets).

A full pool must never read as「AI 从此记不住东西」(审计 CTX-A2): the card names
every entry this pass could not write AND the biggest entries currently holding the
pool, so the user can see what to trim. Nothing is silently evicted — the write is
refused, the existing entries stay.

See docs/03-AI核心/Agent记忆与知识系统.md「配额：闸在写侧，读侧全量」.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.db.models import Document
from agentcore.db.repositories import DocumentRepository, MemoryUpdateRepository
from agentcore.documents.frontmatter import strip_entry_frontmatter
from agentcore.memory.maintenance import (
    MemoryUpdateItem,
    _memory_file_label,
    _memory_leaf_target,
)
from agentcore.memory.store import memory_version

logger = get_logger(__name__)

# Set by consolidation / AI write paths that own a conversation_id for quota cards.
memory_write_conversation_id: ContextVar[str | None] = ContextVar(
    "memory_write_conversation_id", default=None
)

Writer = Literal["user", "ai"]

QUOTA_CARD_KIND = "quota"
_USER_OVER_WARNING = (
    "常驻太多了。这次改动已保存；请删减或改为按需，以免 AI 记不下新的。"
)
_USER_CREATE_DENIED = (
    "常驻太多了。请先删减已有常驻或改为按需，再新建或改成常驻。"
)
_AI_DENIED_MESSAGE = (
    "常驻太多，AI 暂时记不下新的。请删减或改为按需后再试。"
)
_CARD_SUMMARY = (
    "常驻太多，AI 暂时记不下新的：以下 {denied} 条没能写进常驻，"
    "现有条目一条也没被删。删减或改为按需后即可继续。"
)
# How many current always entries the card names as「谁占着配额」. Enough to act on,
# short enough that the card stays a card.
_HOLDER_ROWS = 5


@dataclass(frozen=True)
class AlwaysUsage:
    """Write-side always-pool usage (percentage + absolute chars)."""

    used_chars: int
    max_chars: int
    fingerprint: str = ""
    # Split of ``used_chars`` for the two-segment meter (global + this project).
    # By construction ``used_chars == global_chars + project_chars``; global context
    # leaves ``project_chars`` at 0.
    global_chars: int = 0
    project_chars: int = 0

    @property
    def percent(self) -> float:
        if self.max_chars <= 0:
            return 0.0
        return round(min(100.0, 100.0 * self.used_chars / self.max_chars), 1)

    @property
    def over_limit(self) -> bool:
        return self.max_chars > 0 and self.used_chars > self.max_chars


@dataclass(frozen=True)
class AlwaysQuotaDecision:
    """Gate outcome for one prospective always write."""

    allowed: bool
    warning: str | None = None
    usage: AlwaysUsage | None = None
    message: str | None = None  # set when denied


class AlwaysQuotaExceededError(Exception):
    """AI write refused because the always pool would grow past the cap.

    Carries WHICH entry was refused (``file`` is the store-relative memory path or the
    document name, ``scope`` the folder id / None for global) so the card can name it
    instead of only reporting that the pool is full.
    """

    def __init__(
        self,
        usage: AlwaysUsage,
        message: str | None = None,
        *,
        file: str = "",
        scope: str | None = None,
        attempted_chars: int = 0,
    ) -> None:
        self.usage = usage
        self.file = file
        self.scope = scope
        self.attempted_chars = attempted_chars
        self.message = message or _AI_DENIED_MESSAGE
        super().__init__(self.message)

    @property
    def denial(self) -> DeniedAlwaysWrite:
        return DeniedAlwaysWrite(
            file=self.file,
            scope=self.scope,
            attempted_chars=self.attempted_chars,
            usage=self.usage,
        )


@dataclass(frozen=True)
class DeniedAlwaysWrite:
    """One always write the quota refused — a card row, not a log line."""

    file: str
    scope: str | None
    attempted_chars: int
    usage: AlwaysUsage


# Bound around a whole consolidation pass: denials accumulate instead of pushing a card
# each, so one full pool produces ONE card naming everything it blocked. Unbound (single
# tool / API write) keeps the immediate one-denial card.
always_quota_denials: ContextVar[list[DeniedAlwaysWrite] | None] = ContextVar(
    "always_quota_denials", default=None
)


@contextmanager
def collect_always_quota_denials() -> Iterator[list[DeniedAlwaysWrite]]:
    """Collect this pass's refused always writes; caller flushes one card at the end."""
    denials: list[DeniedAlwaysWrite] = []
    token = always_quota_denials.set(denials)
    try:
        yield denials
    finally:
        always_quota_denials.reset(token)


def always_entry_chars(content: str) -> int:
    """Chars that count toward the always pool (frontmatter-stripped body).

    Unclosed / uninjectable frontmatter → 0 (matches read-side skip).
    """
    stripped = strip_entry_frontmatter(content)
    if stripped is None:
        return 0
    return len(stripped)


def always_max_chars() -> int:
    return int(settings.memory_always_max_chars)


def _fingerprint(docs: list[Document], *, used: int, max_chars: int) -> str:
    parts = [f"{d.id}:{memory_version(d.content)}" for d in sorted(docs, key=lambda x: x.id)]
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"{digest}:{used}:{max_chars}"


async def _always_docs_for_scope(
    repo: DocumentRepository, user_id: str, scope: str | None
) -> list[Document]:
    """Always-on rule docs of one scope (user + AI-maintained).

    One ``list_injectable_rules`` call per scope (``ai_maintained=None`` merges both
    authorships). Callers that need global vs project meters still invoke this once
    per scope — that split cannot be collapsed into a single query.
    """
    return await repo.list_injectable_rules(user_id, scope, ai_maintained=None)


async def list_always_quota_docs(
    repo: DocumentRepository, user_id: str, *, folder_id: str | None
) -> list[Document]:
    """Always-on rule docs in the injection context (global + optional project)."""
    scopes: list[str | None] = [None] if folder_id is None else [None, folder_id]
    out: list[Document] = []
    for scope in scopes:
        out.extend(await _always_docs_for_scope(repo, user_id, scope))
    return out


async def measure_always_usage(
    repo: DocumentRepository, user_id: str, *, folder_id: str | None = None
) -> AlwaysUsage:
    """Current always-pool usage for the injection context of ``folder_id``."""
    global_docs = await _always_docs_for_scope(repo, user_id, None)
    global_chars = sum(always_entry_chars(d.content) for d in global_docs)
    project_docs: list[Document] = []
    project_chars = 0
    if folder_id is not None:
        project_docs = await _always_docs_for_scope(repo, user_id, folder_id)
        project_chars = sum(always_entry_chars(d.content) for d in project_docs)
    docs = [*global_docs, *project_docs]
    used = global_chars + project_chars
    max_chars = always_max_chars()
    return AlwaysUsage(
        used_chars=used,
        max_chars=max_chars,
        fingerprint=_fingerprint(docs, used=used, max_chars=max_chars),
        global_chars=global_chars,
        project_chars=project_chars,
    )


def project_usage_after(
    docs: list[Document],
    *,
    exclude_id: str | None,
    new_chars: int,
    new_is_always: bool,
) -> AlwaysUsage:
    """Usage if ``exclude_id`` is replaced by ``new_chars`` (0 / non-always = drop)."""
    kept = [d for d in docs if exclude_id is None or d.id != exclude_id]
    used = sum(always_entry_chars(d.content) for d in kept)
    if new_is_always:
        used += max(0, new_chars)
    max_chars = always_max_chars()
    # Fingerprint of the *current* set (pending-state identity before the write).
    current_chars = sum(always_entry_chars(d.content) for d in docs)
    return AlwaysUsage(
        used_chars=used,
        max_chars=max_chars,
        fingerprint=_fingerprint(docs, used=current_chars, max_chars=max_chars),
    )


def evaluate_always_write(
    *,
    writer: Writer,
    editing_existing_always: bool,
    current_used: int,
    projected: AlwaysUsage,
) -> AlwaysQuotaDecision:
    """Apply who-is-writing rules to a projected always-pool usage."""
    max_chars = projected.max_chars
    if max_chars <= 0:
        return AlwaysQuotaDecision(allowed=True, usage=projected)

    if projected.used_chars <= max_chars:
        return AlwaysQuotaDecision(allowed=True, usage=projected)

    # Over limit.
    if writer == "user" and editing_existing_always:
        return AlwaysQuotaDecision(
            allowed=True,
            warning=_USER_OVER_WARNING,
            usage=projected,
        )

    # Refuse net growth past the cap; shrink / same-size while already over is fine.
    # Writer-agnostic: denying a write that adds nothing stops no growth, and it blocks
    # the remedy itself — an empty new entry is how content moves out of a bloated
    # always entry into an on_demand one.
    if projected.used_chars <= current_used:
        return AlwaysQuotaDecision(allowed=True, usage=projected)

    msg = _USER_CREATE_DENIED if writer == "user" else _AI_DENIED_MESSAGE
    return AlwaysQuotaDecision(allowed=False, usage=projected, message=msg)


async def check_always_write(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    writer: Writer,
    editing_existing_always: bool,
    exclude_id: str | None,
    new_content: str,
    new_is_always: bool,
) -> AlwaysQuotaDecision:
    """Measure + decide for one prospective write against the always pool."""
    docs = await list_always_quota_docs(repo, user_id, folder_id=folder_id)
    current_used = sum(always_entry_chars(d.content) for d in docs)
    new_chars = always_entry_chars(new_content) if new_is_always else 0
    projected = project_usage_after(
        docs,
        exclude_id=exclude_id,
        new_chars=new_chars,
        new_is_always=new_is_always,
    )
    return evaluate_always_write(
        writer=writer,
        editing_existing_always=editing_existing_always,
        current_used=current_used,
        projected=projected,
    )


def _card_fingerprint(row_items: list | None, summary: str | None) -> str | None:
    if row_items:
        for it in row_items:
            if isinstance(it, dict) and it.get("action") == "quota":
                content = it.get("content")
                if isinstance(content, str) and content:
                    return content
    if summary and "fp:" in summary:
        # fallback — not used by current writer
        return summary.rsplit("fp:", 1)[-1].strip() or None
    return None


def _denial_card_fingerprint(
    usage: AlwaysUsage, denials: Sequence[DeniedAlwaysWrite]
) -> str:
    """Pending-state identity: the pool state PLUS which entries it refused.

    Pool state alone would swallow the second card when the same full pool blocks a
    *different* entry — exactly the item-level visibility this card exists for.
    """
    keys = sorted(f"{d.scope or ''}/{d.file}" for d in denials)
    return f"{usage.fingerprint}#{','.join(keys)}" if keys else usage.fingerprint


def _denied_rows(denials: Sequence[DeniedAlwaysWrite]) -> list[MemoryUpdateItem]:
    """One row per refused write: which entry, which layer, how big it was."""
    rows: list[MemoryUpdateItem] = []
    for d in denials:
        if not d.file:
            continue
        rows.append(
            MemoryUpdateItem(
                action="quota_denied",
                file=_memory_file_label(d.file),
                section="",
                scope="project" if d.scope else "global",
                content=f"这次的更新没能写入常驻（{d.attempted_chars} 字符）",
                target=_memory_leaf_target(d.file, d.scope),
                project_id=d.scope,
            )
        )
    return rows


async def _holder_rows(
    repo: DocumentRepository, user_id: str, *, folder_id: str | None
) -> list[MemoryUpdateItem]:
    """The biggest current always entries — the「为什么满了」half of the card."""
    docs = await list_always_quota_docs(repo, user_id, folder_id=folder_id)
    sized = [(always_entry_chars(d.content), d) for d in docs]
    sized = [pair for pair in sized if pair[0] > 0]
    sized.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [
        MemoryUpdateItem(
            action="quota_holder",
            file=_memory_file_label(doc.name),
            section="",
            scope="project" if doc.folder_id else "global",
            content=f"占用 {chars} 字符",
            target=_memory_leaf_target(doc.name, doc.folder_id),
            project_id=doc.folder_id,
        )
        for chars, doc in sized[:_HOLDER_ROWS]
    ]


async def record_always_quota_card_once(
    session: AsyncSession,
    *,
    user_id: str,
    conversation_id: str,
    usage: AlwaysUsage,
    denials: Sequence[DeniedAlwaysWrite] = (),
):
    """Persist a quota card, or return ``None`` when the same pending state repeats.

    The card is item-level: a hidden fingerprint row (dedup key), one row per entry this
    pass could not write, and the biggest entries currently holding the pool.
    """
    from agentcore.db.models import MemoryUpdateRow

    repo = MemoryUpdateRepository(session)
    rows = await repo.list_for_conversation(conversation_id, limit=50)
    latest = next((r for r in reversed(rows) if r.kind == QUOTA_CARD_KIND), None)
    fingerprint = _denial_card_fingerprint(usage, denials)
    if latest is not None and _card_fingerprint(latest.items, latest.summary) == fingerprint:
        logger.info(
            "memory.always_quota_card_suppressed",
            conversation_id=conversation_id,
            fingerprint=fingerprint,
        )
        return None

    denied_rows = _denied_rows(denials)
    summary = _CARD_SUMMARY.format(denied=len(denied_rows))
    items = [
        MemoryUpdateItem(
            action="quota",
            file="",
            section="",
            scope="global",
            content=fingerprint,
            target="",
            project_id=None,
        ),
        *denied_rows,
        *await _holder_rows(
            DocumentRepository(session),
            user_id,
            folder_id=next((d.scope for d in denials if d.scope), None),
        ),
    ]
    row: MemoryUpdateRow = await repo.record(
        conversation_id=conversation_id,
        user_id=user_id,
        items=[asdict(item) for item in items],
        kind=QUOTA_CARD_KIND,
        summary=summary,
    )
    logger.info(
        "memory.always_quota_card",
        conversation_id=conversation_id,
        used=usage.used_chars,
        max=usage.max_chars,
        denied=len(denied_rows),
    )
    return row


async def notify_always_quota_exceeded(user_id: str, exc: AlwaysQuotaExceededError) -> None:
    """Route one refused AI write: collect it for the pass, or push its card now."""
    pending = always_quota_denials.get()
    if pending is not None:
        pending.append(exc.denial)
        return
    await push_always_quota_card(user_id, exc.usage, [exc.denial])


async def push_always_quota_card(
    user_id: str,
    usage: AlwaysUsage,
    denials: Sequence[DeniedAlwaysWrite],
) -> None:
    """Best-effort ``memory_updates`` card naming everything the full pool blocked."""
    import contextlib

    conversation_id = memory_write_conversation_id.get()
    if not conversation_id:
        return
    from agentcore.db.base import async_session_factory
    from agentcore.messaging.hub import default_chat_hub

    try:
        async with async_session_factory() as session:
            row = await record_always_quota_card_once(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                usage=usage,
                denials=denials,
            )
            if row is None:
                return
            update_payload = {
                "id": row.id,
                "conversation_id": conversation_id,
                "created_at": row.created_at.isoformat(),
                "kind": row.kind,
                "summary": row.summary,
                "items": row.items,
                # Quota cards describe the pool, not a message window — always null;
                # sent anyway so every memory_updated payload has the same shape.
                "anchor_at": None,
            }
        with contextlib.suppress(Exception):
            await default_chat_hub().publish(
                [user_id],
                {
                    "type": "memory_updated",
                    "conversation_id": conversation_id,
                    "kind": QUOTA_CARD_KIND,
                    "update": update_payload,
                },
            )
    except Exception as e:  # noqa: BLE001 - card is best-effort
        logger.warning(
            "memory.always_quota_card_failed",
            user_id=user_id,
            error=str(e),
        )
