"""阶段推进卡（批 B）：点卡起幕仍可用；新调研不再因 motion_card 自动登记。

旧卡 resolve 起新回合开辩或回灌 research_first。
失效语义（2026-07-19 修订）：用户发新消息不立即 orphan；回合收尾时若 CEO
既未调 debate 也未起 MLR，才落 ``interaction_orphaned``。pending 卡存在时
CEO 调 debate = 口头开赛消费该卡（同点卡授权路径）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.runtime.debate.types import DebateForm, RoundPolicy
from agentcore.runtime.events import stage_card_required
from agentcore.runtime.kickoff.research_first import research_first_tool_result
from agentcore.tools.builtin.motion_card import parse_motion_card

logger = get_logger(__name__)

STAGE_CARD_DECISIONS = frozenset({"start_debate", "research_first"})

_HOST_TRIPLE_KEYS = ("host_execution_id", "synthesizer_run_id", "host_message_id")

# 本回合是否已调 debate / 起调研组队（收尾 orphan 判定）。
_turn_keeps_stage_card: ContextVar[bool] = ContextVar(
    "stage_card_turn_keeps", default=False
)


def mark_turn_keeps_stage_card() -> None:
    """CEO called debate or started a team this turn — do not orphan pending cards."""
    _turn_keeps_stage_card.set(True)


def clear_turn_keeps_stage_card() -> None:
    """STOP / 开辩失败 / 调度失败：清 keep，允许回合收尾 orphan。"""
    _turn_keeps_stage_card.set(False)


def turn_keeps_stage_card() -> bool:
    return bool(_turn_keeps_stage_card.get())


def reset_stage_card_turn_flags() -> None:
    """Call at turn entry so prior-turn flags never leak."""
    _turn_keeps_stage_card.set(False)


def default_rounds_for_form(form: str, *, thorough: bool = True) -> tuple[bool, int]:
    """Return (thorough, max_rounds) display defaults for the stage card."""
    try:
        debate_form = DebateForm(form)
    except ValueError:
        debate_form = DebateForm.DEBATE
    policy = RoundPolicy.for_form(debate_form, thorough=thorough)
    return policy.thorough, policy.max_rounds


def host_triple_from_journal(
    entries: Sequence[Mapping[str, Any]] | None,
    *,
    host_message_id: str,
) -> dict[str, str] | None:
    """Stamp host_execution_id / synthesizer_run_id / host_message_id from MLR journal."""
    mid = (host_message_id or "").strip()
    if not entries or not mid:
        return None
    from agentcore.runtime.kickoff.debate_host import (
        synthesizer_completed,
        synthesizer_run_id,
    )

    execution_id: str | None = None
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind != "run_plan":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("plan_type") or "") != "multi_agent":
            continue
        eid = str(payload.get("execution_id") or "").strip()
        if eid:
            execution_id = eid
    anchor = synthesizer_run_id(entries)
    if not execution_id or not anchor:
        return None
    if not synthesizer_completed(entries, anchor):
        return None
    return {
        "host_execution_id": execution_id,
        "synthesizer_run_id": anchor,
        "host_message_id": mid,
    }


def copy_host_triple(
    source: Mapping[str, Any] | None, target: dict[str, Any]
) -> dict[str, Any]:
    """Copy host stamp keys from ``source`` onto ``target`` (in-place + return)."""
    if not isinstance(source, Mapping):
        return target
    for key in _HOST_TRIPLE_KEYS:
        val = str(source.get(key) or "").strip()
        if val:
            target[key] = val
    return target


async def resolve_host_attach_from_card(
    card: Mapping[str, Any] | None,
    *,
    append_message_id: str | None = None,
) -> Any | None:
    """Async: card triple → ``DebateHostAttach`` when host graph still valid.

    Validates journal still has the stamped ``execution_id`` + completed synthesizer.
    On failure returns ``None`` so callers fall back to ``resolve_debate_host_attach``
    (禁止硬挂坏 mid)。
    """
    if not isinstance(card, Mapping):
        return None
    eid = str(card.get("host_execution_id") or "").strip()
    mid = str(card.get("host_message_id") or "").strip()
    anchor = str(card.get("synthesizer_run_id") or "").strip()
    if not (eid and mid and anchor):
        return None
    from agentcore.runtime.delegate.graph_append import load_host_journal_entries
    from agentcore.runtime.kickoff.debate_host import (
        DebateHostAttach,
        next_act_id,
        synthesizer_completed,
    )

    entries = await load_host_journal_entries(mid)
    if not entries:
        logger.info(
            "stage_card.host_attach_invalid",
            reason="host_journal_missing",
            host_message_id=mid,
        )
        return None
    live_eid: str | None = None
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind != "run_plan":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("plan_type") or "") != "multi_agent":
            continue
        found = str(payload.get("execution_id") or "").strip()
        if found:
            live_eid = found
    if live_eid != eid:
        logger.info(
            "stage_card.host_attach_invalid",
            reason="execution_mismatch",
            stamped=eid,
            live=live_eid,
            host_message_id=mid,
        )
        return None
    if not synthesizer_completed(entries, anchor):
        logger.info(
            "stage_card.host_attach_invalid",
            reason="synthesizer_incomplete",
            synthesizer_run_id=anchor,
            host_message_id=mid,
        )
        return None
    same_turn = bool(append_message_id and append_message_id.strip() == mid)
    return DebateHostAttach(
        execution_id=eid,
        host_message_id=mid,
        anchor_run_id=anchor,
        act_id=next_act_id(entries),
        same_turn=same_turn,
    )


def build_stage_card_payload(
    card: Mapping[str, Any],
    *,
    conversation_id: str,
    stage_card_id: str | None = None,
    host_execution_id: str | None = None,
    synthesizer_run_id: str | None = None,
    host_message_id: str | None = None,
) -> dict[str, Any] | None:
    """Build ``stage_card_required`` payload from a compliant motion_card, or None."""
    parsed, err = parse_motion_card(dict(card))
    if parsed is None or err:
        return None
    form = str(parsed.get("form") or "debate")
    thorough, max_rounds = default_rounds_for_form(form, thorough=True)
    payload: dict[str, Any] = {
        "stage_card_id": stage_card_id or new_id(),
        "conversation_id": conversation_id,
        "motion": parsed["motion"],
        "sides": list(parsed["sides"]),
        "form": form,
        "rationale": parsed["rationale"],
        "fact_pointers": list(parsed.get("fact_pointers") or []),
        "thorough": thorough,
        "max_rounds": max_rounds,
    }
    for key, val in (
        ("host_execution_id", host_execution_id),
        ("synthesizer_run_id", synthesizer_run_id),
        ("host_message_id", host_message_id),
    ):
        text = (val or "").strip() if isinstance(val, str) else ""
        if text:
            payload[key] = text
    # Allow motion_card / caller to stamp host fields directly.
    copy_host_triple(card, payload)
    return payload


async def emit_stage_card_for_motion(
    sink: Any,
    *,
    conversation_id: str,
    motion_card: Mapping[str, Any] | None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    journal_entries: Sequence[Mapping[str, Any]] | None = None,
) -> str | None:
    """Journal + SSE ``stage_card_required`` when motion_card is compliant.

    Persist-time emit uses ``prewrite_settlement_direct`` so the DURABLE fact lands
    even after the turn's journal writer is closed. Host triple is stamped from the
    emitting turn's MLR journal when available.
    """
    if not isinstance(motion_card, Mapping):
        return None
    host_kw: dict[str, str] = {}
    if turn_id:
        triple = host_triple_from_journal(journal_entries, host_message_id=str(turn_id))
        if triple is None and journal_entries is None:
            # Best-effort: load the emitting turn's journal for the stamp.
            try:
                from agentcore.db.base import async_session_factory
                from agentcore.db.repositories import TurnJournalRepository

                async with async_session_factory() as db:
                    entries = await TurnJournalRepository(db).load(str(turn_id))
                triple = host_triple_from_journal(entries, host_message_id=str(turn_id))
            except Exception:  # noqa: BLE001 — stamp 失败不阻断建卡
                triple = None
        if triple:
            host_kw = triple
    payload = build_stage_card_payload(
        motion_card,
        conversation_id=conversation_id,
        **host_kw,
    )
    if payload is None:
        return None
    # 单会话至多一张 pending：发新卡前把旧卡落 orphaned(superseded)。
    try:
        from agentcore.conversation.stage_card_resolve import orphan_conversation_stage_cards

        await orphan_conversation_stage_cards(
            conversation_id, sink=sink, reason="superseded"
        )
    except Exception as exc:  # noqa: BLE001 — 清旧卡失败不阻断建新卡
        logger.warning(
            "stage_card.supersede_prior_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )
    event = stage_card_required(**payload)
    if turn_id:
        from agentcore.runtime.settlement import prewrite_settlement_direct

        try:
            await prewrite_settlement_direct(
                turn_id=turn_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                event=event,
            )
        except Exception as exc:  # noqa: BLE001 — journal 失败 → 不发 SSE（无耐久事实）
            logger.warning(
                "stage_card.journal_write_failed",
                stage_card_id=payload["stage_card_id"],
                error=str(exc),
            )
            return None
    if sink is not None:
        with contextlib.suppress(Exception):
            sink.emit(event)
    has_triple = all(payload.get(k) for k in _HOST_TRIPLE_KEYS)
    logger.info(
        "stage_card.emitted",
        stage_card_id=payload["stage_card_id"],
        motion=str(payload["motion"])[:80],
        has_host_triple=has_triple,
    )
    if turn_id and not has_triple:
        # MLR 正常收尾发卡应必然带宿主三元组；缺失必须可观测，禁止静默。
        logger.warning(
            "stage_card.host_triple_missing",
            stage_card_id=payload["stage_card_id"],
            conversation_id=conversation_id,
            turn_id=str(turn_id),
            had_journal_entries=journal_entries is not None,
        )
    return str(payload["stage_card_id"])


def apply_motion_override(
    card_payload: Mapping[str, Any],
    motion_override: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """Merge optional motion rewrite; re-run motion_card gate.

    Returns ``(merged_card, error)``. On gate failure ``merged_card`` is None and
    ``error`` is the inline message (card stays pending — caller must not resolve).
    Host triple from the original payload is preserved on success.
    """
    motion = (motion_override if motion_override is not None else card_payload.get("motion")) or ""
    motion = str(motion).strip()
    candidate = {
        "motion": motion,
        "sides": list(card_payload.get("sides") or []),
        "fact_pointers": list(card_payload.get("fact_pointers") or []),
        "rationale": str(card_payload.get("rationale") or "").strip() or "阶段推进卡授权开辩",
        "form": str(card_payload.get("form") or "debate"),
    }
    parsed, err = parse_motion_card(candidate)
    if parsed is None:
        return None, err or "`motion` 检定未通过。"
    copy_host_triple(card_payload, parsed)
    return parsed, ""


def debate_arguments_from_card(
    card: Mapping[str, Any],
    *,
    note: str = "",
) -> dict[str, Any]:
    """Map stage-card / motion_card fields → DebateTool.execute arguments."""
    thorough_raw = card.get("thorough", True)
    thorough = True if not isinstance(thorough_raw, bool) else thorough_raw
    args: dict[str, Any] = {
        "motion": str(card.get("motion") or "").strip(),
        "sides": list(card.get("sides") or []),
        "form": str(card.get("form") or "debate"),
        "thorough": thorough,
    }
    try:
        max_rounds = int(card.get("max_rounds"))  # type: ignore[arg-type]
        if max_rounds >= 1:
            args["max_rounds"] = max_rounds
    except (TypeError, ValueError):
        pass
    # §7.5：开赛卡上已消歧的裁判三元组（用户点名或系统默认），resume 原样带回。
    mod_model = str(card.get("moderator_model") or "").strip()
    if mod_model:
        args["moderator_model"] = mod_model
        mod_origin = str(card.get("moderator_origin") or "").strip()
        if mod_origin:
            args["moderator_origin"] = mod_origin
        mod_provider = str(card.get("moderator_provider_id") or "").strip()
        if mod_provider:
            args["moderator_provider_id"] = mod_provider
    note_text = (note or "").strip()
    if note_text:
        args["_kickoff_ask"] = note_text
    return args


def research_first_user_message(*, motion: str = "") -> str:
    """Short user-visible message for the research_first follow-up turn."""
    topic = (motion or "").strip()
    if topic:
        return f"先补充多视角调研再开辩（命题：{topic}）"
    return "先补充多视角调研再开辩"


def research_first_bootstrap(*, motion: str = "", user_message: str = "") -> str:
    """Imperative blob injected into the CEO turn (同构 kickoff research_first)."""
    return research_first_tool_result(motion=motion, user_message=user_message)


def select_pending_stage_cards(
    entries: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Pending stage_card payloads from a turn journal (required without settle)."""
    if not entries:
        return []
    from agentcore.runtime.journal.pending_interactions import fold_interactions

    out: list[dict[str, Any]] = []
    for rec in fold_interactions(list(entries)):
        if rec.kind == "stage_card" and rec.status == "pending":
            out.append({"id": rec.id, "payload": dict(rec.payload)})
    return out


def turn_advanced_stage_from_entries(
    entries: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """True when journal/SSE shows debate call or any delegate this turn."""
    if not entries:
        return False
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if kind == "tool_use_start":
            name = str(payload.get("tool_name") or payload.get("name") or "")
            if name == "debate":
                return True
            if name == "delegate":
                return True
        if kind == "stage_card_resolved" and str(payload.get("decision") or "") == (
            "start_debate"
        ):
            return True
    return False
