"""User-turn cumulative token ceiling (策略 A 硬闸 · delivery reserve as minor helper).

Metering: all LLM calls during a bound user turn (CEO + workers + 续派 / debate).
Gate: when ``spent >= engine_turn_token_ceiling`` (>0), reject new ``delegate`` /
``debate`` and soft-stop WaveScheduler admission (in-flight drain only).

Delivery reserve: when ``spent >= ceiling − delivery_reserve``, prefer
``ceiling_priority`` tails — **not** the primary product fix for whole-page QA.
Website quality bottom line = section-level mechanical gates; whole-page / visual
verify may defer to a follow-up turn (``qa_deferred_budget``) rather than raising
reserve dials.

Nested envelope (B1): ``depth ≥ 1`` drives may reserve a sub-team envelope from
parent remaining (``engine_nested_turn_token_ceiling``). Wave ``should_stop`` then
uses ``(spent − baseline) ≥ envelope`` on the **same** ``TurnTokenMeter`` — never
swap the ContextVar meter. Parallel nests atomically reserve so they cannot each
claim the full remaining. Nested paths disable parent ``priority_reserve_hit``.

Step 2: when the ceiling is hit, inject a one-shot CEO wrap-up steer via the
existing soft-gate / coordination nudge seam (``maybe_inject_turn_token_budget_gate``)
so the captain closes on completed output — no parallel channel, no force_finalize.

Orthogonal to per-worker ``engine_worker_token_ceiling``. No USD / tiers / CEO
override / cancel-in-flight.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcore.runtime.runs.types import RunSpec

# delivery_status.gaps.reason — budget skip / deferred verify (not a soft-accept path)
REASON_TURN_TOKEN_BUDGET = "turn_token_budget"
REASON_QA_DEFERRED = "qa_deferred_budget"

TURN_TOKEN_CEILING_WARNING = "本回合累计 token 已触顶，未派发节点已跳过；请基于已完成产出收口"

TURN_TOKEN_RESERVE_SKIP_WARNING = "本回合进入交付预留窗口，次要节点已跳过以为验收节点留量"

TURN_TOKEN_NESTED_ENVELOPE_WARNING = (
    "子团队额度信封已触顶，未派发节点已跳过；下一回合可续跑未跑节点"
)


@dataclass
class TurnTokenMeter:
    """Mutable turn-scoped spent counter (task-local via ContextVar).

    ``reserved`` holds atomic nested-envelope reservations so parallel nests
    cannot each claim the same remaining headroom.
    """

    spent: int = 0
    reserved: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, tokens: int) -> None:
        if tokens > 0:
            with self._lock:
                self.spent += int(tokens)


@dataclass(frozen=True)
class NestedTurnEnvelope:
    """Active nested-drive envelope bound for wave ``should_stop`` predicates."""

    baseline: int
    envelope: int
    depth: int


class NestedEnvelopeRejected(RuntimeError):  # noqa: N818 — historical name; RuntimeError subclass
    """Parent remaining cannot fund a nested envelope (admission failure)."""


_meter: ContextVar[TurnTokenMeter | None] = ContextVar("turn_token_meter", default=None)
_nested_envelope: ContextVar[NestedTurnEnvelope | None] = ContextVar(
    "nested_turn_envelope", default=None
)


def bind_turn_token_meter(*, seed: int = 0) -> Token[TurnTokenMeter | None]:
    """Install a fresh meter for this user turn; returns reset token."""
    return _meter.set(TurnTokenMeter(spent=max(0, int(seed))))


def reset_turn_token_meter(token: Token[TurnTokenMeter | None]) -> None:
    _meter.reset(token)


def record_turn_tokens(tokens: int) -> None:
    """Accumulate tokens when a turn meter is bound (no-op off-turn / background)."""
    meter = _meter.get()
    if meter is None:
        return
    meter.add(tokens)


def current_turn_tokens() -> int:
    meter = _meter.get()
    return meter.spent if meter is not None else 0


def resolve_turn_token_ceiling() -> int:
    """Configured hard ceiling; ≤0 disables. Settings missing → 0 (off) for stubs."""
    try:
        from agentcore.config import settings

        return int(settings.engine_turn_token_ceiling)
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        return 0


def resolve_nested_turn_token_ceiling() -> int:
    """Nested sub-team envelope cap; ≤0 disables nested envelopes (fallback to parent)."""
    try:
        from agentcore.config import settings

        return int(settings.engine_nested_turn_token_ceiling)
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        return 0


def resolve_turn_token_delivery_reserve() -> int:
    """Absolute headroom for ``ceiling_priority`` tails; ≤0 disables reserve soft gate."""
    try:
        from agentcore.config import settings

        return int(settings.engine_turn_token_delivery_reserve)
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        return 0


def is_turn_token_ceiling_hit() -> bool:
    ceiling = resolve_turn_token_ceiling()
    if ceiling <= 0:
        return False
    return current_turn_tokens() >= ceiling


def is_turn_token_delivery_reserve_hit() -> bool:
    """True when spent has entered the delivery-reserve window (not yet hard ceiling).

    ``reserve <= 0`` or ``reserve >= ceiling`` → off (same pathology rule as worker
    wind_down). Hard ceiling alone still governs full stop.
    """
    ceiling = resolve_turn_token_ceiling()
    reserve = resolve_turn_token_delivery_reserve()
    if ceiling <= 0 or reserve <= 0 or reserve >= ceiling:
        return False
    spent = current_turn_tokens()
    if spent >= ceiling:
        return False  # hard ceiling owns the stop; reserve soft-gate is moot
    return spent >= (ceiling - reserve)


def turn_token_ceiling_reject_message() -> str:
    ceiling = resolve_turn_token_ceiling()
    spent = current_turn_tokens()
    return (
        f"本回合累计 token 已达上限（已用 {spent} / 上限 {ceiling}），"
        "禁止新开派单；请基于已完成产出收口。"
        "下一回合可续跑本图未跑节点（append 同图 / replan 点名），禁止假装已全部完成。"
    )


def nested_envelope_reject_message() -> str:
    ceiling = resolve_turn_token_ceiling()
    spent = current_turn_tokens()
    return (
        f"本回合父剩余 token 不足，无法为嵌套子团队拨付额度信封"
        f"（已用 {spent} / 上限 {ceiling}）；"
        "请下一回合续跑未完成节点，禁止本回合假装已全部完成。"
    )


def current_nested_envelope() -> NestedTurnEnvelope | None:
    return _nested_envelope.get()


def is_nested_envelope_hit() -> bool:
    """True when the active nested envelope has been exhausted (same meter)."""
    env = _nested_envelope.get()
    if env is None or env.envelope <= 0:
        return False
    return (current_turn_tokens() - env.baseline) >= env.envelope


def try_reserve_nested_envelope(*, depth: int) -> NestedTurnEnvelope | None:
    """Atomically reserve a nested envelope from parent remaining, or ``None``.

    Returns ``None`` when nested envelopes are disabled, no meter is bound, or
    parent remaining is 0. Caller must bind via :func:`bind_nested_envelope` /
    :func:`nested_turn_envelope_scope` and release on exit.
    """
    nested_cap = resolve_nested_turn_token_ceiling()
    if nested_cap <= 0:
        return None
    ceiling = resolve_turn_token_ceiling()
    if ceiling <= 0:
        return None
    meter = _meter.get()
    if meter is None:
        return None
    with meter._lock:
        remaining = max(0, ceiling - meter.spent - meter.reserved)
        if remaining <= 0:
            return None
        envelope = min(int(nested_cap), remaining)
        if envelope <= 0:
            return None
        baseline = meter.spent
        meter.reserved += envelope
        return NestedTurnEnvelope(baseline=baseline, envelope=envelope, depth=int(depth))


def release_nested_envelope(env: NestedTurnEnvelope) -> None:
    """Release a previously reserved nested envelope (spent already on the meter)."""
    meter = _meter.get()
    if meter is None:
        return
    with meter._lock:
        meter.reserved = max(0, meter.reserved - int(env.envelope))


def bind_nested_envelope(
    env: NestedTurnEnvelope,
) -> Token[NestedTurnEnvelope | None]:
    """Install nested envelope for wave predicates; returns reset token."""
    return _nested_envelope.set(env)


def reset_nested_envelope(token: Token[NestedTurnEnvelope | None]) -> None:
    _nested_envelope.reset(token)


@contextmanager
def nested_turn_envelope_scope(*, depth: int) -> Iterator[NestedTurnEnvelope | None]:
    """Reserve + bind a nested envelope for ``depth ≥ 1`` drives; no-op when disabled.

    Yields the envelope when reserved, or ``None`` when nested envelopes are off
    / no turn meter is bound (caller falls back to parent-ceiling predicates).
    Raises :class:`NestedEnvelopeRejected` when remaining is 0 (parent cannot
    fund a nest).
    """
    nested_cap = resolve_nested_turn_token_ceiling()
    if nested_cap <= 0 or depth < 1:
        yield None
        return
    if _meter.get() is None:
        # Off-turn / unit stubs without a meter — fall back to parent predicates.
        yield None
        return
    if is_turn_token_ceiling_hit():
        raise NestedEnvelopeRejected(turn_token_ceiling_reject_message())
    env = try_reserve_nested_envelope(depth=depth)
    if env is None:
        raise NestedEnvelopeRejected(nested_envelope_reject_message())
    token = bind_nested_envelope(env)
    try:
        from agentcore.core.logging import get_logger

        get_logger(__name__).info(
            "delegate.nested_turn_token_envelope",
            depth=env.depth,
            envelope=env.envelope,
            baseline=env.baseline,
            spent=current_turn_tokens(),
            ceiling=resolve_turn_token_ceiling(),
            nested_cap=nested_cap,
        )
        yield env
    finally:
        reset_nested_envelope(token)
        release_nested_envelope(env)


def resolve_wave_budget_hooks(*, credential_source: str) -> tuple[
    Callable[[], bool],
    Callable[[], bool] | None,
]:
    """Shared ``should_stop`` / ``priority_reserve_hit`` for drive + drive_redirect.

    Nested envelope active → stop on envelope only; parent delivery reserve off.
    Otherwise → parent hard ceiling + delivery reserve (depth-0 path).

    This drive's LLM payer (``credential_source``) ORs into ``should_stop`` so
    unstarted workers are not admitted after that payer's confirmed death.
    A different payer dying (e.g. platform chrome) does not stop this wave.
    """
    from agentcore.llm.turn_auth_dead import is_turn_auth_dead

    if _nested_envelope.get() is not None:

        def _nested_stop() -> bool:
            return is_nested_envelope_hit() or is_turn_auth_dead(credential_source)

        return _nested_stop, None

    def _parent_stop() -> bool:
        return is_turn_token_ceiling_hit() or is_turn_auth_dead(credential_source)

    return _parent_stop, is_turn_token_delivery_reserve_hit


def should_materialise_turn_token_budget_skips(*, credential_source: str) -> bool:
    """Whether un-run tails should be materialised as SKIPPED after a wave."""
    from agentcore.llm.turn_auth_dead import is_turn_auth_dead

    if is_turn_auth_dead(credential_source):
        return True
    if _nested_envelope.get() is not None:
        return is_nested_envelope_hit()
    return is_turn_token_ceiling_hit()


def budget_skip_warning_for_active_scope(*, credential_source: str) -> str:
    from agentcore.llm.turn_auth_dead import (
        TURN_AUTH_DEAD_REJECT_MESSAGE,
        is_turn_auth_dead,
        turn_auth_dead_reject_message,
    )

    if is_turn_auth_dead(credential_source):
        return turn_auth_dead_reject_message(credential_source) or (
            TURN_AUTH_DEAD_REJECT_MESSAGE
        )
    if _nested_envelope.get() is not None:
        return TURN_TOKEN_NESTED_ENVELOPE_WARNING
    return TURN_TOKEN_CEILING_WARNING


def is_page_qa_delivery_node(spec: RunSpec) -> bool:
    """True for whole-page / visual QA tails (``ceiling_priority`` + scan, or visual_critic)."""
    deliverable = getattr(spec, "deliverable", None)
    if deliverable is None:
        return False
    if bool(getattr(deliverable, "visual_critic", False)):
        return True
    return bool(getattr(spec, "ceiling_priority", False)) and bool(
        getattr(deliverable, "web_quality_scan", False)
    )


def honesty_gaps_for_skipped_delivery_node(spec: RunSpec) -> list[dict[str, str]]:
    """Gaps when a delivery/QA node never ran (诚实收口 · 建站验收可第二段).

    Same-turn quality bottom line is **section-level** mechanical gates
    (``web_quality`` / ``{{}}`` on fill workers). Skipping the whole-page QA
    worker must **not** claim those never ran — it defers page-level / visual
    verify to a follow-up turn (方案 3 底 + 方案 2 第二段).

    B3: callers also attach :func:`website_section.collect_light_website_acceptance_gaps`
    so empty shells cannot pass on deferred-QA honesty alone.
    """
    deliverable = getattr(spec, "deliverable", None)
    if deliverable is None:
        return []
    gaps: list[dict[str, str]] = []
    if is_page_qa_delivery_node(spec):
        gaps.append(
            {
                "description": (
                    "整页验收波未跑（本回合预算用尽）——"
                    "区块自动检查仍以各分区落盘为准；"
                    "轻量壳检（关键文件 / 残留 {{…}}）仍会跑；"
                    "请下一回合续派页面验收（总检/视觉），勿假装已质检通过"
                ),
                "reason": REASON_QA_DEFERRED,
            }
        )
        if bool(getattr(deliverable, "visual_critic", False)):
            gaps.append(
                {
                    "description": "视觉总检未目验（验收波未跑）",
                    "reason": REASON_QA_DEFERRED,
                }
            )
        return gaps
    if bool(getattr(deliverable, "web_quality_scan", False)):
        gaps.append(
            {
                "description": "未跑 web_quality 验收",
                "reason": REASON_TURN_TOKEN_BUDGET,
            }
        )
    return gaps


def turn_token_budget_wrap_prompt() -> str:
    """CEO one-shot ``[系统提示]``：触顶后基于已有产出收口（禁假完成 / 禁再派）。"""
    ceiling = resolve_turn_token_ceiling()
    spent = current_turn_tokens()
    return (
        f"[系统提示] 本回合累计 token 已触顶（已用 {spent} / 上限 {ceiling}）。"
        "本回合禁止乱开新派单与新辩论；在飞任务结束后请立即基于已完成产出向用户收口——"
        "汇总已有结论与落盘文件，并显式标出未完成缺口"
        f"（gap 原因可用 `{REASON_TURN_TOKEN_BUDGET}` / `{REASON_QA_DEFERRED}`）。"
        "**下一回合可续跑本图因额度未跑的节点**（append 同图 / replan 点名角色）；"
        "禁止假装本回合已全部完成。"
        "若建站「页面 QA」未跑：说明区块自动检查已覆盖落盘文件，"
        "**整页/视觉验收可下一回合续派**，禁止伪装成已质检完毕。"
        "禁止再尝试无关的新 delegate/debate；禁止空转探路；禁止把部分完成伪装成全部交付。"
    )


def tokens_from_journal_entries(entries: list[dict[str, Any]] | None) -> int:
    """Sum input+output from ``llm_call`` journal facts (resume seed)."""
    total = 0
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("kind") != "llm_call":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            continue
        inp = int(usage.get("input") or usage.get("input_tokens") or 0)
        out = int(usage.get("output") or usage.get("output_tokens") or 0)
        total += inp + out
    return total
