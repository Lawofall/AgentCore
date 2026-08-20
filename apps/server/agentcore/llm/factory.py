"""LLM provider assembly."""

from __future__ import annotations

from agentcore.core.errors import ValidationError
from agentcore.llm.call_fence import observe_provider
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.platform import PlatformProvider
from agentcore.llm.provider.protocol import LLMProvider
from agentcore.llm.provider.router import ProviderRouter
from agentcore.llm.resolve import ProviderPurpose

# Sibling catalog-miss path (inference proxy ValidationError) asks the user to
# re-pick a model. Same product semantics here when credentials are absent at
# build_* (no silent platform key) — invariant text stays in details. Copy does
# not name a client page: the same sentence is sent to every client.
_MISSING_LLM_CREDENTIALS_USER_MESSAGE = (
    "当前模型不可用或凭据未就绪，请改选可用模型后再试。"
)


class MissingLLMCredentialsError(ValidationError):
    """``build_*`` called without explicit credentials (no silent platform key).

    User face is curated zh (re-pick a model). The English invariant that
    named the call site is stored under ``details["invariant"]`` for logs only.
    """

    def __init__(self, invariant: str = "", **kwargs):
        if invariant:
            kwargs.setdefault("invariant", invariant)
        super().__init__(_MISSING_LLM_CREDENTIALS_USER_MESSAGE, **kwargs)


_VENDOR_PROVIDERS: dict[str, tuple[str, str]] = {
    "kimi": ("moonshot_api_key", "moonshot_base_url"),
    "zhipu": ("zhipu_api_key", "zhipu_base_url"),
    "doubao": ("doubao_api_key", "doubao_base_url"),
}


def build_platform_provider(
    *,
    purpose: ProviderPurpose = "user_facing",
) -> LLMProvider:
    """Platform router leaf: credentials resolved per ``request.model`` (F3 一 key 一模型)."""
    _ = purpose
    return observe_provider(PlatformProvider())


def build_provider(
    credentials: LLMCredentials | None = None,
    *,
    purpose: ProviderPurpose = "user_facing",
    display_name: str | None = None,
) -> LLMProvider:
    """Build an upstream provider from resolved credentials.

    Credentials are authoritative — callers (billing gate, background resolve, BYOK
    settings probe) must pass them explicitly. ``credentials=None`` is rejected so
    user-facing paths cannot silently re-platform onto ``PLATFORM_API_KEY``.

    ``purpose`` is retained for call-site clarity only.

    Leaf ``name`` (log / pricing) stays ``credentials.source`` (``user``) or the
    platform leaf's fixed ``platform``. ``display_name`` is user-facing only:
    explicit arg → ``credentials.label`` → ``服务商``. Does **not** change
    ``credentials.source`` — turn-path pricing still binds from ``creds.source``.

    ``source=platform`` always yields :class:`PlatformProvider` (per-model key
    resolution). Pre-resolved platform ``api_key`` on ``credentials`` is not frozen
    into the leaf — the request's model id selects the key at call time so a single
    ``platform/`` router entry can serve multiple catalog models together
    (per-model credentials).

    Callers that need ambient call-level pricing should bind
    ``credential_source`` in log context (pipeline / proxy) from ``creds.source``.

    Every leaf is wrapped by :func:`observe_provider` so ``complete`` / ``stream``
    emit uniform ``llm.call`` / ``llm.call_failed`` (observation only — no retry).
    The fence also forwards leaf ``probe`` / ``probe_tools`` / ``list_models`` for
    BYOK connectivity tests.
    """
    _ = purpose  # call-site documentation only
    if credentials is None:
        raise MissingLLMCredentialsError(
            "build_provider requires explicit credentials; resolve via billing gate "
            "or platform_llm_credentials(model=…) — silent PLATFORM_API_KEY fallback "
            "is removed"
        )
    if credentials.source == "platform":
        return build_platform_provider(purpose=purpose)
    log_name = credentials.source
    shown = (display_name or "").strip() or (credentials.label or "").strip() or "服务商"
    leaf: LLMProvider = OpenAICompatibleProvider(
        name=log_name,
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        extra_headers=credentials.extra_headers,
        display_name=shown,
    )
    return observe_provider(leaf)


def _vendor_extras() -> dict[str, LLMProvider]:
    from agentcore.config import settings

    extras: dict[str, LLMProvider] = {}
    for prefix, (key_attr, url_attr) in _VENDOR_PROVIDERS.items():
        api_key = getattr(settings, key_attr, "")
        if not api_key:
            continue
        extras[prefix] = observe_provider(
            OpenAICompatibleProvider(
                name=prefix,
                api_key=api_key,
                base_url=getattr(settings, url_attr),
            )
        )
    return extras


def build_router_around(
    default: LLMProvider,
    *,
    extra_providers: dict[str, LLMProvider] | None = None,
) -> ProviderRouter:
    """Wrap ``default`` with vendor extras + optional BYOK extras (worker cross-provider)."""
    providers: dict[str, LLMProvider] = dict(_vendor_extras())
    if extra_providers:
        providers.update(extra_providers)
    return ProviderRouter(default=default, providers=providers)


async def build_turn_router(
    credentials: LLMCredentials | None = None,
    *,
    user_id: str | None = None,
    profiles: object | None = None,
    purpose: ProviderPurpose = "user_facing",
) -> ProviderRouter:
    """Build the turn ProviderRouter, injecting cross-provider / platform worker when needed.

    ``profiles.agent_provider_id`` (from ``model_selection.select_turn_profiles``)
    that differs from the turn's chat credentials causes that provider to be
    registered under its id so ``TurnProfiles.route_model_for("agent")`` can
    dispatch with a ``provider_id/model`` prefix. ``PLATFORM_PROVIDER_SENTINEL``
    registers :func:`build_platform_provider` (per-model credentials). Same-provider
    BYOK overrides need no extras.
    """
    from agentcore.db.base import async_session_factory
    from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL, TurnProfiles
    from agentcore.llm.resolve import platform_llm_credentials, resolve_provider_credentials

    if credentials is None:
        raise MissingLLMCredentialsError(
            "build_turn_router requires explicit credentials (no silent platform key)"
        )

    extras: dict[str, LLMProvider] = {}
    turn_provider_id = credentials.provider_id
    agent_provider_id = getattr(profiles, "agent_provider_id", None) if profiles else None
    if (
        isinstance(profiles, TurnProfiles)
        and agent_provider_id
        and agent_provider_id != turn_provider_id
    ):
        if agent_provider_id == PLATFORM_PROVIDER_SENTINEL:
            worker_model = profiles.model_for("agent")
            if platform_llm_credentials(model=worker_model) is not None:
                extras[PLATFORM_PROVIDER_SENTINEL] = build_platform_provider(purpose=purpose)
        elif user_id:
            async with async_session_factory() as session:
                agent_creds = await resolve_provider_credentials(
                    session, user_id, agent_provider_id
                )
            if agent_creds is not None:
                extras[agent_provider_id] = build_provider(agent_creds, purpose=purpose)
    return build_router_around(
        build_provider(credentials, purpose=purpose),
        extra_providers=extras or None,
    )


def build_router(
    credentials: LLMCredentials | None = None,
    *,
    purpose: ProviderPurpose = "user_facing",
) -> ProviderRouter:
    if credentials is None:
        raise MissingLLMCredentialsError(
            "build_router requires explicit credentials (no silent platform key)"
        )
    return build_router_around(build_provider(credentials, purpose=purpose))


def spawn_independent_llm(llm: LLMProvider) -> tuple[LLMProvider, bool]:
    """Spawn a client the coordination background drive owns and must close.

    Returns ``(client, owns)``. Production routers / OpenAI-compatible providers
    are cloned so turn teardown ``llm.close()`` cannot ReadError-kill workers.
    Test fakes without ``clone`` are returned as-is with ``owns=False``.
    """
    clone_fn = getattr(llm, "clone", None)
    if callable(clone_fn):
        return clone_fn(), True
    return llm, False
