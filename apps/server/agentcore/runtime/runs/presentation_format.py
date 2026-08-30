"""Presentation delivery-format confirmation ledger (dual-gate).

Unique source for the user-selected ``format_id`` after ask_user resume (or a
future full_auto default). Resume wire priority: explicit ``format_id`` →
legitimate ``fN`` in ``selected``; prose note alone never confirms.

Keyed on conversation — delegate hard-gate reads this ledger
(:func:`resolve_format_confirmation` / pptx-vs-md delivery check).
Orthogonal to website ``style_id`` / kickoff delivery questions.

Persistence (与挂起恢复同构):
- Durable fact ``presentation_format_confirmed`` via :func:`record_turn_fact`.
- ``turn_paused.presentation_format`` snapshot at durable pause; resume rehydrates.
- Process-local ``_LEDGER`` is a hot cache only — clear + rehydrate from journal /
  paused must still surface the confirmation.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, ClassVar

from agentcore.runtime.facts import Fact, FactKind, current_fact_log, record_turn_fact

# full_auto narrow default when CEO skips the format card.
# Prefer pptx when code_execute is available; otherwise marp — callers pass prefer_pptx.
DEFAULT_FORMAT_ID = "f_default"
DEFAULT_FORMAT_LABEL_PPTX = "PowerPoint（.pptx）"
DEFAULT_FORMAT_LABEL_MARP = "Marp Markdown 幻灯片"
DEFAULT_FORMAT_LABEL_OUTLINE = "仅讲稿大纲"

# Canonical delivery-form hints for teaching / option labels (ids still minted f0/f1…).
SUGGESTED_FORMAT_LABELS: tuple[str, ...] = (
    "PowerPoint（.pptx）— 真幻灯片文件；有 code_execute 时推荐",
    "Marp Markdown 幻灯片 — 无代码执行时推荐",
    "仅讲稿大纲 — 只要讲稿、不要幻灯片文件",
)

# ``f0`` / ``f_default`` / ``f12`` — ids minted by normalize_format_options or default.
_FORMAT_ID_TOKEN_RE = re.compile(r"\b(f(?:_default|\d+))\b", re.IGNORECASE)

_lock = threading.Lock()


@dataclass(frozen=True)
class FormatConfirmation:
    format_id: str
    label: str
    source: str  # "ask_user" | "full_auto_default"


# conversation_id → FormatConfirmation (hot cache; durable source = journal / paused)
_LEDGER: dict[str, FormatConfirmation] = {}


@dataclass(frozen=True, slots=True)
class PresentationFormatConfirmedFact:
    """Durable structured format pick for presentation delivery gate rehydration."""

    format_id: str
    label: str
    source: str
    conversation_id: str = ""
    kind: ClassVar[FactKind] = FactKind.PRESENTATION_FORMAT_CONFIRMED

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "format_id": self.format_id,
                "label": self.label,
                "source": self.source,
                "conversation_id": self.conversation_id,
            },
            ts=ts,
        )


def presentation_pptx_silent_md_error() -> str:
    return (
        "用户已确认交付形态为 PowerPoint（.pptx），且本回合具备 code_execute："
        "禁止静默改成只交 .md / Marp。"
        "请在 tasks / deliverable.artifacts 中声明 .pptx（如 course.pptx），"
        "由 worker 用 python-pptx 生成真幻灯片；若要改用 Marp，须先经 ask_user 重选形态。"
    )


def presentation_pptx_no_exec_soft_tip() -> str:
    return (
        "[能力提示] 用户已选 .pptx，但本回合无 code_execute："
        "可用 Marp/Slidev .md 或「生成脚本+说明」并明示缺口，"
        "勿假称已交付可直接打开的 PowerPoint。"
    )


# Confirmed format looks like real PowerPoint (not Marp / outline).
_PPTX_FORMAT_RE = re.compile(
    r"(?:\.pptx\b|powerpoint|真\s*(?:幻灯片|PPT)|PowerPoint)",
    re.IGNORECASE,
)
_NON_PPTX_FORMAT_RE = re.compile(
    r"(?:marp|slidev|outline|仅讲稿|讲稿大纲|Markdown\s*幻灯片)",
    re.IGNORECASE,
)

# Task / artifact declarations that downgrade to md-as-slides.
_MD_MARP_DELIVERY_RE = re.compile(
    r"(?:"
    r"\.md\b|marp|slidev|"
    r"markdown\s*(?:幻灯片|slides?)|"
    r"Markdown\s*[（(]\s*(?:Slidev|Marp)"
    r")",
    re.IGNORECASE,
)
_PPTX_DELIVERY_RE = re.compile(
    r"(?:\.pptx\b|python-pptx|powerpoint)",
    re.IGNORECASE,
)


def is_pptx_format_confirmation(conf: FormatConfirmation | None) -> bool:
    """True when the ledgered format is real PowerPoint (.pptx), not Marp/outline."""
    if conf is None:
        return False
    label = (conf.label or "").strip()
    if not label:
        return False
    if _NON_PPTX_FORMAT_RE.search(label) and not _PPTX_FORMAT_RE.search(label):
        return False
    if label == DEFAULT_FORMAT_LABEL_PPTX:
        return True
    return bool(_PPTX_FORMAT_RE.search(label) or re.search(r"\bpptx\b", label, re.I))


def _task_delivery_blob(tasks_raw: list[Any] | None) -> str:
    parts: list[str] = []
    for raw in tasks_raw or []:
        if not isinstance(raw, dict):
            continue
        task = raw.get("task")
        if task:
            parts.append(str(task))
        deliverable = raw.get("deliverable")
        if isinstance(deliverable, dict):
            arts = deliverable.get("artifacts")
            if isinstance(arts, list):
                parts.extend(str(a) for a in arts if a)
        arts_top = raw.get("artifacts")
        if isinstance(arts_top, list):
            parts.extend(str(a) for a in arts_top if a)
    return "\n".join(parts)


def tasks_declare_pptx_delivery(tasks_raw: list[Any] | None) -> bool:
    return bool(_PPTX_DELIVERY_RE.search(_task_delivery_blob(tasks_raw)))


def tasks_declare_md_or_marp_delivery(tasks_raw: list[Any] | None) -> bool:
    return bool(_MD_MARP_DELIVERY_RE.search(_task_delivery_blob(tasks_raw)))


def tasks_silently_downgrade_pptx_to_md(tasks_raw: list[Any] | None) -> bool:
    """True when tasks/artifacts only declare .md/Marp and never .pptx."""
    return tasks_declare_md_or_marp_delivery(tasks_raw) and not tasks_declare_pptx_delivery(
        tasks_raw
    )


def format_confirmation_to_payload(conf: FormatConfirmation) -> dict[str, str]:
    return {
        "format_id": conf.format_id,
        "label": conf.label,
        "source": conf.source,
    }


def format_confirmation_from_payload(
    payload: dict[str, Any] | None,
) -> FormatConfirmation | None:
    if not isinstance(payload, dict):
        return None
    fid = str(payload.get("format_id") or "").strip()
    if not fid:
        return None
    return FormatConfirmation(
        format_id=fid,
        label=str(payload.get("label") or "").strip() or fid,
        source=str(payload.get("source") or "").strip() or "ask_user",
    )


def format_from_journal_entries(
    entries: list[dict[str, Any]] | None,
) -> FormatConfirmation | None:
    """Fold the last ``presentation_format_confirmed`` fact from a journal stream."""
    if not entries:
        return None
    last: FormatConfirmation | None = None
    kind = FactKind.PRESENTATION_FORMAT_CONFIRMED.value
    for entry in entries:
        if (entry.get("kind") or "") != kind:
            continue
        conf = format_confirmation_from_payload(entry.get("payload"))
        if conf is not None:
            last = conf
    return last


def _cache_put(conversation_id: str, conf: FormatConfirmation) -> FormatConfirmation:
    with _lock:
        _LEDGER[conversation_id] = conf
    return conf


def _cache_get(conversation_id: str) -> FormatConfirmation | None:
    with _lock:
        return _LEDGER.get(conversation_id)


def hydrate_format_confirmation(
    conversation_id: str,
    conf: FormatConfirmation,
) -> FormatConfirmation:
    """Fill the hot cache only (no journal append) — used by rehydrate paths."""
    cid = (conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id required")
    return _cache_put(cid, conf)


def rehydrate_format_confirmation(
    conversation_id: str | None,
    *,
    entries: list[dict[str, Any]] | None = None,
    turn_paused_format: dict[str, Any] | None = None,
) -> FormatConfirmation | None:
    """Restore memory from ``turn_paused.presentation_format`` and/or journal facts.

    Priority: turn_paused snapshot → last ``presentation_format_confirmed`` in ``entries``.
    Returns the hydrated confirmation, or ``None`` when neither source has one.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    conf = format_confirmation_from_payload(turn_paused_format)
    if conf is None:
        conf = format_from_journal_entries(entries)
    if conf is None:
        return None
    return hydrate_format_confirmation(cid, conf)


def record_format_confirmation(
    conversation_id: str,
    *,
    format_id: str,
    label: str,
    source: str,
) -> FormatConfirmation:
    """Overwrite the conversation's confirmed format and append a durable journal fact."""
    cid = (conversation_id or "").strip()
    fid = (format_id or "").strip()
    if not cid or not fid:
        raise ValueError("conversation_id and format_id required")
    conf = FormatConfirmation(
        format_id=fid,
        label=(label or "").strip() or fid,
        source=source,
    )
    _cache_put(cid, conf)
    record_turn_fact(
        PresentationFormatConfirmedFact(
            format_id=conf.format_id,
            label=conf.label,
            source=conf.source,
            conversation_id=cid,
        ).to_fact()
    )
    return conf


def get_format_confirmation(conversation_id: str | None) -> FormatConfirmation | None:
    """Hot cache, then ambient fact log (same-turn durable rehydrate without DB)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    hit = _cache_get(cid)
    if hit is not None:
        return hit
    log = current_fact_log.get()
    if log is None:
        return None
    conf = format_from_journal_entries(log.entries())
    if conf is None:
        return None
    return hydrate_format_confirmation(cid, conf)


def clear_format_confirmation(conversation_id: str | None) -> None:
    """Test helper — drop a conversation's hot-cache entry (journal untouched)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return
    with _lock:
        _LEDGER.pop(cid, None)


def ensure_full_auto_default_format(
    conversation_id: str,
    *,
    prefer_pptx: bool = False,
) -> FormatConfirmation:
    """Narrow full_auto exemption: ledger the default if none confirmed yet.

    ``prefer_pptx=True`` when code_execute is available; otherwise marp.
    """
    existing = get_format_confirmation(conversation_id)
    if existing is not None:
        return existing
    return record_format_confirmation(
        conversation_id,
        format_id=DEFAULT_FORMAT_ID,
        label=(
            DEFAULT_FORMAT_LABEL_PPTX if prefer_pptx else DEFAULT_FORMAT_LABEL_MARP
        ),
        source="full_auto_default",
    )


async def load_format_confirmation_from_db(
    conversation_id: str | None,
) -> FormatConfirmation | None:
    """Cold path: scan recent turn journals for ``presentation_format_confirmed``.

    Used when the hot cache and ambient fact log miss (process restart / new turn).
    Hydrates memory on hit. Best-effort — DB gaps return ``None`` (gate fails cleanly).
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    hit = _cache_get(cid)
    if hit is not None:
        return hit
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository
    except Exception:
        return None
    try:
        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            payload = await repo.find_latest_presentation_format(conversation_id=cid)
    except Exception:
        return None
    conf = format_confirmation_from_payload(payload)
    if conf is None:
        return None
    return hydrate_format_confirmation(cid, conf)


async def resolve_format_confirmation(
    conversation_id: str | None,
) -> FormatConfirmation | None:
    """Gate helper: memory / ambient journal → else durable DB scan."""
    conf = get_format_confirmation(conversation_id)
    if conf is not None:
        return conf
    return await load_format_confirmation_from_db(conversation_id)


def snapshot_presentation_format_for_pause(
    journal_entries: list[dict[str, Any]] | None,
    *,
    conversation_id: str | None = None,
) -> dict[str, str] | None:
    """Build ``turn_paused.presentation_format`` from journal fact or hot cache."""
    conf = format_from_journal_entries(journal_entries)
    if conf is None and conversation_id:
        conf = _cache_get((conversation_id or "").strip())
    if conf is None:
        return None
    return format_confirmation_to_payload(conf)


def _lookup_format_option(
    by_id: dict[str, dict[str, Any]],
    format_id: str,
) -> FormatConfirmation | None:
    fid = (format_id or "").strip()
    if not fid:
        return None
    for kid, opt in by_id.items():
        if kid.casefold() == fid.casefold():
            return FormatConfirmation(
                format_id=kid,
                label=str(opt.get("label") or kid),
                source="ask_user",
            )
    return None


def resolve_format_from_resume(
    format_options: list[dict[str, Any]] | None,
    *,
    format_id: str | None = None,
    selected: list[str] | None = None,
    note: str = "",
) -> FormatConfirmation | None:
    """Map structured resume wire onto a format_options entry.

    Priority: explicit ``format_id`` (must ∈ options) → else a legitimate ``fN`` /
    ``f_default`` token in ``selected`` that ∈ options. Prose ``note`` / label
    fuzzy match is **not** a success path.
    """
    opts = [o for o in (format_options or []) if isinstance(o, dict) and o.get("id")]
    if not opts:
        return None
    by_id = {str(o["id"]).strip(): o for o in opts if str(o.get("id") or "").strip()}
    if not by_id:
        return None

    explicit = (format_id or "").strip()
    if explicit:
        hit = _lookup_format_option(by_id, explicit)
        if hit is not None:
            return hit
        # Invalid explicit id → reject (do not fall through to selected / note).
        return None

    for raw in selected or []:
        tok = str(raw or "").strip()
        if not tok or not _FORMAT_ID_TOKEN_RE.fullmatch(tok):
            continue
        hit = _lookup_format_option(by_id, tok)
        if hit is not None:
            return hit

    _ = note
    return None
