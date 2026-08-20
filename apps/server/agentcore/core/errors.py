"""Exception hierarchy for AgentCore.

All module-specific errors inherit from AgentCoreError.
Each error carries a code, message, and retryable flag for the API layer to translate
into appropriate HTTP responses. Every ``code`` is a member of the single
:class:`~agentcore.core.error_codes.ErrorCode` catalog (the shared directory),
so codes never drift apart from the SSE emitters or the frontend mirror.
"""

from datetime import UTC, datetime, timedelta

from agentcore.core.error_codes import ErrorCode

# How the layer *above* the provider leaf should treat an LLM failure.
# Distinct from ``retryable``, which is the leaf's remaining HTTP budget.
# Wave reads :func:`llm_failure_class` — not ``retryable`` — to fork
# wait-and-resume vs fail-the-node (a short 429 the leaf already sat out
# comes back ``retryable=False`` and still ``transient``).
LLM_FAILURE_TRANSIENT = "transient"
LLM_FAILURE_TERMINAL = "terminal"


class AgentCoreError(Exception):
    """Base exception for all AgentCore errors."""

    code: str = ErrorCode.INTERNAL_ERROR
    retryable: bool = False
    status_code: int = 500
    failure_class: str = LLM_FAILURE_TERMINAL

    def __init__(self, message: str = "", **kwargs):
        self.message = message
        self.details = kwargs
        super().__init__(message)


class LLMError(AgentCoreError):
    """LLM provider call failure."""

    code: str = ErrorCode.LLM_ERROR
    status_code = 502


class LLMUpstreamError(LLMError):
    """Upstream provider returned 5xx (transient server error). Retryable."""

    retryable = True


class OurServiceUnavailableError(LLMError):
    """Our own cloud hop (inference proxy / gateway) failed — not the model vendor.

    Stays inside the LLM error family so the provider keeps its retry budget and
    its「already committed partial content」handling, while ``code`` names the real
    fault so the bubble never blames the user's Base URL / API Key. ``code`` and
    ``retryable`` are stamped per instance from the wire envelope.
    """

    code: str = ErrorCode.INTERNAL_ERROR
    status_code = 503
    retryable = True


# Longest upstream ``Retry-After`` an *interactive* turn will actually sit out, and
# the single source for every 429 sentence below.
#
# A header-less 429 is judged against it too, and there the number compared is our
# own backoff chain (2→4→8→16→32), which outgrows this line on the fifth attempt —
# after ~30s already slept. That is what the 138 production give-ups logging 32 秒
# were: our number, not an upstream cooldown sitting two seconds past the line, so
# no widened per-call budget was ever going to rescue them (see
# ``llm.provider.call_budget``). A caller with a wall clock of its own may derive a
# different ceiling for ``retryable``; the copy below keeps this one, because a human
# waiting on a turn is the only caller whose 429 ever becomes a sentence on screen.
MAX_RETRY_AFTER = 30.0

# Where a 429's cooldown number came from. A header-less 429 still needs *some*
# number to pace the retry with, and that number is our own exponential backoff
# (``openai_compatible._parse_retry_after``) — a fallback worth keeping, but never
# one to read back as something upstream said: 138 production give-ups logged
# ``retry_after_sec=32.0``, the last link of our own 2→4→8→16→32 chain, and were
# then reasoned about as「上游只要 32 秒，放宽一点就能救回来」.
#
# So it also gates the copy: only ``RETRY_AFTER_FROM_HEADER`` may turn into a moment
# a user reads. The other two are numbers we cannot attest, and a clock time derived
# from one is an invention no upstream ever made.
RETRY_AFTER_FROM_HEADER = "upstream_header"
RETRY_AFTER_FROM_BACKOFF = "local_backoff"
# Relayed to us as a plain number (our own ``/inference/`` hop rebuilds the leaf's
# 429 from an envelope field): a cooldown we cannot attest either way.
RETRY_AFTER_UNKNOWN = "unknown"


def utc_moment_iso(moment: datetime) -> str:
    """An absolute moment as the wire's ISO-8601 UTC instant (``2026-08-14T16:00:00Z``).

    Moments a user acts on travel as data, never as prose the server pre-worded.
    Copy used to stamp「8 月 14 日 16:00（UTC）」straight into the sentence, which is
    accurate and unusable in one read: a user in UTC+8 waiting out a day reset has to
    convert it to next-day 00:00 before knowing whether to wait or go to bed, and a
    wrong conversion sends him back at 16:05 to the same wall. Stamping the server's
    own zone instead would only swap whose zone is wrong. The client knows the
    reader's zone, so it gets the instant and renders it there.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def recovery_at_iso(retry_after: float, *, now: datetime | None = None) -> str:
    """ISO-8601 UTC instant a ``retry_after``-second cooldown ends at.

    Day-scale ``Retry-After`` values are upstream day resets, so what a user needs
    is when service returns — never「等 16.6 小时」, which reads as a promise no
    retry budget ever made.
    """
    base = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    return utc_moment_iso(base + timedelta(seconds=retry_after))


# Every absolute moment a user-facing error may carry, and the only ``details``
# keys the plain JSON error envelope forwards. They exist because the copy no
# longer names a clock time, so a client that cannot read them degrades to a
# thinner-but-true sentence — which is only tolerable while every live client
# can, on both the SSE error context and the bare 429 body.
WIRE_MOMENT_FIELDS = ("recovery_at", "reset_at")


def wire_moments(exc: BaseException) -> dict[str, str]:
    """The ISO-8601 UTC instants ``exc`` carries for a client to render locally."""
    if not isinstance(exc, AgentCoreError):
        return {}
    return {
        field: value
        for field in WIRE_MOMENT_FIELDS
        if isinstance(value := exc.details.get(field), str) and value
    }


class LLMRateLimitError(LLMError):
    """LLM API rate limit hit (429). User-facing zh message.

    Two questions, deliberately keyed on two different numbers. ``retryable``
    answers「*这次调用*等不等得起」and follows ``retry_ceiling`` — the caller's own
    remaining budget, defaulting to the interactive one. The sentence answers
    「用户该怎么办」and follows :data:`MAX_RETRY_AFTER` alone: past it the copy
    stops saying「请稍后再试」and says plainly that retrying keeps failing until the
    allowance returns — otherwise the user obeys copy the engine has already
    overruled and burns a handful of guaranteed-failing retries. A background
    one-shot may well retry a cooldown this copy calls hopeless; nobody reads that
    copy, which is exactly why only silent-degrade callers are allowed their own
    ceiling (see ``llm.provider.call_budget``).

    The moment itself rides in ``recovery_at`` (ISO-8601 UTC) rather than in the
    sentence, so the client can name it in the reader's own zone; a client that
    cannot read it is left with copy that is thinner but still true.

    Inside the ceiling the copy still says「再试」but never「点重试」: the red
    error card carries no retry control (定案 A), so naming a button sends the
    user hunting for one. Re-sending the message is the real next step.

    Platform-funded turns take the ``QUOTA_EXCEEDED`` face for that same long
    *declared* cooldown: build 429s through :func:`upstream_rate_limit_error` so the
    split by credential source happens in one place.

    ``retry_after_source`` says whose number ``retry_after`` is — upstream's header,
    our own backoff standing in for a missing one, or unattested — and it decides how
    much this copy is allowed to claim. Only a declared cooldown may name a moment,
    an allowance, or a wait in seconds: on a header-less 429 the number is the last
    link of our own backoff chain, and wording it as「请约 N 秒后再试」told users a
    vendor deadline nobody declared (production ``retry_after=None`` /
    ``cooldown_source=local_backoff``). What is left to say there is what we actually
    know — we stopped retrying — plus, for a platform key, the BYOK exit that
    genuinely does bypass the limit right now.

    ``self.retry_after`` keeps the engine number (including our backoff). The wire
    envelope and user-facing sentence only carry a duration when the source is
    :data:`RETRY_AFTER_FROM_HEADER`.
    """

    code = ErrorCode.LLM_RATE_LIMIT
    retryable = True
    failure_class = LLM_FAILURE_TRANSIENT

    def __init__(
        self,
        retry_after: float | None = None,
        *,
        now: datetime | None = None,
        retry_ceiling: float | None = None,
        retry_after_source: str = RETRY_AFTER_UNKNOWN,
        **kwargs,
    ):
        self.retry_after = retry_after
        # Attribute, not ``details``: this is for our logs, not the wire envelope.
        self.retry_after_source = retry_after_source
        ceiling = MAX_RETRY_AFTER if retry_ceiling is None else retry_ceiling
        self.retryable = not (retry_after is not None and retry_after > ceiling)
        declared = retry_after if retry_after_source == RETRY_AFTER_FROM_HEADER else None
        if declared is not None and declared > MAX_RETRY_AFTER:
            # BYOK: the throttled allowance is the user's own; unknown source
            # stays on the vaguer「上游额度」rather than guessing whose key it is.
            whose = (
                "你的服务商额度"
                if kwargs.get("credential_source") == "user"
                else "上游额度"
            )
            message = f"上游限流，本回合无法继续。{whose}恢复前重试仍会失败。"
            kwargs["recovery_at"] = recovery_at_iso(declared, now=now)
        elif retry_after is not None and retry_after > MAX_RETRY_AFTER:
            # Past the ceiling on a cooldown nobody declared: we gave up, and that is
            # the whole of what we know. No moment, and no allowance either — a
            # header-less 429 is as likely to be throttling as an exhausted quota.
            message = (
                "平台模型被上游限流，本回合无法继续。"
                "可接入自己的 API Key 立即继续，或稍后重新发送。"
                if kwargs.get("credential_source") == "platform"
                else "上游限流，本回合无法继续。请稍后重新发送。"
            )
        elif declared is not None and 0 < declared <= MAX_RETRY_AFTER:
            message = (
                f"上游限流，暂时无法继续本回合。请约 {int(declared)} 秒后再试。"
            )
        else:
            # Unattested short cooldown (local backoff / unknown): we know we
            # stopped, not when the vendor will take us back.
            message = "上游限流，暂时无法继续本回合。请稍后再试。"
        # Wire ``retry_after`` is the attested header only — ErrorContext documents
        # it as 上游 Retry-After. The engine still has ``self.retry_after``.
        kwargs.pop("retry_after", None)
        super().__init__(message, **kwargs, retry_after=declared)


class LLMQuotaExceededError(LLMError):
    """Our own cloud hop refused the call because a usage quota is exhausted.

    The sidecar leaf reaches models through ``/inference/``, which answers
    ``QUOTA_EXCEEDED`` for a fault only the cloud can see: the user's allowance is
    spent. Distinct from :class:`LLMRateLimitError` — nothing clears on its own, so
    retrying only burns a minute of backoff before repeating the same refusal, and
    the remedy is the ``QUOTA_EXCEEDED`` CTA (wait for reset / bring your own key)
    rather than「稍后再试」.

    Wire twin of :class:`QuotaExceededError` (route preflight, HTTP 429) for the
    leaf side of that hop: same code, but an ``LLMError`` so the provider retry loop
    and the turn's error surfacing treat it like any other leaf failure.

    Also the face a *vendor* 429 takes when a platform key draws an upstream-declared
    cooldown longer than :data:`MAX_RETRY_AFTER` (:func:`upstream_rate_limit_error`):
    to the user that is the same wall — an operator-owned allowance they cannot clear.
    A header-less 429 does not qualify, however long our own backoff grew: this face
    asserts an exhausted allowance, and nothing there proves one.

    ``retry_after_source`` carries the same provenance :class:`LLMRateLimitError`
    does, for the same reason: this face is what a *background* caller meets when a
    dated 429 outruns its budget, and the schedulers reading it (compaction folding,
    memory consolidation) sit out a wall only when the number is upstream's own. The
    quota-window face raised by ``billing.call_quota`` dates nothing and leaves it
    unattested.
    """

    code = ErrorCode.QUOTA_EXCEEDED
    status_code = 429
    retryable = False

    _DEFAULT_MESSAGE = (
        "额度已用完，本回合无法继续。请等待额度重置，或接入自己的 API Key。"
    )

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_source: str = RETRY_AFTER_UNKNOWN,
        **kwargs,
    ):
        # Attribute, not ``details``: for our own scheduling, not the wire envelope.
        self.retry_after_source = retry_after_source
        super().__init__(message or self._DEFAULT_MESSAGE, **kwargs)


def upstream_rate_limit_error(
    retry_after: float | None,
    *,
    credential_source: str | None = None,
    now: datetime | None = None,
    retry_ceiling: float | None = None,
    retry_after_source: str = RETRY_AFTER_UNKNOWN,
    **details,
) -> LLMError:
    """Product face for an upstream 429, split by who funds the key.

    ``retry_ceiling`` is what *this* call can sit out (default: the interactive
    :data:`MAX_RETRY_AFTER`). While the cooldown fits inside it the 429 is an
    ordinary retryable throttle and must stay an :class:`LLMRateLimitError` — at
    that point the exception is the provider loop's own control flow rather than a
    product face, and morphing it into a quota wall would make the loop skip the
    retry it was about to perform.

    The wall faces are what we raise when we *give up*, and then the two credential
    sources need different exits: a platform-funded call hit an allowance the user
    cannot clear, so it takes the ``QUOTA_EXCEEDED`` face (client suppresses retry
    and offers「接入自己的 Key」); BYOK keeps the rate-limit face, since telling a
    user who already brought their own key to bring one is nonsense. An unknown
    source takes the BYOK-free conservative branch rather than guessing a platform
    wall.

    That quota face needs the cooldown to be upstream's own (``retry_after_source``):
    it says an allowance ran out and dates its return, and a header-less 429 proves
    neither — only that our backoff outgrew the ceiling. Those keep the rate-limit
    face, which carries the same「接入自己的 Key」exit without the two claims.
    """
    if credential_source in ("user", "platform"):
        details["credential_source"] = credential_source
    ceiling = MAX_RETRY_AFTER if retry_ceiling is None else retry_ceiling
    declared = retry_after if retry_after_source == RETRY_AFTER_FROM_HEADER else None
    if (
        declared is not None
        and declared > ceiling
        and declared > MAX_RETRY_AFTER
        and details.get("credential_source") == "platform"
    ):
        return LLMQuotaExceededError(
            "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，"
            "或接入自己的 API Key 立即继续。",
            retry_after=declared,
            recovery_at=recovery_at_iso(declared, now=now),
            retry_after_source=retry_after_source,
            **details,
        )
    return LLMRateLimitError(
        retry_after,
        now=now,
        retry_ceiling=retry_ceiling,
        retry_after_source=retry_after_source,
        **details,
    )


def llm_failure_class(exc: BaseException) -> str:
    """``transient`` vs ``terminal`` for the layer above the provider leaf.

    Wave (and any other run-level retry) must read this, not ``retryable``:

    - ``transient`` — upstream pressure that clears on a clock. Do **not**
      re-run the node from round 0. Wait until ``retry_after`` / ``recovery_at``
      and resume the transcript. Rate limits are always this, even after the
      leaf spent its in-place retries (``retryable`` is then False).
    - ``terminal`` — will not clear by waiting (auth, balance, undated quota).

    A platform 429 that took the dated quota face still counts as transient:
    the allowance returns, the node must not be rebuilt.
    """
    if isinstance(exc, LLMRateLimitError):
        return LLM_FAILURE_TRANSIENT
    if isinstance(exc, LLMQuotaExceededError) and exc.details.get("recovery_at"):
        return LLM_FAILURE_TRANSIENT
    if isinstance(exc, AgentCoreError):
        return exc.failure_class
    return LLM_FAILURE_TERMINAL


def mark_llm_leaf_exhausted(exc: AgentCoreError) -> None:
    """The leaf spent its in-place retry; Wave must not treat this as a node retry.

    Leaves ``failure_class`` untouched so a rate limit stays transient.
    """
    exc.retryable = False


class LLMTimeoutError(LLMError):
    """LLM API request timed out."""

    code = ErrorCode.LLM_TIMEOUT
    retryable = True


class LLMInsufficientBalanceError(LLMError):
    """Configured API key reached the upstream but the account balance is
    exhausted (typically HTTP 402 Insufficient Balance) — any OpenAI-compatible
    vendor, not DeepSeek-only.

    Distinct from ``BYOKKeyMissingError`` (no key at all, refused at the route
    preflight before the stream opens): here a *valid* key fails mid-turn, so the
    error surfaces as an inline ``error`` event rather than a 402 JSON response. Not
    retryable — an immediate retry just re-fails until the user tops up. Copy is
    vendor-neutral (the key is fine; the balance is not).
    """

    code = ErrorCode.LLM_INSUFFICIENT_BALANCE
    retryable = False

    # Platform keys are operator-owned: the end user cannot top them up, so the
    # copy offers the BYOK exit instead of a 充值 instruction they cannot act on.
    _PLATFORM_MESSAGE = (
        "平台模型暂时不可用（上游账户余额不足）。请改用自己的 API Key，或联系管理员。"
    )

    def __init__(
        self,
        message: str | None = None,
        *,
        provider_name: str | None = None,
        display_name: str | None = None,
        **kwargs,
    ):
        if message is None:
            name = (provider_name or "").strip()
            shown = (display_name or "").strip()
            if name == "platform":
                message = self._PLATFORM_MESSAGE
            else:
                if shown:
                    label = shown
                elif name and name != "user":
                    label = name
                else:
                    label = "服务商"
                message = f"{label} API Key 有效，但账户余额不足，请充值后重试。"
        if provider_name is not None and "provider_name" not in kwargs:
            kwargs["provider_name"] = provider_name
        if "credential_source" not in kwargs:
            name = (provider_name or "").strip()
            kwargs["credential_source"] = "platform" if name == "platform" else "user"
        super().__init__(message, **kwargs)


# Single source for「你还没配 key」across the leaf error and the route preflights
# (conversations / inference proxy pass it as ``byok_missing_message``). One
# constant so the next wording change is one edit. The sentence does not name a
# client page: the same copy is sent to desktop, mobile, and admin, and their
# Key-config screens do not share a name. Navigation is each client's CTA.
BYOK_KEY_REQUIRED_MESSAGE = "请先接入自己的 API Key，再发起对话。"


class LLMKeyRequiredError(LLMError):
    """A turn reached the model hop with no BYOK key configured at all.

    Wire twin of :class:`BYOKKeyMissingError` (route preflight, HTTP 402) for the
    sidecar leaf: the cloud ``/inference/`` hop refuses before it ever contacts a
    vendor, and the leaf must keep the ``LLM_KEY_REQUIRED`` code so each client
    can offer its own Key-config CTA. Distinct from
    :class:`LLMInsufficientBalanceError` — there is no key and no account to top
    up, so 充值 is the wrong remedy — and not retryable, since only the user
    adding a key can change the outcome.

    Copy does not name a settings page: the same sentence is sent to every client.
    """

    code = ErrorCode.LLM_KEY_REQUIRED
    status_code = 402
    retryable = False

    _DEFAULT_MESSAGE = BYOK_KEY_REQUIRED_MESSAGE

    def __init__(self, message: str | None = None, **kwargs):
        super().__init__(message or self._DEFAULT_MESSAGE, **kwargs)


class LLMAuthError(LLMError):
    """Configured API key rejected upstream (HTTP 401/403): invalid, revoked,
    or lacking permission — for any provider (BYOK DeepSeek, platform Claude, …).

    Distinct from ``BYOKKeyMissingError`` (no key at all, refused at preflight): a
    *configured* key fails mid-turn, so it surfaces as an inline ``error`` event. Not
    retryable — re-sending with the same bad key just re-fails — and its message (and
    the ``LLM_KEY_INVALID`` code, which each client maps to its own Key-config CTA)
    lets the user fix the key.

    Platform keys are operator-owned: default copy must not echo upstream gateway
    help (e.g. CC Switch tutorials) or the internal provider label ``platform``.
    """

    code = ErrorCode.LLM_KEY_INVALID
    retryable = False

    _PLATFORM_MESSAGE = "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。"

    def __init__(
        self,
        message: str | None = None,
        *,
        provider_name: str | None = None,
        display_name: str | None = None,
        **kwargs,
    ):
        name = (provider_name or "").strip()
        shown = (display_name or "").strip()
        if message is None:
            if name == "platform":
                message = self._PLATFORM_MESSAGE
            else:
                if shown:
                    label = shown
                elif name and name != "user":
                    label = name
                else:
                    label = "服务商"
                message = f"{label} API Key 无效或无权限，请更新后重试。"
        # Wire CTA 分流：platform → 接入自己的 Key；user/BYOK → 各端换 Key
        # （桌面「去服务商」、手机「去配置」）。句子本身不点名页面。
        if "credential_source" not in kwargs:
            kwargs["credential_source"] = "platform" if name == "platform" else "user"
        if provider_name is not None and "provider_name" not in kwargs:
            kwargs["provider_name"] = provider_name
        super().__init__(message, **kwargs)


class InferenceTokenExpiredError(LLMAuthError):
    """Sidecar→cloud inference proxy JWT rejected (invalid / expired).

    Distinct from BYOK ``LLM_KEY_INVALID``: the user should remint / re-login /
    retry the turn — not open a Key-config screen to edit an API key. ``retryable``
    so the desktop can clear the cache, mint once, and retry the turn.
    """

    code = ErrorCode.INFERENCE_TOKEN_EXPIRED
    retryable = True

    # 「再试」= 重发本条消息：红错误卡不挂重试按钮（定案 A），点名一个按钮只会让人白找。
    _DEFAULT_MESSAGE = (
        "本地与云端的推理凭证已失效或过期。请稍后再试（将自动换新凭证）；仍失败请重新登录后再试。"
    )

    def __init__(self, message: str | None = None, **kwargs):
        # Bypass LLMAuthError's BYOK default copy.
        LLMError.__init__(self, message or self._DEFAULT_MESSAGE, **kwargs)


class LLMClientClosedError(LLMError):
    """httpx client was closed while a caller still tried to send (turn teardown race).

    Coordination background drives clone an independent client so chat-turn
    ``llm.close()`` cannot hit in-flight workers; residual paths that still share a
    closed client must not burn WaveScheduler infra retries — re-POST on the same
    closed client is deterministic failure.
    """

    code = ErrorCode.LLM_ERROR
    retryable = False

    def __init__(
        self,
        message: str = "Cannot send a request, as the client has been closed.",
        **kwargs,
    ):
        super().__init__(message, **kwargs)


class LLMInvalidResponseError(LLMError):
    """Upstream returned HTTP 2xx but the body is not usable JSON.

    Typical BYOK/gateway cases: HTML login page, reverse-proxy interstitial, or
    other non-OpenAI shells. Not retryable — the same endpoint will keep returning
    the same shell. Side-path logs classify this as ``invalid_response`` so it
    does not drown in the ``other`` bucket.
    """

    code = ErrorCode.LLM_ERROR
    retryable = False


def is_llm_client_closed_error(exc: BaseException) -> bool:
    """True for typed closed-client errors or httpx's RuntimeError wording."""
    if isinstance(exc, LLMClientClosedError):
        return True
    if isinstance(exc, RuntimeError):
        return "client has been closed" in str(exc).lower()
    return False


class ToolError(AgentCoreError):
    """Tool execution failure."""

    code = ErrorCode.TOOL_ERROR
    status_code = 500


class ToolNotFoundError(ToolError):
    """Requested tool not registered."""

    code = ErrorCode.TOOL_NOT_FOUND
    status_code = 404


class SandboxError(AgentCoreError):
    """Code sandbox execution failure."""

    code = ErrorCode.SANDBOX_ERROR
    status_code = 500


class SandboxTimeoutError(SandboxError):
    """Code execution exceeded timeout."""

    code = ErrorCode.SANDBOX_TIMEOUT


class AuthenticationError(AgentCoreError):
    """Authentication failure."""

    code = ErrorCode.AUTH_ERROR
    status_code = 401


class EmailNotVerifiedError(AuthenticationError):
    """Credentials are valid but the account has not verified its inbox.

    Only raised when ``require_email_verified`` is on (default off). 403 so
    clients do not treat this as a wrong-password 401 and wipe the session.
    """

    code = ErrorCode.EMAIL_NOT_VERIFIED
    status_code = 403

    def __init__(self, message: str = "请先验证邮箱", **kwargs):
        super().__init__(message, **kwargs)


class GoneError(AgentCoreError):
    """The resource / endpoint is no longer available (HTTP 410)."""

    code = ErrorCode.GONE
    status_code = 410


class AuthorizationError(AgentCoreError):
    """Authorization/permission failure."""

    code = ErrorCode.FORBIDDEN
    status_code = 403


class AdminProductForbiddenError(AuthorizationError):
    """Admin accounts cannot authenticate on product clients (desktop / mobile)."""

    code = ErrorCode.ADMIN_PRODUCT_FORBIDDEN

    def __init__(self, message: str = "管理员账号请使用管理后台登录", **kwargs):
        super().__init__(message, **kwargs)


class MfaRequiredError(AgentCoreError):
    """Password verified; TOTP step still pending."""

    code = ErrorCode.MFA_REQUIRED
    status_code = 401


class MfaSetupRequiredError(AgentCoreError):
    """Admin session exists but MFA enrollment is incomplete."""

    code = ErrorCode.MFA_SETUP_REQUIRED
    status_code = 428


class NotFoundError(AgentCoreError):
    """Resource not found."""

    code = ErrorCode.NOT_FOUND
    status_code = 404


class ConflictError(AgentCoreError):
    """Request conflicts with the resource's current state (HTTP 409).

    e.g. moving a *started* conversation between folders: its folder decides which
    workspace directory it runs in — and whether cloud or local (双模式工作区 §七:
    folder = project = workspace) — so re-filing a chat that has already
    accumulated files would silently re-point it at a different directory. The
    workspace is fixed once a conversation has any messages, so the move is refused
    rather than quietly switching it.
    """

    code = ErrorCode.CONFLICT
    status_code = 409


class ValidationError(AgentCoreError):
    """Input validation failure."""

    code = ErrorCode.VALIDATION_ERROR
    status_code = 422


class PayloadTooLargeError(ValidationError):
    """Request / download payload exceeds a configured byte ceiling (HTTP 413).

    Reuses ``VALIDATION_ERROR`` so clients already treating validation as
    non-retriable keep working; status is 413 so oversized panel downloads are
    distinct from generic 422 path/UTF-8 problems and never collapse to 500.
    """

    status_code = 413


class RateLimitedError(AgentCoreError):
    """Too many requests in a rolling window; this one is refused (HTTP 429).

    The 速率 line of defense (成本配额与计费.md §一), orthogonal to 配额 (总量):
    rate limiting caps requests-per-window, quota caps cumulative usage. Per-user
    message-send throttling is enforced at the route layer against the authenticated
    user. ``retry_after`` (seconds) rides along so the API layer can emit a
    ``Retry-After`` header and the client can show a friendly cool-down. Reuses the
    ``RATE_LIMITED`` code shared with the auth-endpoint limiter so the client handles
    one rate-limit shape regardless of which layer tripped.
    """

    code = ErrorCode.RATE_LIMITED
    status_code = 429

    def __init__(self, message: str = "", *, retry_after: float | None = None, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, retry_after=retry_after, **kwargs)


class QuotaExceededError(AgentCoreError):
    """A configured usage quota is exhausted; the next turn is refused.

    Three independent dimensions (daily tokens / monthly cost / daily requests),
    checked before a turn starts (成本配额与计费.md §一). Maps to HTTP 429 so the
    client can surface a "quota reached" state distinct from auth (401) or
    validation (422). ``dimension`` / ``used`` / ``limit`` ride along on the
    exception for logging and tests.

    ``reset_at`` is the ISO-8601 UTC instant the exhausted window rolls over at
    (:func:`utc_moment_iso`). It rides in the envelope rather than in the sentence
    for the reason the 429 copy does: the reader's zone is the client's to know.

    Leaf-side twin (same code, inside the LLM family): :class:`LLMQuotaExceededError`.
    """

    code = ErrorCode.QUOTA_EXCEEDED
    status_code = 429

    def __init__(
        self,
        message: str = "",
        *,
        dimension: str = "",
        used: int = 0,
        limit: int = 0,
        reset_at: str = "",
        **kwargs,
    ):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.reset_at = reset_at
        if reset_at:
            kwargs["reset_at"] = reset_at
        super().__init__(message, dimension=dimension, used=used, limit=limit, **kwargs)


class BYOKKeyMissingError(AgentCoreError):
    """No usable BYOK LLM key is configured, so a turn cannot start.

    In BYOK billing mode every user-facing turn runs on the user's own API key;
    with none configured the turn is refused *before* the SSE opens (route preflight)
    so each client can offer its own Key-config CTA rather than getting a
    half-opened stream. 402 Payment Required fits "you must supply your own"
    billing credentials to proceed", and the ``LLM_KEY_REQUIRED`` code lets the
    client distinguish it from auth (401) / quota (429).

    Leaf-side twin (same code, inside the LLM family): :class:`LLMKeyRequiredError`.
    """

    code = ErrorCode.LLM_KEY_REQUIRED
    status_code = 402


class PlatformBillingUnavailableError(AgentCoreError):
    """User chose platform free quota but the operator key is not configured."""

    code = ErrorCode.PLATFORM_BILLING_UNAVAILABLE
    status_code = 503


class ResumeJournalDegradedError(AgentCoreError):
    """A durable pause frame survived but its ``turn_journal`` mirror did not.

    Resume cannot rebuild the CEO window from facts alone; the user must abandon the
    paused turn and start fresh rather than continuing on a silently empty context.
    """

    code = ErrorCode.STREAM_ERROR


class KeyStorageUnavailableError(AgentCoreError):
    """The server cannot store or read BYOK keys because no encryption master key
    is configured (settings.encryption_key).

    BYOK requires AES-256-GCM at-rest encryption (security.KeyEncryptor); without
    the master key the set-key endpoint refuses to store a key it could never read
    back (fail-safe: plaintext never lands on disk). 503 Service Unavailable —
    it's a server misconfiguration, not the user's fault, and is fixable by
    setting ENCRYPTION_KEY and restarting.
    """

    code = ErrorCode.KEY_STORAGE_UNAVAILABLE
    status_code = 503


class DatabaseUnavailableError(AgentCoreError):
    """Primary DB pool exhausted or database unreachable for this request.

    Maps to HTTP 503 with a stable product sentence (not a raw QueuePool /
    driver traceback). Retryable: pool pressure and brief outages clear on
    their own. Distinct from readiness: ``database_ready`` uses an isolated
    probe connection so K8s does not confuse pool exhaustion with PG down.
    """

    code = ErrorCode.DATABASE_UNAVAILABLE
    status_code = 503
    retryable = True

    def __init__(self, message: str = "AgentCore 服务暂时不可用，请稍后重试", **kwargs):
        super().__init__(message, **kwargs)


class ClientTooOldError(AgentCoreError):
    """Client build is below its platform floor (HTTP 426).

    Global floors enforced by middleware on ``/v1/*``: ``DESKTOP_MIN_VERSION``
    for ``desktop``, ``MOBILE_MIN_VERSION`` for native mobile (android / ios).
    ``mobile-web`` and admin are never gated. Empty min version / missing or
    ``dev`` client version / compare failure all fail-open (see middleware).
    Not the §7.9 per-flag ``min_client_version`` gate.
    """

    code = ErrorCode.CLIENT_TOO_OLD
    status_code = 426

    def __init__(
        self,
        message: str = "桌面端版本过旧，请更新后再试",
        *,
        min_version: str = "",
        **kwargs,
    ):
        self.min_version = min_version
        if min_version and "最低版本" not in message:
            message = f"{message}（最低版本 {min_version}）"
        super().__init__(message, min_version=min_version, **kwargs)


# Product-face fallback when an unclassified exception hits a user-facing boundary.
# Same sentence as ``message_merge.DEFAULT_FAILED_ERROR_MESSAGE`` (settle / usage).
UNCLASSIFIED_EXCEPTION_USER_MESSAGE = "模型调用失败，请稍后重试。"


def error_fields_for(
    exc: BaseException,
    *,
    fallback_code: str,
    fallback_message: str,
) -> tuple[str, str, dict | None]:
    """Decide the ``(code, message, context)`` a product-facing error should carry.

    Category gate (not string matching):
    - :class:`AgentCoreError` — pass through coded product copy on the type.
    - Sticky-dead :class:`~agentcore.workspace.protocol.WorkspaceIOError` — honest
      channel-down zh already on the exception (product text by construction).
    - Everything else (dev invariants, third-party, unclassified) — the caller's
      curated ``fallback_message``, never ``str(exc)``. Callers own that copy and
      must pass product text (an empty one degrades to
      :data:`UNCLASSIFIED_EXCEPTION_USER_MESSAGE`); the exception's own text is
      for logs.
    """
    if isinstance(exc, AgentCoreError):
        from agentcore.llm.errors import error_context_from

        return (
            exc.code,
            (exc.message or fallback_message),
            error_context_from(exc),
        )
    # Local workspace sticky-dead / presence-gate during prepare / turn gate:
    # surface the honest WorkspaceIOError text (not the generic STREAM_ERROR
    # fallback) so the UI can clear isStreaming with a clear channel-down reason.
    from agentcore.runtime.pipeline.errors import (
        LOCAL_CHANNEL_DEAD,
        is_prepare_local_abort_message,
    )
    from agentcore.workspace.limits import is_channel_dead_detail
    from agentcore.workspace.protocol import WorkspaceIOError

    if isinstance(exc, WorkspaceIOError):
        detail = str(exc).strip()
        if is_prepare_local_abort_message(detail) or is_channel_dead_detail(detail):
            return (
                ErrorCode.STREAM_ERROR,
                detail or LOCAL_CHANNEL_DEAD,
                None,
            )
    product = (fallback_message or "").strip()
    return fallback_code, product or UNCLASSIFIED_EXCEPTION_USER_MESSAGE, None
