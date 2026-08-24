"""Agent / automation delivery-format confirmation ledger (dual-gate · mirror presentation).

Unique source for the user-selected ``format_id`` after ask_user resume (or a
full_auto default). Resume wire priority: explicit ``format_id`` → legitimate
``fN`` in ``selected``; prose note alone never confirms.

Keyed on conversation — delegate hard-gate reads this ledger
(:func:`resolve_delivery_confirmation`). Orthogonal to website ``style_id`` /
presentation ``format_id`` (same wire, separate ledger by kickoff intent).

Persistence (与挂起恢复同构):
- Durable fact ``automation_delivery_confirmed`` via :func:`record_turn_fact`.
- ``turn_paused.automation_delivery`` snapshot at durable pause; resume rehydrates.
- Process-local ``_LEDGER`` is a hot cache only — clear + rehydrate from journal /
  paused must still surface the confirmation.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from agentcore.runtime.facts import Fact, FactKind, current_fact_log, record_turn_fact
from agentcore.runtime.runs.presentation_format import (
    FormatConfirmation,
    resolve_format_from_resume,
)

# full_auto narrow default when CEO skips the format card.
DEFAULT_FORMAT_ID = "f_default"
DEFAULT_FORMAT_LABEL_RUNNABLE = "可运行自动化"
DEFAULT_FORMAT_LABEL_CONSOLE = "控制台原型"
DEFAULT_FORMAT_LABEL_PLAN = "仅方案"

DeliveryKind = Literal["runnable", "console", "plan"]

# Canonical delivery-form hints for teaching / option labels (ids still minted f0/f1…).
SUGGESTED_FORMAT_LABELS: tuple[str, ...] = (
    "可运行自动化 — 真实可调度的 Agent/工作流（有执行环境时按环境能力交付；无则如实降级）",
    "控制台原型 — 工具台 / 运营后台 UI 原型",
    "仅方案 — 方案文档 / 架构说明",
)

_RUNNABLE_LABEL_RE = re.compile(
    r"(?:可运行\s*自动化|真实可调度|runnable\s*automation|automation\s*agent)",
    re.IGNORECASE,
)
_CONSOLE_LABEL_RE = re.compile(
    r"(?:控制台\s*原型|工具台|运营后台|console\s*prototype|toolshed)",
    re.IGNORECASE,
)
_PLAN_LABEL_RE = re.compile(
    r"(?:仅\s*方案|方案文档|架构说明|plan\s*only|outline\s*only)",
    re.IGNORECASE,
)

_lock = threading.Lock()


@dataclass(frozen=True)
class DeliveryConfirmation:
    format_id: str
    label: str
    source: str  # "ask_user" | "full_auto_default"


# conversation_id → DeliveryConfirmation (hot cache; durable source = journal / paused)
_LEDGER: dict[str, DeliveryConfirmation] = {}


@dataclass(frozen=True, slots=True)
class AutomationDeliveryConfirmedFact:
    """Durable structured format pick for automation delivery gate rehydration."""

    format_id: str
    label: str
    source: str
    conversation_id: str = ""
    kind: ClassVar[FactKind] = FactKind.AUTOMATION_DELIVERY_CONFIRMED

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


def automation_toolshed_rejected_message() -> str:
    return (
        "当前记账交付形态与所选控制台原型气质不符："
        "可运行自动化请自由组队 / `build_feature`；仅方案请手写 tasks 交方案文档。"
        "若要做控制台原型，请先经 ask_user 重选「控制台原型」。"
    )


def automation_website_rejected_message() -> str:
    return (
        "当前记账交付形态为「仅方案」：请手写 tasks 交方案文档，"
        "或经 ask_user 重选形态。"
    )


def automation_runnable_no_exec_soft_tip() -> str:
    return (
        "[能力提示] 用户已选可运行自动化，但本回合执行面有限："
        "请如实降级交付（脚本+说明 / 可本地跑的草案），勿假称已部署可调度的线上 Agent。"
    )


def classify_delivery_kind(conf: DeliveryConfirmation | None) -> DeliveryKind | None:
    """Map ledger label → runnable | console | plan (None if unknown)."""
    if conf is None:
        return None
    label = (conf.label or "").strip()
    if not label:
        return None
    if label == DEFAULT_FORMAT_LABEL_RUNNABLE or (
        _RUNNABLE_LABEL_RE.search(label) and not _CONSOLE_LABEL_RE.search(label)
    ):
        return "runnable"
    if label == DEFAULT_FORMAT_LABEL_CONSOLE or _CONSOLE_LABEL_RE.search(label):
        return "console"
    if label == DEFAULT_FORMAT_LABEL_PLAN or _PLAN_LABEL_RE.search(label):
        return "plan"
    # Default full_auto id without matched label keywords → runnable.
    if conf.format_id == DEFAULT_FORMAT_ID and conf.source == "full_auto_default":
        return "runnable"
    return None


def is_runnable_delivery(conf: DeliveryConfirmation | None) -> bool:
    return classify_delivery_kind(conf) == "runnable"


def is_console_prototype_delivery(conf: DeliveryConfirmation | None) -> bool:
    return classify_delivery_kind(conf) == "console"


def is_plan_only_delivery(conf: DeliveryConfirmation | None) -> bool:
    return classify_delivery_kind(conf) == "plan"


def format_options_look_like_automation(
    format_options: list[dict[str, Any]] | None,
) -> bool:
    """True when option labels are the Agent/自动化三档 (not pptx/marp)."""
    labels = [
        str(o.get("label") or "")
        for o in (format_options or [])
        if isinstance(o, dict)
    ]
    if not labels:
        return False
    blob = " ".join(labels)
    hits = sum(
        1
        for pat in (_RUNNABLE_LABEL_RE, _CONSOLE_LABEL_RE, _PLAN_LABEL_RE)
        if pat.search(blob)
    )
    return hits >= 2


def delivery_confirmation_to_payload(conf: DeliveryConfirmation) -> dict[str, str]:
    return {
        "format_id": conf.format_id,
        "label": conf.label,
        "source": conf.source,
    }


def delivery_confirmation_from_payload(
    payload: dict[str, Any] | None,
) -> DeliveryConfirmation | None:
    if not isinstance(payload, dict):
        return None
    fid = str(payload.get("format_id") or "").strip()
    if not fid:
        return None
    return DeliveryConfirmation(
        format_id=fid,
        label=str(payload.get("label") or "").strip() or fid,
        source=str(payload.get("source") or "").strip() or "ask_user",
    )


def delivery_from_journal_entries(
    entries: list[dict[str, Any]] | None,
) -> DeliveryConfirmation | None:
    """Fold the last ``automation_delivery_confirmed`` fact from a journal stream."""
    if not entries:
        return None
    last: DeliveryConfirmation | None = None
    kind = FactKind.AUTOMATION_DELIVERY_CONFIRMED.value
    for entry in entries:
        if (entry.get("kind") or "") != kind:
            continue
        conf = delivery_confirmation_from_payload(entry.get("payload"))
        if conf is not None:
            last = conf
    return last


def _cache_put(conversation_id: str, conf: DeliveryConfirmation) -> DeliveryConfirmation:
    with _lock:
        _LEDGER[conversation_id] = conf
    return conf


def _cache_get(conversation_id: str) -> DeliveryConfirmation | None:
    with _lock:
        return _LEDGER.get(conversation_id)


def hydrate_delivery_confirmation(
    conversation_id: str,
    conf: DeliveryConfirmation,
) -> DeliveryConfirmation:
    """Fill the hot cache only (no journal append) — used by rehydrate paths."""
    cid = (conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id required")
    return _cache_put(cid, conf)


def rehydrate_delivery_confirmation(
    conversation_id: str | None,
    *,
    entries: list[dict[str, Any]] | None = None,
    turn_paused_delivery: dict[str, Any] | None = None,
) -> DeliveryConfirmation | None:
    """Restore memory from ``turn_paused.automation_delivery`` and/or journal facts.

    Priority: turn_paused snapshot → last ``automation_delivery_confirmed`` in ``entries``.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    conf = delivery_confirmation_from_payload(turn_paused_delivery)
    if conf is None:
        conf = delivery_from_journal_entries(entries)
    if conf is None:
        return None
    return hydrate_delivery_confirmation(cid, conf)


def record_delivery_confirmation(
    conversation_id: str,
    *,
    format_id: str,
    label: str,
    source: str,
) -> DeliveryConfirmation:
    """Overwrite the conversation's confirmed delivery and append a durable journal fact."""
    cid = (conversation_id or "").strip()
    fid = (format_id or "").strip()
    if not cid or not fid:
        raise ValueError("conversation_id and format_id required")
    conf = DeliveryConfirmation(
        format_id=fid,
        label=(label or "").strip() or fid,
        source=source,
    )
    _cache_put(cid, conf)
    record_turn_fact(
        AutomationDeliveryConfirmedFact(
            format_id=conf.format_id,
            label=conf.label,
            source=conf.source,
            conversation_id=cid,
        ).to_fact()
    )
    return conf


def get_delivery_confirmation(conversation_id: str | None) -> DeliveryConfirmation | None:
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
    conf = delivery_from_journal_entries(log.entries())
    if conf is None:
        return None
    return hydrate_delivery_confirmation(cid, conf)


def clear_delivery_confirmation(conversation_id: str | None) -> None:
    """Test helper — drop a conversation's hot-cache entry (journal untouched)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return
    with _lock:
        _LEDGER.pop(cid, None)


def ensure_full_auto_default_delivery(
    conversation_id: str,
) -> DeliveryConfirmation:
    """Narrow full_auto exemption: ledger 可运行自动化 if none confirmed yet."""
    existing = get_delivery_confirmation(conversation_id)
    if existing is not None:
        return existing
    return record_delivery_confirmation(
        conversation_id,
        format_id=DEFAULT_FORMAT_ID,
        label=DEFAULT_FORMAT_LABEL_RUNNABLE,
        source="full_auto_default",
    )


async def load_delivery_confirmation_from_db(
    conversation_id: str | None,
) -> DeliveryConfirmation | None:
    """Cold path: scan recent turn journals for ``automation_delivery_confirmed``."""
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
            payload = await repo.find_latest_automation_delivery(conversation_id=cid)
    except Exception:
        return None
    conf = delivery_confirmation_from_payload(payload)
    if conf is None:
        return None
    return hydrate_delivery_confirmation(cid, conf)


async def resolve_delivery_confirmation(
    conversation_id: str | None,
) -> DeliveryConfirmation | None:
    """Gate helper: memory / ambient journal → else durable DB scan."""
    conf = get_delivery_confirmation(conversation_id)
    if conf is not None:
        return conf
    return await load_delivery_confirmation_from_db(conversation_id)


def snapshot_automation_delivery_for_pause(
    journal_entries: list[dict[str, Any]] | None,
    *,
    conversation_id: str | None = None,
) -> dict[str, str] | None:
    """Build ``turn_paused.automation_delivery`` from journal fact or hot cache."""
    conf = delivery_from_journal_entries(journal_entries)
    if conf is None and conversation_id:
        conf = _cache_get((conversation_id or "").strip())
    if conf is None:
        return None
    return delivery_confirmation_to_payload(conf)


def resolve_delivery_from_resume(
    format_options: list[dict[str, Any]] | None,
    *,
    format_id: str | None = None,
    selected: list[str] | None = None,
    note: str = "",
) -> DeliveryConfirmation | None:
    """Map structured resume wire onto a format_options entry (reuse presentation wire)."""
    conf: FormatConfirmation | None = resolve_format_from_resume(
        format_options,
        format_id=format_id,
        selected=selected,
        note=note,
    )
    if conf is None:
        return None
    return DeliveryConfirmation(
        format_id=conf.format_id,
        label=conf.label,
        source=conf.source,
    )
