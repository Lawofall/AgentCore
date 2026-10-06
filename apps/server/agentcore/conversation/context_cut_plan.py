"""Pure plan for a user context cut (方案切点).

No DB, no LLM. The service in ``context_cut`` loads rows and calls these.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from agentcore.conversation.compact_prompt import (
    attach_identity_ledger,
    strip_identity_ledger,
)

NOTHING_TO_FOLD = "这条前面没有还在上下文里的内容"
TOO_MANY_TO_FOLD = "前面还有太多原文，等自动压缩收一轮后再从这条继续"
NO_LEADING_USER = "这条回复前面没有你的话，不能从这里切开"
BAD_ROLE = "只能从你的话或回复上切开"
STILL_RUNNING = "这场对话还在进行，先等它停下来"
TEAM_OPEN = "团队还没收口，先等这一轮结束"
PAUSED = "这场对话还停在待拍板，先处理完再收起更早的内容"
CHOSEN_RUNNING = "这条回复还在生成，不能收起更早的内容"
PREVIEW_STALE = "预览已失效，请再看一次"
SUMMARY_EMPTY = "没能收成摘要，更早的内容还在"
SUMMARY_UNAVAILABLE = "现在收不成摘要，更早的内容还在。可以过一会儿再试"
PROSE_EMPTY = "摘要是空的，写上要留下的内容再继续"
SUMMARY_TOO_LONG = "摘要太长，收短后再继续"
NOTHING_TO_UNDO = "没有可撤回的切点"
UNDO_MOVED = "之后又收过一轮，这次不能撤回"


class ContextCutError(Exception):
    """A cut the user asked for that must not change the window."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def resolve_verbatim_start(chain: Sequence[Any], message_id: str) -> Any:
    """First message that stays verbatim.

    A user row starts the tail. An assistant row starts at the nearest
    preceding user row, so the assistant-role summary is not followed by
    another assistant. ``chain`` is chronological and includes the chosen row.
    """
    idx = next((i for i, row in enumerate(chain) if getattr(row, "id", None) == message_id), -1)
    if idx < 0:
        raise ContextCutError(NOTHING_TO_FOLD)
    chosen = chain[idx]
    role = getattr(chosen, "role", None)
    if role == "user":
        return chosen
    if role != "assistant":
        raise ContextCutError(BAD_ROLE)
    for earlier in range(idx - 1, -1, -1):
        if getattr(chain[earlier], "role", None) == "user":
            return chain[earlier]
    raise ContextCutError(NO_LEADING_USER)


def take_fold(candidates: Sequence[Any], *, max_fold: int, more: bool) -> list[Any]:
    """Rows already limited to (watermark, verbatim start).

    ``more`` means the query stopped at ``max_fold`` with older-or-equal rows
    still waiting — one pass cannot retire them, so refuse instead of cutting
    a prefix the user did not ask for.
    """
    if more or len(candidates) > max_fold:
        raise ContextCutError(TOO_MANY_TO_FOLD)
    if not candidates:
        raise ContextCutError(NOTHING_TO_FOLD)
    return list(candidates)


def fold_digest(watermark: datetime | None, fold: Sequence[Any]) -> str:
    """Bind a preview to the watermark and the exact folded ids."""
    stamp = _as_utc(watermark).isoformat() if watermark is not None else ""
    ids = ",".join(str(getattr(row, "id", "")) for row in fold)
    return hashlib.sha256(f"{stamp}|{ids}".encode()).hexdigest()


def accept_cut_prose(prose: str, ledger: str, *, max_chars: int) -> str:
    """Prose the user confirmed, plus this fold's program-owned ledger.

    Text from the identity fence onward is dropped so the client cannot
    replace the ledger. Empty prose and prose over ``max_chars`` are refused.
    """
    body = strip_identity_ledger(prose).strip()
    if not body:
        raise ContextCutError(PROSE_EMPTY)
    if len(body) > max_chars:
        raise ContextCutError(SUMMARY_TOO_LONG)
    return attach_identity_ledger(body, ledger)


def undo_snapshot(conv: Any, *, cut_watermark: datetime) -> dict[str, Any]:
    """Prior compaction state, plus the watermark this cut is about to write."""
    through = getattr(conv, "compacted_through", None)
    return {
        "summary": getattr(conv, "compaction_summary", None),
        "compacted_through": through.isoformat() if isinstance(through, datetime) else None,
        "input_tokens": getattr(conv, "compaction_input_tokens", None),
        "cut_watermark": _as_utc(cut_watermark).isoformat(),
    }


def as_undo_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


def context_cut_undoable(raw: Any, compacted_through: datetime | None) -> bool:
    """True when the stored snapshot still matches the live watermark."""
    payload = as_undo_dict(raw)
    if payload is None or compacted_through is None:
        return False
    cut = _parse_dt(payload.get("cut_watermark"))
    if cut is None:
        return False
    return _as_utc(cut) == _as_utc(compacted_through)


def parse_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_dt(value)
    if parsed is None:
        raise ContextCutError(NOTHING_TO_UNDO)
    return parsed


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
