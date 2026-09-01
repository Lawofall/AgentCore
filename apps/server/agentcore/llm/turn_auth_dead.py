"""Turn-scoped LLM credential death flag — single source of truth (甲+乙 + 余额并入).

Within one user turn, the first confirmed **non-retryable credential failure**
latches a per-payer flag (``credential_source``: ``user`` vs ``platform``):

- ``LLMAuthError`` — real API key 401/403 (**not** ``InferenceTokenExpiredError``)
- ``LLMInsufficientBalanceError`` — upstream 402 / exhausted balance

Later *unstarted* LLM work for **that same payer** short-circuits here instead of
hitting upstream again. The other payer in the same turn is unaffected (platform
chrome death must not skip BYOK chat, and vice versa). Re-raise preserves the
original error class so client CTAs stay correct (换钥匙 vs 去充值).

Scope: one turn only (ContextVar + mutable object, same pattern as
``turn_token_budget``). **No** process-wide / cross-turn / TTL negative cache
(丙 deferred; keeps ``平台LLM接入``「禁止进程内 auth 熔断缓存」).

Consumers (all read this module — do not invent parallel flags):

- ``ObservingLLMProvider`` — mark on death; raise before new complete/stream
- ``run_background_llm`` — skip chrome when **that call's** source is latched
- ``resolve_wave_budget_hooks`` / materialise — ``should_stop`` when this drive's
  payer is dead (OR the turn token ceiling); stop admitting unstarted workers
- ``delegate`` / ``debate`` tools — hard-refuse new batches for the dead payer
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Literal

from agentcore.core.errors import (
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMInsufficientBalanceError,
)

TURN_AUTH_DEAD_REJECT_MESSAGE = (
    "本回合 API Key 鉴权已失败，已跳过后续模型调用。请先更新密钥或改用可用凭据后再试。"
)

TURN_BALANCE_DEAD_REJECT_MESSAGE = (
    "本回合账户余额不足，已跳过后续模型调用。请充值或换用可用凭据后再试。"
)

REASON_TURN_AUTH_DEAD = "turn_auth_dead"

LatchKind = Literal["auth", "balance"]
CredentialSource = Literal["user", "platform"]


@dataclass
class _SourceLatch:
    kind: LatchKind
    message: str | None = None


@dataclass
class TurnAuthDeadState:
    """Mutable turn-scoped latch (shared across asyncio tasks via object identity).

    ``deaths`` is keyed by payer (``user`` / ``platform``). Each source latches
    independently; one dying does not short-circuit the other.
    """

    deaths: dict[str, _SourceLatch] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


_state: ContextVar[TurnAuthDeadState | None] = ContextVar("turn_auth_dead", default=None)


def bind_turn_auth_dead() -> Token[TurnAuthDeadState | None]:
    """Install a fresh latch for this user turn; returns reset token."""
    return _state.set(TurnAuthDeadState())


def reset_turn_auth_dead(token: Token[TurnAuthDeadState | None]) -> None:
    _state.reset(token)


def normalize_credential_source(raw: object) -> CredentialSource:
    """Wire payer: only ``user`` / ``platform``; anything else is BYOK ``user``."""
    return raw if raw in ("user", "platform") else "user"


def credential_source_from_provider_name(name: str | None) -> CredentialSource:
    """Map a leaf ``name`` to wire ``credential_source`` (same rule as LLM*Error)."""
    return "platform" if (name or "").strip() == "platform" else "user"


def credential_source_from_llm(llm: object) -> CredentialSource:
    """Payer of this LLM handle: the leaf, or a turn-router's default leaf.

    Admission sites (delegate / debate / wave) gate *this tool's* LLM. Unprefixed
    CEO/worker calls use the router default; prefixed extras still hit
    :func:`raise_if_turn_auth_dead` at the leaf fence with that leaf's own source.
    """
    from agentcore.llm.call_fence import ObservingLLMProvider, unwrap_provider
    from agentcore.llm.provider.router import ProviderRouter

    node: object
    if llm is None:
        return "user"
    node = unwrap_provider(llm) if isinstance(llm, ObservingLLMProvider) else llm
    if isinstance(node, ProviderRouter):
        node = unwrap_provider(node._default)
    name = getattr(node, "name", None) or getattr(node, "_name", None)
    text = name if isinstance(name, str) else None
    return credential_source_from_provider_name(text)


def credential_source_from_auth_error(exc: LLMAuthError) -> str:
    """Map an auth error to wire ``credential_source`` (user BYOK vs platform)."""
    explicit = exc.details.get("credential_source")
    if explicit in ("user", "platform"):
        return str(explicit)
    provider = (exc.details.get("provider_name") or "").strip()
    if provider == "platform":
        return "platform"
    return "user"


def _source_from_death(exc: BaseException) -> CredentialSource:
    if isinstance(exc, LLMInsufficientBalanceError):
        return normalize_credential_source(exc.details.get("credential_source"))
    if isinstance(exc, LLMAuthError):
        return normalize_credential_source(credential_source_from_auth_error(exc))
    return "user"


def is_real_api_key_auth_death(exc: BaseException) -> bool:
    """True for mid-turn API key 401/403 — excludes inference-JWT remint path."""
    return isinstance(exc, LLMAuthError) and not isinstance(exc, InferenceTokenExpiredError)


def is_insufficient_balance_death(exc: BaseException) -> bool:
    """True for upstream 402 / exhausted balance (not retryable)."""
    return isinstance(exc, LLMInsufficientBalanceError)


def is_latchable_llm_death(exc: BaseException) -> bool:
    """True when this turn should short-circuit further unstarted LLM work."""
    return is_real_api_key_auth_death(exc) or is_insufficient_balance_death(exc)


def mark_turn_auth_dead(exc: BaseException) -> bool:
    """Latch this payer on first auth or balance death. Returns True when newly marked."""
    if not is_latchable_llm_death(exc):
        return False
    state = _state.get()
    if state is None:
        return False
    src = _source_from_death(exc)
    if is_insufficient_balance_death(exc):
        assert isinstance(exc, LLMInsufficientBalanceError)
        kind: LatchKind = "balance"
        message = (exc.message or "").strip() or TURN_BALANCE_DEAD_REJECT_MESSAGE
    else:
        assert isinstance(exc, LLMAuthError)
        kind = "auth"
        message = (exc.message or "").strip() or TURN_AUTH_DEAD_REJECT_MESSAGE
    with state._lock:
        if src in state.deaths:
            return False
        state.deaths[src] = _SourceLatch(kind=kind, message=message)
    try:
        from agentcore.core.logging import get_logger

        get_logger(__name__).info(
            "llm.turn_auth_dead",
            credential_source=src,
            kind=kind,
        )
    except Exception:  # noqa: BLE001 — observability must not break the turn
        pass
    return True


def is_turn_auth_dead(credential_source: str) -> bool:
    """True when **this payer** is latched dead this turn."""
    state = _state.get()
    if state is None:
        return False
    src = normalize_credential_source(credential_source)
    with state._lock:
        return src in state.deaths


def turn_auth_dead_reject_message(credential_source: str) -> str:
    state = _state.get()
    src = normalize_credential_source(credential_source)
    latch: _SourceLatch | None = None
    if state is not None:
        with state._lock:
            latch = state.deaths.get(src)
    if latch is not None and latch.message:
        return latch.message
    if latch is not None and latch.kind == "balance":
        return TURN_BALANCE_DEAD_REJECT_MESSAGE
    return TURN_AUTH_DEAD_REJECT_MESSAGE


def raise_if_turn_auth_dead(credential_source: str) -> None:
    """Re-raise the latched error class for **this payer** when set (no upstream call)."""
    state = _state.get()
    if state is None:
        return
    src = normalize_credential_source(credential_source)
    with state._lock:
        latch = state.deaths.get(src)
    if latch is None:
        return
    msg = latch.message or (
        TURN_BALANCE_DEAD_REJECT_MESSAGE
        if latch.kind == "balance"
        else TURN_AUTH_DEAD_REJECT_MESSAGE
    )
    if latch.kind == "balance":
        raise LLMInsufficientBalanceError(
            msg,
            credential_source=src,
            short_circuited=True,
        )
    raise LLMAuthError(
        msg,
        provider_name="platform" if src == "platform" else "user",
        credential_source=src,
        short_circuited=True,
    )
