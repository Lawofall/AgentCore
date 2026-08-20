"""Shared LLM billing preflight for user-facing and background call sites.

Chat turns, file assist, and the inference proxy all refuse or admit an LLM call
with the same per-origin billing decision: ``model_origin=byok`` requires the
user's own key (no quota check); ``model_origin=platform`` enforces quota then
runs on the global key.

This is the **admission** gate — one check as a turn / job / proxied call starts,
so a refusal is a clean 402/429/503 before any stream opens. It is not the whole
quota defence: :mod:`agentcore.billing.call_quota` re-checks before every upstream
call, which is what stops concurrent turns and worker fan-out from overselling one
stale reading (成本配额与计费 §一).

Background product chrome (title / memory / compaction) resolves
platform-first via ``resolve_and_gate_background``: platform spend always passes
``enforce_quota`` (no BYOK freeload); quota exhaustion yields no credentials so
best-effort callers degrade instead of 429-ing the user turn. ``run_background_llm``
keeps that contract when the per-call gate refuses mid-flight. An account that
explicitly pointed its background slot at its own key resolves BYOK up front (its
own spend — nothing to freeload), so it never sees the platform cap at all.

A refusal is returned, not raised, and it **says why**: ``run_background_llm``
answers with :class:`BackgroundLlmResult` or :class:`BackgroundLlmSkip`, never a
bare ``None``. Some refusals name the moment upstream frees up (a platform-funded
429 whose ``Retry-After`` outran the call's budget), and flattening that into an
empty value left callers guessing a cooldown upstream had already dated for them.

Auth-rejected platform keys fall back **once** to user BYOK through
``run_background_llm`` — the sole chrome entry that may retry after
``LLMAuthError``. Call sites must not invent their own try/except BYOK glue or
process-local auth circuit breakers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.preference import platform_catalog_visible
from agentcore.conversation.quota import QuotaLimits, enforce_quota
from agentcore.core.errors import (
    BYOKKeyMissingError,
    LLMAuthError,
    LLMQuotaExceededError,
    PlatformBillingUnavailableError,
    QuotaExceededError,
)
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import CostEventRepository, UserLlmProviderRepository, UserRepository
from agentcore.llm.background_failure import (
    classify_background_llm_failure,
    declared_recovery_seconds,
    is_config_shaped_background_failure,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.resolve import (
    ModelConfig,
    ModelOrigin,
    ModelPurpose,
    ModelSelection,
    platform_llm_credentials,
    resolve_background_user_fallback,
    resolve_model_config,
    resolve_user_llm_credentials,
)

logger = get_logger(__name__)

_PLATFORM_UNAVAILABLE_MESSAGE = (
    "平台额度暂不可用（运营方未配置平台 Key，或当前部署未开放平台代付）。"
    "请在设置中切换为自带 API Key，或联系管理员。"
)


@dataclass(frozen=True)
class BackgroundGateResolve:
    """Result of ``resolve_and_gate_background`` — credentials or why admission skipped."""

    credentials: LLMCredentials | None = None
    quota_skipped_at_admission: bool = False


@dataclass(frozen=True)
class BackgroundLlmResult[T]:
    """Successful background chrome LLM call: payload + credentials that worked."""

    value: T
    credentials: LLMCredentials


class BackgroundSkipReason(StrEnum):
    """Why background chrome got no value — one member per refusal site in the gate.

    ``NO_CREDENTIALS`` also covers an allowance already spent at admission:
    ``resolve_and_gate_background`` answers「没有可用凭据」for both a keyless account
    and an exhausted platform quota (it logs ``billing.background_quota_skip`` for
    the latter). ``QUOTA_EXCEEDED`` is the narrower mid-flight refusal — the per-call
    gate or upstream turning down a call this gate had already admitted.
    """

    TURN_AUTH_DEAD = "turn_auth_dead"
    NO_CREDENTIALS = "no_credentials"
    QUOTA_EXCEEDED = "quota_exceeded"
    AUTH_REJECTED = "auth_rejected"


@dataclass(frozen=True)
class BackgroundLlmSkip:
    """A refusal said out loud: why there is no value, and when that may change.

    ``declared_recovery_in`` is upstream's own answer to「多久之后重试才值得」in
    seconds, and is set **only** when the refusal named a moment — a platform-funded
    429 whose ``Retry-After`` outran the call's budget wears the ``QUOTA_EXCEEDED``
    face (``upstream_rate_limit_error``) and brings that date along. Every other
    refusal leaves it ``None`` rather than guessing: the caller's own cooldown
    estimate is the honest fallback, and it already exists.
    """

    reason: BackgroundSkipReason
    declared_recovery_in: float | None = None


type BackgroundLlmOutcome[T] = BackgroundLlmResult[T] | BackgroundLlmSkip


async def maybe_mark_byok_provider_error(
    *,
    user_id: str,
    purpose: str,
    credentials: LLMCredentials,
    exc: BaseException,
) -> None:
    """Best-effort: set BYOK provider ``status=error`` on config-shaped chrome failures.

    Platform credentials and non-config failures are no-ops. DB write failures are
    swallowed so background chrome never fails louder because of the badge write.
    """
    if credentials.source != "user":
        return
    provider_id = credentials.provider_id
    if not provider_id:
        return
    if not is_config_shaped_background_failure(exc):
        return

    reason = classify_background_llm_failure(exc)
    try:
        async with async_session_factory() as session:
            await UserLlmProviderRepository(session).update_status(provider_id, "error")
        logger.warning(
            "billing.background_byok_provider_error",
            user_id=user_id,
            purpose=purpose,
            provider_id=provider_id,
            reason=reason,
        )
    except Exception as mark_exc:
        logger.warning(
            "billing.background_byok_provider_error",
            user_id=user_id,
            purpose=purpose,
            provider_id=provider_id,
            reason=reason,
            error=str(mark_exc),
        )


class _BillingGateUser(Protocol):
    user_id: str


async def preflight_llm_credentials(
    *,
    session: AsyncSession,
    user: _BillingGateUser,
    cost_repo: CostEventRepository,
    byok_missing_message: str,
    model_origin: ModelOrigin,
    provider_id: str | None = None,
) -> LLMCredentials | None:
    """Run the shared billing gate before a user-facing LLM call.

    Returns resolved BYOK credentials, or ``None`` when the turn runs on the
    platform key (quota already enforced). Raises ``BYOKKeyMissingError`` (402),
    ``QuotaExceededError`` (429), or ``PlatformBillingUnavailableError`` (503)
    when the call must be refused.

    ``provider_id`` pins the exact BYOK 服务商 resolved for this turn (from the
    conversation override or the account default). It is authoritative: the turn runs
    on that provider's key. When it is absent / undecryptable the gate falls back to the
    account default provider, then 402s if the user has no usable provider at all.
    """
    if model_origin == "byok":
        credentials = await resolve_user_llm_credentials(
            session, user.user_id, provider_id=provider_id
        )
        if credentials is None and provider_id is not None:
            # Pinned provider gone / undecryptable → account default (silent fallback).
            credentials = await resolve_user_llm_credentials(session, user.user_id)
        if credentials is None:
            raise BYOKKeyMissingError(byok_missing_message)
        return credentials

    if not platform_catalog_visible():
        raise PlatformBillingUnavailableError(_PLATFORM_UNAVAILABLE_MESSAGE)

    await enforce_quota(
        cost_repo,
        user.user_id,
        limits=QuotaLimits.for_user(user),
    )
    return None


async def preflight_resolved_llm_credentials(
    *,
    session: AsyncSession,
    user: _BillingGateUser,
    cost_repo: CostEventRepository,
    byok_missing_message: str,
    selection: ModelSelection,
) -> LLMCredentials | None:
    """Gate + resolve credentials for standing_tasks / workflows (same shape).

    Runs :func:`preflight_llm_credentials` by ``selection.origin``, then for
    platform origin replaces the gate's ``None`` with
    ``platform_llm_credentials(model=selection.model)``.

    **Callers**: ``standing_tasks.runner`` and ``workflows.runner`` only.
    Handoff must keep its thin ``resolve_user_llm_credentials`` path — do **not**
    route handoff through this helper (would thicken dispatch into preflight).
    """
    credentials = await preflight_llm_credentials(
        session=session,
        user=user,
        cost_repo=cost_repo,
        byok_missing_message=byok_missing_message,
        model_origin=selection.origin,
        provider_id=selection.provider_id,
    )
    if selection.origin == "platform":
        credentials = platform_llm_credentials(model=selection.model)
    return credentials


def _creds_from_cfg(cfg: ModelConfig) -> LLMCredentials:
    return LLMCredentials(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        default_model=cfg.model,
        source="platform" if cfg.source == "platform" else "user",
        provider_id=cfg.provider_id,
        label=cfg.label,
    )


async def resolve_and_gate_background(
    session: AsyncSession,
    user_id: str,
    *,
    purpose: ModelPurpose = "title",
) -> BackgroundGateResolve:
    """Resolve background credentials (own-key slot, else platform-first) and gate spend.

    Returns credentials to pass to ``build_provider``, or a skip with
    ``quota_skipped_at_admission`` when platform quota blocked admission. Never raises
    quota errors — background paths are best-effort product chrome.

    When ``source=platform``, ``enforce_quota`` always runs (even if the account
    has a BYOK key) so background cannot freeload past the platform cap. A resolve
    that already landed on the user's own key skips the quota gate — that is their
    spend, not the platform's.

    Prefer ``run_background_llm`` at call sites that actually invoke the model: it
    adds the single platform-``LLMAuthError`` → user BYOK retry.
    """
    cfg = await resolve_model_config(session, user_id, purpose)
    if cfg is None:
        return BackgroundGateResolve()

    # Dormant / credentials gated off: same as main chat — BYOK or skip.
    if cfg.source == "platform" and not platform_catalog_visible():
        fallback = await resolve_and_gate_background_user_fallback(
            session, user_id, purpose=purpose
        )
        return BackgroundGateResolve(credentials=fallback)

    creds = _creds_from_cfg(cfg)
    if cfg.source != "platform":
        return BackgroundGateResolve(credentials=creds)

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        return BackgroundGateResolve()

    try:
        await enforce_quota(
            CostEventRepository(session),
            user_id,
            limits=QuotaLimits.for_user(user),
        )
    except QuotaExceededError as e:
        logger.info(
            "billing.background_quota_skip",
            user_id=user_id,
            purpose=purpose,
            error=str(e),
        )
        return BackgroundGateResolve(quota_skipped_at_admission=True)
    return BackgroundGateResolve(credentials=creds)


async def resolve_and_gate_background_user_fallback(
    session: AsyncSession,
    user_id: str,
    *,
    purpose: ModelPurpose = "title",
) -> LLMCredentials | None:
    """Resolve user BYOK for background chrome after platform is unavailable / auth-rejected.

    ``source=user`` — no platform quota. Returns ``None`` when the account has no
    usable BYOK key (including combo slots that only point at platform).
    """
    cfg = await resolve_background_user_fallback(session, user_id, purpose)
    if cfg is None:
        return None
    return _creds_from_cfg(cfg)


async def run_background_llm[T](
    user_id: str,
    *,
    purpose: ModelPurpose = "title",
    runner: Callable[[LLMCredentials], Awaitable[T]],
) -> BackgroundLlmOutcome[T]:
    """Platform-first background LLM with one BYOK retry on platform ``LLMAuthError``.

    Flow:
    1. ``resolve_and_gate_background`` (platform-first + quota when platform).
    2. Run ``runner(credentials)``.
    3. On ``LLMAuthError`` **and** ``credentials.source == "platform"``: resolve
       user BYOK once via ``resolve_and_gate_background_user_fallback`` and re-run.
    4. Missing credentials on either side, or BYOK also auth-fails →
       :class:`BackgroundLlmSkip`.
    5. On ``LLMQuotaExceededError``: the platform allowance is spent after step 1
       admitted the call — the per-call gate saying so, or upstream answering a
       day-scale 429 through the platform quota face. Same decision as step 1's own
       quota skip, just seen a moment later: skip rather than raise, so chrome keeps
       the "never 429s the user turn" contract. When that refusal named the moment
       it lifts, the skip carries it as ``declared_recovery_in``.
    6. Any other runner exception propagates unchanged.

    Every refusal is a ``BackgroundLlmSkip`` — best-effort callers stay silent
    either way, but the ones that schedule a retry can now read *why* and *when*
    instead of re-deriving a cooldown the refusal already stated.

    No process-local auth circuit breaker — each call re-resolves. Call sites must
    re-raise ``LLMAuthError`` from their generators so this entry can see it.

    Turn-scoped exception: when **this call's** payer is latched dead
    (``llm.turn_auth_dead``), skip immediately — that is per-turn short-circuit
    for that ``credential_source`` only, not a process TTL cache. Checked after
    resolve so the skip uses the actual source (platform-first chrome vs an
    own-key background slot), not an assumed payer. Does not widen
    platform→BYOK fallback: a dead platform resolve still returns here instead
    of taking the auth-fallback path.
    """
    from agentcore.llm.turn_auth_dead import is_turn_auth_dead

    async with async_session_factory() as session:
        resolved = await resolve_and_gate_background(session, user_id, purpose=purpose)
    if resolved.credentials is None:
        if resolved.quota_skipped_at_admission:
            return BackgroundLlmSkip(reason=BackgroundSkipReason.QUOTA_EXCEEDED)
        return BackgroundLlmSkip(reason=BackgroundSkipReason.NO_CREDENTIALS)

    if is_turn_auth_dead(resolved.credentials.source):
        logger.info(
            "billing.background_skip_turn_auth_dead",
            user_id=user_id,
            purpose=purpose,
        )
        return BackgroundLlmSkip(reason=BackgroundSkipReason.TURN_AUTH_DEAD)

    try:
        value = await runner(resolved.credentials)
        return BackgroundLlmResult(value=value, credentials=resolved.credentials)
    except LLMQuotaExceededError as e:
        # Quota ran out between resolve and call (per-call gate, billing.call_quota),
        # or upstream answered a cooldown too long for this call to sit out.
        declared = declared_recovery_seconds(e)
        logger.info(
            "billing.background_quota_skip",
            user_id=user_id,
            purpose=purpose,
            error=str(e),
            declared_recovery_sec=declared,
        )
        return BackgroundLlmSkip(
            reason=BackgroundSkipReason.QUOTA_EXCEEDED, declared_recovery_in=declared
        )
    except LLMAuthError as e:
        await maybe_mark_byok_provider_error(
            user_id=user_id, purpose=purpose, credentials=resolved.credentials, exc=e
        )
        if resolved.credentials.source != "platform":
            # Already on user BYOK — do not bounce back to platform.
            return BackgroundLlmSkip(reason=BackgroundSkipReason.AUTH_REJECTED)
    except Exception as e:
        await maybe_mark_byok_provider_error(
            user_id=user_id, purpose=purpose, credentials=resolved.credentials, exc=e
        )
        raise

    logger.info(
        "billing.background_platform_auth_fallback",
        user_id=user_id,
        purpose=purpose,
    )
    async with async_session_factory() as session:
        fallback = await resolve_and_gate_background_user_fallback(
            session, user_id, purpose=purpose
        )
    if fallback is None:
        return BackgroundLlmSkip(reason=BackgroundSkipReason.AUTH_REJECTED)

    if is_turn_auth_dead(fallback.source):
        logger.info(
            "billing.background_skip_turn_auth_dead",
            user_id=user_id,
            purpose=purpose,
        )
        return BackgroundLlmSkip(reason=BackgroundSkipReason.TURN_AUTH_DEAD)

    try:
        value = await runner(fallback)
        return BackgroundLlmResult(value=value, credentials=fallback)
    except LLMAuthError as e:
        await maybe_mark_byok_provider_error(
            user_id=user_id, purpose=purpose, credentials=fallback, exc=e
        )
        return BackgroundLlmSkip(reason=BackgroundSkipReason.AUTH_REJECTED)
    except Exception as e:
        await maybe_mark_byok_provider_error(
            user_id=user_id, purpose=purpose, credentials=fallback, exc=e
        )
        raise
