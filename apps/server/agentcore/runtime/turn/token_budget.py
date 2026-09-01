"""User-turn cumulative token ceiling (策略 A 硬闸).

Metering: all LLM calls during a bound user turn (CEO + workers + 续派 / debate),
same unit as the per-worker fuse — ``TokenUsage.fuse_tokens`` (cache miss + new
output; providers that omit the split fall back to total). Live recording and
journal resume seeds share this unit. Billing / platform ¥ quota stay on full
``total_tokens``.

Gate: when ``spent >= engine_turn_token_ceiling`` (>0), reject new ``delegate`` /
``debate`` and soft-stop WaveScheduler admission (in-flight drain only). Nested
sub-teams share this remaining pool — no reserved envelopes.

When the ceiling is hit, inject a one-shot CEO wrap-up steer via the existing
soft-gate seam (``maybe_inject_turn_token_budget_gate``) so the captain closes on
completed output — no parallel channel, no force_finalize.

Orthogonal to per-worker ``engine_worker_token_ceiling``. No USD / tiers / CEO
override / cancel-in-flight / nested envelopes / delivery-reserve soft gate.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

# delivery_status.gaps.reason — budget skip (not a soft-accept path)
REASON_TURN_TOKEN_BUDGET = "turn_token_budget"

TURN_TOKEN_CEILING_WARNING = "本回合累计 token 已触顶，未派发节点已跳过；请基于已完成产出收口"


@dataclass
class TurnTokenMeter:
    """Mutable turn-scoped spent counter (task-local via ContextVar)."""

    spent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, tokens: int) -> None:
        if tokens > 0:
            with self._lock:
                self.spent += int(tokens)


_meter: ContextVar[TurnTokenMeter | None] = ContextVar("turn_token_meter", default=None)


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


def is_turn_token_ceiling_hit() -> bool:
    ceiling = resolve_turn_token_ceiling()
    if ceiling <= 0:
        return False
    return current_turn_tokens() >= ceiling


def turn_token_ceiling_reject_message() -> str:
    ceiling = resolve_turn_token_ceiling()
    spent = current_turn_tokens()
    return (
        f"本回合累计 token 已达上限（已用 {spent} / 上限 {ceiling}），"
        "禁止新开派单；请基于已完成产出收口。"
        "下一回合可续跑本图未跑节点（append 同图 / replan 点名），禁止假装已全部完成。"
    )


def resolve_wave_budget_hooks(*, credential_source: str) -> Callable[[], bool]:
    """``should_stop`` for drive + drive_redirect: turn ceiling OR this drive's payer death."""
    from agentcore.llm.turn_auth_dead import is_turn_auth_dead

    def _stop() -> bool:
        return is_turn_token_ceiling_hit() or is_turn_auth_dead(credential_source)

    return _stop


def should_materialise_turn_token_budget_skips(*, credential_source: str) -> bool:
    """Whether un-run tails should be materialised as SKIPPED after a wave."""
    from agentcore.llm.turn_auth_dead import is_turn_auth_dead

    if is_turn_auth_dead(credential_source):
        return True
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
    return TURN_TOKEN_CEILING_WARNING


def turn_token_budget_wrap_prompt() -> str:
    """CEO one-shot ``[系统提示]``：触顶后基于已有产出收口（禁假完成 / 禁再派）。"""
    ceiling = resolve_turn_token_ceiling()
    spent = current_turn_tokens()
    return (
        f"[系统提示] 本回合累计 token 已触顶（已用 {spent} / 上限 {ceiling}）。"
        "本回合禁止乱开新派单与新辩论；在飞任务结束后请立即基于已完成产出向用户收口——"
        "汇总已有结论与落盘文件，并显式标出未完成缺口"
        f"（gap 原因可用 `{REASON_TURN_TOKEN_BUDGET}`）。"
        "**下一回合可续跑本图因额度未跑的节点**（append 同图 / replan 点名角色）；"
        "禁止假装本回合已全部完成。"
        "禁止再尝试无关的新 delegate/debate；禁止空转探路；禁止把部分完成伪装成全部交付。"
    )


def tokens_from_journal_entries(entries: list[dict[str, Any]] | None) -> int:
    """Sum fuse tokens from ``llm_call`` journal facts (resume seed).

    Same unit as live :func:`record_turn_tokens` (cache miss + output; no split
    → input+output). Accepts short keys (``input`` / ``cache_hit``) and
    ``*_tokens`` aliases.
    """
    from agentcore.llm.provider.protocol import TokenUsage

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
        tu = TokenUsage(
            input_tokens=int(usage.get("input") or usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output") or usage.get("output_tokens") or 0),
            cache_hit_tokens=int(
                usage.get("cache_hit") or usage.get("cache_hit_tokens") or 0
            ),
            cache_miss_tokens=int(
                usage.get("cache_miss") or usage.get("cache_miss_tokens") or 0
            ),
        )
        total += tu.fuse_tokens
    return total
