"""Unit tests for the sidecar cloud inference proxy (双模式工作区 §一.1 / Slice 4a).

The proxy is the single choke point that lets a local sidecar reach DeepSeek
WITHOUT the platform key on the user's machine, runs the same spend gate as a
cloud turn, and meters real usage server-side (so platform billing can't be
under-reported by the client). Collaborators are faked (mirroring
``test_local_turn``) so the control flow is asserted without a DB / real LLM; the
HTTP forwarding is driven through ``httpx.MockTransport``.

Covered:

* token mint → decode roundtrip, and that an ``access`` token is refused as the
  wrong type (the two token kinds can never be confused);
* the bearer dependency rejects a missing / wrong-type token and resolves a valid one;
* the spend gate resolves BYOK vs platform credentials and refuses correctly;
* a proxied call's real usage is priced + recorded under the conversation
  (message_id NULL), skipped when the conversation header is absent, and a ledger
  failure never escapes;
* unary + streaming forwarding pass the upstream body/status through and tee the
  final usage to record spend exactly once.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agentcore.api.routes import inference
from agentcore.core.errors import (
    AuthenticationError,
    BYOKKeyMissingError,
    LLMUpstreamError,
    QuotaExceededError,
    ValidationError,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    ToolCall,
    ToolCallFunction,
)
from agentcore.security import create_access_token, create_inference_token

pytestmark = pytest.mark.anyio


# --- token mint / verify -----------------------------------------------------


async def test_mint_inference_token_includes_resolved_model(monkeypatch):
    """No conversation_id → account default (resolve_user_chat_model); JWT stays user-only."""

    async def _fake_resolve(_session, user_id):
        assert user_id == "u1"
        return "deepseek-v4-flash"

    monkeypatch.setattr(
        inference.token,
        "resolve_user_chat_model",
        _fake_resolve,
    )

    async def _noop_rate_limit(_user_id):
        return None

    monkeypatch.setattr(
        inference.token,
        "enforce_inference_token_mint_rate_limit",
        _noop_rate_limit,
    )
    user = SimpleNamespace(user_id="u1")
    resp = await inference.token.mint_inference_token(user, session=None)
    assert resp.model == "deepseek-v4-flash"
    assert resp.expires_in_sec > 0
    assert resp.token
    # JWT must not embed conversation — decode still yields only the user id.
    assert inference.decode_inference_token(resp.token) == "u1"


async def test_mint_inference_token_uses_conversation_main_model(monkeypatch):
    """Valid owned conversation_id → same main slot as proxy expand."""

    async def _fake_account(_session, user_id):
        raise AssertionError("must not fall back to account default when conv is owned")

    async def _fake_conv_selection(_session, conv, user_id):
        assert user_id == "u1"
        assert conv.id == "c-pinned"
        return SimpleNamespace(model="deepseek-v4-flash-free", origin="platform", provider_id=None)

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, cid, *, user_id):
            assert user_id == "u1"
            assert cid == "c-pinned"
            return SimpleNamespace(id=cid, assembly_id="pinned")

    monkeypatch.setattr(inference.token, "resolve_user_chat_model", _fake_account)
    monkeypatch.setattr(
        inference.token,
        "resolve_conversation_model_selection",
        _fake_conv_selection,
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _ConvRepo,
    )

    async def _noop_rate_limit(_user_id):
        return None

    monkeypatch.setattr(
        inference.token,
        "enforce_inference_token_mint_rate_limit",
        _noop_rate_limit,
    )
    body = inference.token.InferenceTokenRequest(conversation_id="c-pinned")
    resp = await inference.token.mint_inference_token(
        SimpleNamespace(user_id="u1"), session=None, body=body
    )
    # Honest id — do not silent-collapse flash-free → flash.
    assert resp.model == "deepseek-v4-flash-free"
    assert inference.decode_inference_token(resp.token) == "u1"


async def test_mint_inference_token_unknown_conversation_falls_back(monkeypatch):
    """Missing / foreign conversation_id → account default (unchanged path)."""

    async def _fake_resolve(_session, user_id):
        assert user_id == "u1"
        return "deepseek-v4-flash"

    class _ConvRepo:
        def __init__(self, _session):
            pass

        async def get_by_id(self, _cid, *, user_id):
            assert user_id == "u1"
            return None

    monkeypatch.setattr(inference.token, "resolve_user_chat_model", _fake_resolve)
    monkeypatch.setattr(
        "agentcore.db.repositories.ConversationRepository",
        _ConvRepo,
    )

    async def _noop_rate_limit(_user_id):
        return None

    monkeypatch.setattr(
        inference.token,
        "enforce_inference_token_mint_rate_limit",
        _noop_rate_limit,
    )
    body = inference.token.InferenceTokenRequest(conversation_id="missing")
    resp = await inference.token.mint_inference_token(
        SimpleNamespace(user_id="u1"), session=None, body=body
    )
    assert resp.model == "deepseek-v4-flash"


def test_inference_token_roundtrip():
    token = create_inference_token("user-1")
    assert inference.decode_inference_token(token) == "user-1"


def test_inference_token_rejects_access_token():
    """An access token must NOT authorize the inference proxy (wrong type)."""
    access = create_access_token("user-1", audience="product")
    with pytest.raises(AuthenticationError):
        inference.decode_inference_token(access)


def test_inference_token_rejects_expired():
    expired = create_inference_token("user-1", expires_delta=timedelta(minutes=-1))
    with pytest.raises(AuthenticationError):
        inference.decode_inference_token(expired)


def test_access_decode_rejects_inference_token():
    """Symmetry: the cookie API's decoder must also refuse an inference token."""
    from agentcore.security import decode_access_token

    token = create_inference_token("user-1")
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


# --- bearer auth dependency --------------------------------------------------


class _FakeUserRepo:
    def __init__(self, user):
        self._user = user

    async def get_by_id(self, _user_id):
        return self._user


async def test_inference_user_resolves_valid_token():
    user = SimpleNamespace(user_id="u1", status="active")
    resolved = await inference.inference_user(
        authorization=f"Bearer {create_inference_token('u1')}",
        user_repo=_FakeUserRepo(user),
    )
    assert resolved is user


async def test_inference_user_rejects_missing_header():
    with pytest.raises(AuthenticationError):
        await inference.inference_user(authorization=None, user_repo=_FakeUserRepo(None))


async def test_inference_user_rejects_access_token():
    with pytest.raises(AuthenticationError):
        await inference.inference_user(
            authorization=f"Bearer {create_access_token('u1', audience='product')}",
            user_repo=_FakeUserRepo(SimpleNamespace(user_id="u1", status="active")),
        )


async def test_inference_user_rejects_inactive_user():
    with pytest.raises(AuthenticationError):
        await inference.inference_user(
            authorization=f"Bearer {create_inference_token('u1')}",
            user_repo=_FakeUserRepo(SimpleNamespace(user_id="u1", status="disabled")),
        )


# --- credential resolution + spend gate --------------------------------------


async def test_resolve_credentials_byok_returns_user_key(monkeypatch):
    async def _fake_preflight(**_kwargs):
        return LLMCredentials(
            api_key="sk-user",
            base_url="https://api.deepseek.com",
            default_model="deepseek-v4-flash",
        )

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="deepseek-v4-flash", origin="byok", provider_id=None
            )
        ),
    )
    cfg = await inference._resolve_inference_credentials(
        None, None, SimpleNamespace(user_id="u1")
    )
    assert cfg.api_key == "sk-user"
    assert cfg.source == "byok"


async def test_resolve_credentials_byok_missing_key_refuses(monkeypatch):
    async def _fake_preflight(**_kwargs):
        raise BYOKKeyMissingError("missing key")

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="deepseek-v4-flash", origin="byok", provider_id=None
            )
        ),
    )
    with pytest.raises(BYOKKeyMissingError):
        await inference._resolve_inference_credentials(
            None, None, SimpleNamespace(user_id="u1")
        )


async def test_resolve_credentials_platform_enforces_quota_then_uses_global(monkeypatch):
    monkeypatch.setattr(inference.settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(inference.settings, "platform_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(inference.settings, "platform_model", "deepseek-v4-flash")
    seen: dict = {}

    async def _fake_preflight(
        *, session, user, cost_repo, byok_missing_message, model_origin, provider_id
    ):
        seen["user_id"] = user.user_id
        seen["cost_repo"] = cost_repo
        seen["model_origin"] = model_origin
        return None

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="deepseek-v4-flash", origin="platform", provider_id=None
            )
        ),
    )
    cfg = await inference._resolve_inference_credentials(
        None, "COST_REPO", SimpleNamespace(user_id="u1")
    )
    assert cfg.api_key == "sk-platform"
    assert cfg.source == "platform"
    assert seen == {"user_id": "u1", "cost_repo": "COST_REPO", "model_origin": "platform"}


async def test_resolve_credentials_platform_quota_exceeded_propagates(monkeypatch):
    async def _fake_preflight(**_kwargs):
        raise QuotaExceededError("over budget")

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="deepseek-v4-flash", origin="platform", provider_id=None
            )
        ),
    )
    with pytest.raises(QuotaExceededError):
        await inference._resolve_inference_credentials(
            None, None, SimpleNamespace(user_id="u1")
        )


def test_explicit_selection_from_requested_parses_route_keys():
    from agentcore.api.routes.inference.proxy import _explicit_selection_from_requested

    assert _explicit_selection_from_requested(None) is None
    assert _explicit_selection_from_requested("deepseek-v4-flash") is None
    assert _explicit_selection_from_requested("doubao/ep-123") is None  # vendor, not identity

    plat = _explicit_selection_from_requested("platform/glm-5.2")
    assert plat is not None
    assert (plat.model, plat.origin, plat.provider_id) == ("glm-5.2", "platform", None)

    byok = _explicit_selection_from_requested("prov-1/openrouter/auto")
    assert byok is not None
    assert (byok.model, byok.origin, byok.provider_id) == (
        "openrouter/auto",
        "byok",
        "prov-1",
    )


async def test_resolve_member_explicit_route_key_overrides_worker_slot(monkeypatch):
    """member + catalog route key → resolve that identity, not the Worker slot."""
    seen: dict = {}

    async def _fake_preflight(**kw):
        seen["origin"] = kw["model_origin"]
        seen["provider_id"] = kw["provider_id"]
        return LLMCredentials(
            api_key="sk-node",
            base_url="https://api.example.com",
            default_model="node-model",
        )

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="main-model", origin="byok", provider_id="prov-main"
            )
        ),
    )
    worker_mock = AsyncMock(
        return_value=SimpleNamespace(
            model="worker-slot", origin="byok", provider_id="prov-worker"
        )
    )
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_worker_selection", worker_mock
    )
    monkeypatch.setattr(
        "agentcore.llm.catalog.validate_model_choice",
        AsyncMock(return_value=True),
    )

    cfg = await inference._resolve_inference_credentials(
        None,
        None,
        SimpleNamespace(user_id="u1"),
        cost_role="member",
        requested_model="prov-node/node-model",
    )
    assert cfg.model == "node-model"
    assert cfg.api_key == "sk-node"
    assert seen == {"origin": "byok", "provider_id": "prov-node"}
    worker_mock.assert_not_awaited()


async def test_resolve_member_bare_model_follows_worker_slot(monkeypatch):
    """member + bare mint/chat model → still follow the composite Worker slot."""
    seen: dict = {}

    async def _fake_preflight(**kw):
        seen["origin"] = kw["model_origin"]
        seen["provider_id"] = kw["provider_id"]
        return LLMCredentials(
            api_key="sk-worker",
            base_url="https://api.example.com",
            default_model="worker-slot",
        )

    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", _fake_preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="main-model", origin="byok", provider_id="prov-main"
            )
        ),
    )
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_worker_selection",
        AsyncMock(
            return_value=SimpleNamespace(
                model="worker-slot", origin="byok", provider_id="prov-worker"
            )
        ),
    )
    validate = AsyncMock(return_value=True)
    monkeypatch.setattr("agentcore.llm.catalog.validate_model_choice", validate)

    cfg = await inference._resolve_inference_credentials(
        None,
        None,
        SimpleNamespace(user_id="u1"),
        cost_role="member",
        requested_model="main-model",  # bare mint echo — not an identity route key
    )
    assert cfg.model == "worker-slot"
    assert seen == {"origin": "byok", "provider_id": "prov-worker"}
    validate.assert_not_awaited()


async def test_resolve_member_illegal_explicit_hard_fails(monkeypatch):
    """member + illegal route key → ValidationError; never silent-fallback to Worker."""
    preflight = AsyncMock(
        return_value=LLMCredentials(
            api_key="sk", base_url="https://x", default_model="x"
        )
    )
    monkeypatch.setattr(inference.proxy, "preflight_llm_credentials", preflight)
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_default_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model="main-model", origin="byok", provider_id="prov-main"
            )
        ),
    )
    worker_mock = AsyncMock(
        return_value=SimpleNamespace(
            model="worker-slot", origin="byok", provider_id="prov-worker"
        )
    )
    monkeypatch.setattr(
        "agentcore.llm.resolve.resolve_account_worker_selection", worker_mock
    )
    monkeypatch.setattr(
        "agentcore.llm.catalog.validate_model_choice",
        AsyncMock(return_value=False),
    )

    with pytest.raises(ValidationError, match="节点模型不可用"):
        await inference._resolve_inference_credentials(
            None,
            None,
            SimpleNamespace(user_id="u1"),
            cost_role="member",
            requested_model="prov-bad/wild-model",
        )
    preflight.assert_not_awaited()
    worker_mock.assert_not_awaited()


async def test_resolve_vision_role_retired():
    """cost_role=vision no longer expands a slot / VISION_* fallback."""
    with pytest.raises(ValidationError, match="读图角色已退役"):
        await inference._resolve_inference_credentials(
            None, None, SimpleNamespace(user_id="u1"), cost_role="vision"
        )


# --- authoritative metering --------------------------------------------------


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _capture_record_runs(monkeypatch, tmp_path, *, raises: bool = False):
    """Enqueue → drain against a fake ledger; capture record_calls kwargs."""
    from agentcore.billing import cost_ledger_queue as ledger_mod
    from agentcore.billing import proxy_spend_queue as queue_mod

    calls: list = []
    queue = queue_mod.reset_proxy_spend_queue_for_tests()
    monkeypatch.setattr(ledger_mod.settings, "data_dir", str(tmp_path))

    class _FakeCostRepo:
        def __init__(self, _session):
            pass

        async def record_calls(self, **kw):
            if raises:
                raise RuntimeError("ledger boom")
            calls.append(kw)
            return len(kw.get("calls") or [])

        async def record_runs(self, **kw):
            if raises:
                raise RuntimeError("ledger boom")
            calls.append(kw)
            return len(kw.get("runs") or [])

    monkeypatch.setattr(queue_mod, "telemetry_session_factory", lambda: _FakeSession(), raising=False)
    # Drain imports these lazily from agentcore.db.*; patch at the source modules.
    monkeypatch.setattr(
        "agentcore.db.base.telemetry_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.CostEventRepository",
        _FakeCostRepo,
    )
    return calls, queue


async def test_record_proxy_spend_prices_and_records(monkeypatch, tmp_path):
    calls, queue = _capture_record_runs(monkeypatch, tmp_path)
    usage = inference.usage_from_deepseek(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_cache_hit_tokens": 400,
            "prompt_cache_miss_tokens": 600,
        }
    )
    await inference._record_proxy_spend(
        user_id="u1", conversation_id="c1", model="deepseek-v4-flash", usage=usage
    )
    assert await queue.drain_once() == 1

    assert len(calls) == 1
    kw = calls[0]
    assert kw["user_id"] == "u1"
    assert kw["conversation_id"] == "c1"
    # Off-turn shape: lands in account/conversation totals, out of per-message payroll.
    assert kw["message_id"] is None
    assert kw["materialize_runs"] is True
    (row,) = kw["calls"]
    assert row["role"] == inference.ROLE_CAPTAIN
    # Priced server-side off the real usage (not trusted from the client).
    assert row["cost_total_nano"] > 0
    assert row["tokens"]["input"] == 1000


async def test_record_proxy_spend_carries_attribution(monkeypatch, tmp_path):
    calls, queue = _capture_record_runs(monkeypatch, tmp_path)
    usage = inference.usage_from_deepseek({"prompt_tokens": 10, "completion_tokens": 1})
    await inference._record_proxy_spend(
        user_id="u1",
        conversation_id="c1",
        model="deepseek-v4-flash",
        usage=usage,
        message_id="msg-sidecar-1",
        run_id="del_abc_0",
        agent_id="del_abc_0",
        role="member",
        persona="调研员",
        call_id="call_stable_1",
    )
    assert await queue.drain_once() == 1
    assert len(calls) == 1
    assert calls[0]["message_id"] == "msg-sidecar-1"
    row = calls[0]["calls"][0]
    assert row["run_id"] == "del_abc_0"
    assert row["role"] == "member"
    assert row["persona"] == "调研员"
    assert row["call_id"] == "call_stable_1"


async def test_record_proxy_spend_skips_vision_role(monkeypatch, tmp_path):
    """Vision is billed via cost_runs orphans, not proxy cost_calls (call_meter 同闸)."""
    calls, queue = _capture_record_runs(monkeypatch, tmp_path)
    await inference._record_proxy_spend(
        user_id="u1",
        conversation_id="c1",
        model="qwen-vl-max",
        usage=inference.usage_from_deepseek({"prompt_tokens": 900, "completion_tokens": 40}),
        message_id="msg-1",
        run_id="vis_should_not_land",
        role="vision",
    )
    assert await queue.drain_once() == 0
    assert calls == []


async def test_record_proxy_spend_bills_without_conversation(monkeypatch, tmp_path):
    """A missing conversation header no longer drops the spend (「放宽账本」).

    The ledger column used to be NOT NULL, so this call was discarded outright —
    real tokens, no record. It now lands as an account-level row (NULL
    conversation, only ``user_id``), so the money still reaches 用量页 / 额度. The
    header gap stays observable via ``inference.proxy_spend_no_conversation``: a
    sidecar turn should always carry one, and losing it is a bug worth seeing.
    """
    calls, queue = _capture_record_runs(monkeypatch, tmp_path)
    await inference._record_proxy_spend(
        user_id="u1",
        conversation_id=None,
        model="deepseek-v4-flash",
        usage=inference.usage_from_deepseek({"prompt_tokens": 10, "completion_tokens": 1}),
    )
    assert await queue.drain_once() == 1

    assert len(calls) == 1
    kw = calls[0]
    assert kw["user_id"] == "u1"
    assert kw["conversation_id"] is None
    assert kw["message_id"] is None
    (row,) = kw["calls"]
    assert row["cost_total_nano"] > 0


async def test_record_proxy_spend_enqueue_survives_ledger_failure(monkeypatch, tmp_path):
    """Ledger failure on drain must not raise into the already-streamed response path.

    Enqueue itself succeeds; drain leaves the outbox row for retry (at-least-once).
    """
    _calls, queue = _capture_record_runs(monkeypatch, tmp_path, raises=True)
    await inference._record_proxy_spend(
        user_id="u1",
        conversation_id="c1",
        model="deepseek-v4-flash",
        usage=inference.usage_from_deepseek({"prompt_tokens": 10, "completion_tokens": 1}),
    )
    assert await queue.drain_once() == 0  # failed write → row retained
    assert await queue._ledger._backend.pending_count() == 1


def test_usage_from_deepseek_maps_fields():
    usage = inference.usage_from_deepseek(
        {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 4},
            "prompt_cache_hit_tokens": 10,
            "prompt_cache_miss_tokens": 20,
        }
    )
    assert usage.input_tokens == 30
    assert usage.output_tokens == 12
    assert usage.reasoning_tokens == 4
    assert usage.cache_hit_tokens == 10
    assert usage.cache_miss_tokens == 20


def test_usage_from_openai_cached_tokens_derives_split():
    """OpenAI dialect: ``prompt_tokens_details.cached_tokens`` is the hit portion;
    the miss is derived as prompt − cached (TokenUsage.from_openai_wire) — without
    this an upstream that only speaks OpenAI usage reports 0 cache hits forever."""
    usage = inference.usage_from_deepseek(
        {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 10},
        }
    )
    assert usage.cache_hit_tokens == 10
    assert usage.cache_miss_tokens == 20


def test_usage_deepseek_split_precedes_openai_cached_tokens():
    """Both dialects present → the explicit DeepSeek split is authoritative."""
    usage = inference.usage_from_deepseek(
        {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "prompt_cache_hit_tokens": 5,
            "prompt_cache_miss_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 10},
        }
    )
    assert usage.cache_hit_tokens == 5
    assert usage.cache_miss_tokens == 25


def test_usage_from_openai_tolerates_null_and_overlong_cached():
    """``null`` fields from lenient proxies parse as 0; a cached count exceeding
    prompt_tokens clamps the derived miss at 0 instead of going negative."""
    usage = inference.usage_from_deepseek(
        {
            "prompt_tokens": 30,
            "completion_tokens": None,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    )
    assert usage.output_tokens == 0
    assert usage.cache_hit_tokens == 40
    assert usage.cache_miss_tokens == 0


# --- forwarding (httpx.MockTransport) ----------------------------------------


def _provider(handler) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        name="test", api_key="k", base_url="http://upstream/v1"
    )
    provider._client = httpx.AsyncClient(
        base_url="http://upstream/v1", transport=httpx.MockTransport(handler)
    )
    return provider


def _request(model: str = "deepseek-v4-flash", *, stream: bool = False) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        stream=stream,
    )


async def test_llm_request_from_payload_uses_server_resolved_model():
    from agentcore.billing.call_meter import PROXY_LLM_SCENARIO
    from agentcore.llm.resolve import ModelConfig

    req = inference.proxy._llm_request_from_payload(
        {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        },
        ModelConfig(
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="sk",
            source="byok",
            purpose="chat",
        ),
    )
    assert req.model == "deepseek-v4-flash"
    assert req.scenario == PROXY_LLM_SCENARIO


async def test_forward_unary_passes_through_and_records(monkeypatch):
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "prompt_cache_hit_tokens": 7,
                    "prompt_cache_miss_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    provider = _provider(_handler)
    resp = await inference._forward_unary(
        provider, _request(), user_id="u1", conversation_id="c1"
    )

    assert resp.status_code == 200
    assert b'"content": "hi"' in resp.body
    body = json.loads(resp.body)
    usage = body["usage"]
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 5
    assert usage["prompt_cache_hit_tokens"] == 7
    assert usage["prompt_cache_miss_tokens"] == 3
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 2
    assert len(spend) == 1
    assert spend[0]["conversation_id"] == "c1"
    assert spend[0]["model"] == "deepseek-v4-flash"
    assert spend[0]["usage"].output_tokens == 5
    assert spend[0]["usage"].cache_hit_tokens == 7
    assert spend[0]["usage"].reasoning_tokens == 2


async def test_forward_unary_passes_error_status_through(monkeypatch):
    spend: list = []
    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "insufficient balance"})

    provider = _provider(_handler)
    resp = await inference._forward_unary(
        provider, _request(), user_id="u1", conversation_id="c1"
    )

    assert resp.status_code == 502
    body = resp.body.decode()
    assert "余额" in body or "LLM_INSUFFICIENT_BALANCE" in body


async def test_forward_stream_relays_and_records(monkeypatch):
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        async def _body():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield (
                b'data: {"choices":[{"delta":{}}],'
                b'"usage":{"prompt_tokens":10,"completion_tokens":5,'
                b'"prompt_cache_hit_tokens":7,"prompt_cache_miss_tokens":3,'
                b'"completion_tokens_details":{"reasoning_tokens":2}},'
                b'"model":"deepseek-v4-flash"}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_body())

    provider = _provider(_handler)
    resp = await inference._forward_stream(
        provider, _request(stream=True), user_id="u1", conversation_id="c1"
    )

    collected = ""
    async for chunk in resp.body_iterator:
        collected += chunk

    # The content delta + DONE sentinel are relayed verbatim to the sidecar.
    assert '"content": "hi"' in collected or '"content":"hi"' in collected
    assert "[DONE]" in collected
    assert '"prompt_cache_hit_tokens": 7' in collected or '"prompt_cache_hit_tokens":7' in collected
    assert '"prompt_cache_miss_tokens": 3' in collected or '"prompt_cache_miss_tokens":3' in collected
    assert '"reasoning_tokens": 2' in collected or '"reasoning_tokens":2' in collected
    # The final usage chunk is teed → spend recorded once, after the stream ends.
    assert len(spend) == 1
    assert spend[0]["usage"].output_tokens == 5
    assert spend[0]["usage"].cache_hit_tokens == 7
    assert spend[0]["usage"].reasoning_tokens == 2
    assert spend[0]["model"] == "deepseek-v4-flash"


# --- trace stitching (打通气泡↔日志) -----------------------------------------


async def test_record_proxy_spend_binds_trace_into_log_context(monkeypatch, tmp_path):
    """Drain must rebind the turn's trace_id so a streamed call's deferred ledger
    write (relay teardown, after the route's log scope exited) still joins the
    turn's trace end-to-end.
    """
    from agentcore.billing import cost_ledger_queue as ledger_mod
    from agentcore.billing import proxy_spend_queue as queue_mod
    from agentcore.core.log_context import get_log_value

    seen: dict = {}
    queue = queue_mod.reset_proxy_spend_queue_for_tests()
    monkeypatch.setattr(ledger_mod.settings, "data_dir", str(tmp_path))

    class _FakeCostRepo:
        def __init__(self, _session):
            pass

        async def record_calls(self, **_kw):
            seen["trace_id"] = get_log_value("trace_id")
            seen["conversation_id"] = get_log_value("conversation_id")
            return 1

        async def record_runs(self, **_kw):
            return 0

    monkeypatch.setattr(
        "agentcore.db.base.telemetry_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.db.repositories.CostEventRepository",
        _FakeCostRepo,
    )

    await inference._record_proxy_spend(
        user_id="u1",
        conversation_id="c1",
        model="deepseek-v4-flash",
        usage=inference.usage_from_deepseek({"prompt_tokens": 10, "completion_tokens": 1}),
        trace_id="trace-xyz",
    )
    assert await queue.drain_once() == 1
    assert seen == {"trace_id": "trace-xyz", "conversation_id": "c1"}


async def test_forward_unary_threads_trace_id(monkeypatch):
    """The unary path forwards the turn's trace_id to the spend recorder."""
    spend: list = []
    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    provider = _provider(_handler)
    await inference._forward_unary(
        provider, _request("m"), user_id="u1", conversation_id="c1", trace_id="t-abc"
    )
    assert spend and spend[0]["trace_id"] == "t-abc"


async def test_forward_stream_upstream_error_returns_502(monkeypatch):
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    class _FailingProvider:
        async def stream(self, _request):
            from agentcore.core.errors import LLMUpstreamError

            raise LLMUpstreamError(
                "上游模型服务暂时不可用（503），请稍后再试",
                upstream_status=503,
            )
            yield  # pragma: no cover — makes this an async generator

        async def close(self):
            pass

    resp = await inference._forward_stream(
        _FailingProvider(),
        _request(stream=True),
        user_id="u1",
        conversation_id="c1",
    )

    assert resp.status_code == 502
    assert resp.headers.get("x-upstream-retried") == "3"
    body = resp.body.decode()
    assert "error" in body
    assert spend == []


async def test_forward_stream_client_error_surfaces_upstream_message(monkeypatch):
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    class _FailingProvider:
        async def stream(self, _request):
            from agentcore.core.errors import LLMError

            raise LLMError(
                "user The `reasoning_content` in the thinking mode must be passed back.",
                upstream_status=400,
                upstream_body_preview='{"error":{"message":"reasoning_content required"}}',
            )
            yield  # pragma: no cover

        async def close(self):
            pass

    resp = await inference._forward_stream(
        _FailingProvider(),
        _request(stream=True),
        user_id="u1",
        conversation_id="c1",
    )

    assert resp.status_code == 502
    body = resp.body.decode()
    assert "reasoning_content" in body
    assert "上游推理服务不可达" not in body
    assert spend == []


async def test_provider_skips_retry_when_proxy_already_retried():
    attempts: list[int] = []

    def _handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            502,
            headers={"X-Upstream-Retried": "3"},
            json={"error": {"message": "upstream failed"}},
        )

    provider = _provider(_handler)
    with pytest.raises(LLMUpstreamError):
        async for _line in provider.stream(_request(stream=True)):
            pass

    assert len(attempts) == 1


async def test_forward_stream_threads_trace_id(monkeypatch):
    """The streamed path forwards the turn's trace_id to the deferred spend recorder."""
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        async def _body():
            yield (
                b'data: {"choices":[{"delta":{}}],'
                b'"usage":{"prompt_tokens":1,"completion_tokens":1},"model":"m"}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_body())

    provider = _provider(_handler)
    resp = await inference._forward_stream(
        provider,
        _request("m", stream=True),
        user_id="u1",
        conversation_id="c1",
        trace_id="t-stream",
    )
    async for _chunk in resp.body_iterator:
        pass

    assert spend and spend[0]["trace_id"] == "t-stream"


# --- tool-loop fidelity (保真: proxy must forward the full tool shape both ways) ----
#
# The proxy hand-rolls dict↔domain mapping on BOTH boundaries (inbound request rebuild
# + outbound SSE/JSON re-serialization), parallel to the provider's own _build_payload /
# stream parsing. That duplication silently dropped tool_calls / tool_call_id /
# reasoning_content, so ANY proxied multi-turn tool call broke: DeepSeek's thinking-mode
# tool contract 400s when the echoed assistant/tool shape is incomplete, and a streamed
# round-1 tool call never even reached the sidecar. These tests pin BOTH directions.


def _cfg():
    from agentcore.llm.resolve import ModelConfig

    return ModelConfig(
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="sk",
        source="byok",
        purpose="chat",
    )


def test_llm_request_from_payload_preserves_tool_call_messages():
    """(a) Request parse fidelity: a round-2 window rebuilds the FULL assistant/tool
    field set — tool_calls (as ToolCall/ToolCallFunction), tool_call_id,
    reasoning_content — not just role+content."""
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "search AgentCore"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "I should search first.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"q": "AgentCore"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "top hits ..."},
        ],
    }
    req = inference.proxy._llm_request_from_payload(payload, _cfg())

    assistant = req.messages[1]
    assert assistant.role == "assistant"
    assert assistant.reasoning_content == "I should search first."
    assert assistant.tool_calls is not None and len(assistant.tool_calls) == 1
    assert assistant.tool_calls[0].id == "call_1"
    assert assistant.tool_calls[0].function.name == "web_search"
    assert assistant.tool_calls[0].function.arguments == '{"q": "AgentCore"}'

    tool_msg = req.messages[2]
    assert tool_msg.role == "tool"
    assert tool_msg.tool_call_id == "call_1"
    assert tool_msg.content == "top hits ..."


def test_llm_request_from_payload_preserves_thinking_blocks():
    payload = {
        "model": "claude-haiku-4-5",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "plan",
                "thinking_blocks": [
                    {
                        "type": "thinking",
                        "thinking": "plan",
                        "signature": "sig_keep",
                    }
                ],
            }
        ],
    }
    req = inference.proxy._llm_request_from_payload(payload, _cfg())
    assert req.messages[0].thinking_blocks == [
        {"type": "thinking", "thinking": "plan", "signature": "sig_keep"}
    ]


def test_llm_request_from_payload_is_build_payload_inverse():
    """Ratchet: the proxy's request parse is the faithful INVERSE of the provider's
    _build_payload. Round-tripping a tool-loop window through build∘parse∘build must be
    byte-stable (== build), so any future field silently dropped on the inbound rebuild
    (the original P0) re-breaks this test instead of shipping."""
    provider = OpenAICompatibleProvider(name="t", api_key="k", base_url="http://x/v1")
    original = LLMRequest(
        messages=[
            LLMMessage(role="system", content="be brief"),
            LLMMessage(role="user", content="use a tool"),
            LLMMessage(
                role="assistant",
                content="",
                reasoning_content="thinking about it",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=ToolCallFunction(name="web_search", arguments='{"q":"x"}'),
                    )
                ],
            ),
            LLMMessage(role="tool", content="tool output", tool_call_id="call_1"),
            # An assistant tool-call turn whose model omitted reasoning: _build_payload
            # emits reasoning_content="" (DeepSeek echo rule); parse must keep it stable.
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        function=ToolCallFunction(name="finish", arguments="{}"),
                    )
                ],
            ),
            LLMMessage(role="tool", content="ok", tool_call_id="call_2"),
        ],
        model="deepseek-v4-flash",
    )

    wire1 = provider._build_payload(original, stream=False)
    parsed = inference.proxy._llm_request_from_payload(wire1, _cfg())
    wire2 = provider._build_payload(parsed, stream=False)

    assert wire1["messages"] == wire2["messages"]


def test_llm_request_from_payload_profile_effort_wins():
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "max",
        "thinking": {"type": "enabled"},
    }
    req = inference.proxy._llm_request_from_payload(
        payload, _cfg(), reasoning_effort="low"
    )
    assert req.reasoning_effort == "low"
    cleared = inference.proxy._llm_request_from_payload(
        payload, _cfg(), reasoning_effort=None
    )
    assert cleared.reasoning_effort is None
    copied = inference.proxy._llm_request_from_payload(payload, _cfg())
    assert copied.reasoning_effort == "max"


async def test_forward_unary_round2_delivers_full_tool_shape_upstream(monkeypatch):
    """(a) End-to-end request fidelity: a round-2 window parsed by the proxy must reach
    the upstream (DeepSeek) with the complete tool shape — the exact bytes the
    thinking-mode tool contract requires, so the call no longer 400s."""

    async def _fake_spend(**_kw):
        pass

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "thinking",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"q":"x"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "hits"},
        ],
    }
    req = inference.proxy._llm_request_from_payload(payload, _cfg())
    provider = _provider(_handler)
    await inference._forward_unary(provider, req, user_id="u1", conversation_id="c1")

    sent = captured["body"]["messages"]
    assistant = sent[1]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "web_search"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"q":"x"}'
    assert assistant["reasoning_content"] == "thinking"
    tool_msg = sent[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"] == "hits"


async def test_forward_stream_relays_tool_call_deltas(monkeypatch):
    """(b) Streaming relay fidelity: DeepSeek streams a tool call as OpenAI
    delta.tool_calls[] fragments; the proxy must relay them (index/id/function.name/
    arguments) so the sidecar's stream parser reconstructs the call. Pre-fix the proxy
    dropped them, so a proxied tool call never surfaced and delegate/debate couldn't
    start. Proven by re-parsing the proxy's own relayed bytes with the sidecar's
    provider and accumulating the deltas exactly like the engine does."""
    spend: list = []

    async def _fake_spend(**kw):
        spend.append(kw)

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        async def _body():
            # First fragment: id + function name + partial arguments.
            yield (
                b'data: {"choices":[{"index":0,"delta":{"tool_calls":['
                b'{"index":0,"id":"call_1","type":"function",'
                b'"function":{"name":"web_search","arguments":"{\\"q\\":"}}]}}]}\n\n'
            )
            # Continuation fragment: only the trailing arguments (no id/name).
            yield (
                b'data: {"choices":[{"index":0,"delta":{"tool_calls":['
                b'{"index":0,"function":{"arguments":"\\"AgentCore\\"}"}}]}}]}\n\n'
            )
            yield (
                b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],'
                b'"usage":{"prompt_tokens":10,"completion_tokens":5},'
                b'"model":"deepseek-v4-flash"}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_body())

    provider = _provider(_handler)
    resp = await inference._forward_stream(
        provider, _request(stream=True), user_id="u1", conversation_id="c1"
    )
    collected = ""
    async for chunk in resp.body_iterator:
        collected += chunk

    # Re-parse the proxy's relayed SSE with the SAME provider the sidecar runs and
    # accumulate the tool-call deltas exactly like stream_llm_round → the relayed bytes
    # must reconstruct the original call end-to-end.
    replay = _provider(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=collected.encode(),
        )
    )
    acc = {"id": "", "name": "", "arguments": ""}
    async for chunk in replay.stream(_request(stream=True)):
        for tc in chunk.delta_tool_calls or []:
            if tc.id:
                acc["id"] = tc.id
            if tc.function_name:
                acc["name"] = tc.function_name
            if tc.arguments_delta:
                acc["arguments"] += tc.arguments_delta

    assert acc == {"id": "call_1", "name": "web_search", "arguments": '{"q":"AgentCore"}'}
    assert len(spend) == 1
    assert spend[0]["usage"].output_tokens == 5


async def test_forward_unary_includes_reasoning_content(monkeypatch):
    """A unary tool-call response carries BOTH tool_calls and reasoning_content, so a
    client echoing the turn on the next round satisfies DeepSeek's thinking-mode rule."""

    async def _fake_spend(**_kw):
        pass

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "let me search",
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {"name": "web_search", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    provider = _provider(_handler)
    resp = await inference._forward_unary(
        provider, _request(), user_id="u1", conversation_id="c1"
    )
    message = json.loads(resp.body)["choices"][0]["message"]
    assert message["reasoning_content"] == "let me search"
    assert message["tool_calls"][0]["id"] == "call_9"
    assert message["tool_calls"][0]["function"]["name"] == "web_search"


# --- empty-response diagnosis fidelity (01 F8) -------------------------------
#
# On an empty upstream body the provider computes a PRECISE diagnosis
# (upstream_non_api / content_filtered / model_unknown / …) and emits it as a
# terminal LLMChunk. The cloud proxy must forward that field so the sidecar can
# surface the same diagnosis; otherwise the sidecar re-derives SILENT_EMPTY.


async def test_forward_stream_relays_empty_diagnosis(monkeypatch):
    """(emit) The proxy puts the provider's empty_diagnosis on the wire."""

    async def _fake_spend(**_kw):
        pass

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    class _EmptyDiagProvider:
        async def stream(self, _request):
            yield LLMChunk(
                empty_diagnosis="upstream_non_api", empty_raw_preview="<empty>"
            )

        async def close(self):
            pass

    resp = await inference._forward_stream(
        _EmptyDiagProvider(), _request(stream=True), user_id="u1", conversation_id="c1"
    )
    collected = ""
    async for chunk in resp.body_iterator:
        collected += chunk

    assert "upstream_non_api" in collected
    assert "<empty>" in collected


async def test_provider_stream_surfaces_forwarded_empty_diagnosis():
    """(parse) The sidecar's provider surfaces an inbound (proxied) empty_diagnosis
    verbatim — proof it's forwarded, not re-derived: an empty body would otherwise never
    yield the specific upstream_non_api value."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{}}]}\n\n'
                b'data: {"empty_diagnosis":"upstream_non_api","empty_raw_preview":"<empty>"}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    provider = _provider(_handler)
    chunks = [c async for c in provider.stream(_request(stream=True))]
    diag = [c for c in chunks if c.empty_diagnosis]
    assert len(diag) == 1
    assert diag[0].empty_diagnosis == "upstream_non_api"
    assert diag[0].empty_raw_preview == "<empty>"


async def test_forward_stream_binds_log_context_during_upstream(monkeypatch):
    """StreamingResponse outlives the handler log_context; re-bind for the upstream read."""

    async def _fake_spend(**_kw):
        pass

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    seen: dict[str, str] = {}

    class _ContextProbeProvider:
        async def stream(self, _request):
            from agentcore.core.log_context import get_log_value

            seen["conversation_id"] = get_log_value("conversation_id")
            seen["trace_id"] = get_log_value("trace_id")
            seen["persona"] = get_log_value("persona")
            yield LLMChunk(delta_content="ok", finish_reason="stop")

        async def close(self):
            pass

    resp = await inference._forward_stream(
        _ContextProbeProvider(),
        _request(stream=True),
        user_id="u1",
        conversation_id="c1",
        trace_id="ab" * 16,
        attribution={"persona": "CEO", "role": "captain"},
    )
    async for _chunk in resp.body_iterator:
        pass

    assert seen["conversation_id"] == "c1"
    assert seen["trace_id"] == "ab" * 16
    assert seen["persona"] == "CEO"


# --- stream-control relay fidelity (断线可救跨代理跳) --------------------------
#
# The provider's committed-aware stream emits two control signals: stream_reset (a
# transparent pre-commit retry happened → drop ephemeral reasoning) and aborted (a
# post-commit disconnect → keep the partial, finish DEGRADED). Across the proxy hop
# both would flatten to an empty delta and be lost, so the platform path would double
# up reasoning and mask a truncated turn as a clean finish. These pin the inline relay
# both directions, parallel to the empty_diagnosis pair.


async def test_forward_stream_relays_stream_control_signals(monkeypatch):
    """(emit) The proxy puts stream_reset / aborted on the wire as inline markers."""

    async def _fake_spend(**_kw):
        pass

    monkeypatch.setattr(inference.proxy, "_record_proxy_spend", _fake_spend)

    class _ControlSignalProvider:
        async def stream(self, _request):
            yield LLMChunk(delta_reasoning="stale thinking")
            yield LLMChunk(stream_reset=True)
            yield LLMChunk(delta_content="partial answer")
            yield LLMChunk(aborted=True)

        async def close(self):
            pass

    resp = await inference._forward_stream(
        _ControlSignalProvider(), _request(stream=True), user_id="u1", conversation_id="c1"
    )
    collected = ""
    async for chunk in resp.body_iterator:
        collected += chunk

    assert '"stream_reset": true' in collected
    assert '"aborted": true' in collected


async def test_provider_stream_reconstructs_forwarded_control_signals():
    """(parse) The sidecar's provider reconstructs stream_reset / aborted from the
    proxied inline markers — proof they survive the hop, not re-derived. The aborted
    marker also terminates the stream without a retry (post-commit is non-retryable)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"reasoning_content":"stale"}}]}\n\n'
                b'data: {"stream_reset": true}\n\n'
                b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                b'data: {"aborted": true}\n\n'
                b'data: {"choices":[{"delta":{"content":"SHOULD NOT APPEAR"}}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    provider = _provider(_handler)
    chunks = [c async for c in provider.stream(_request(stream=True))]

    assert any(c.stream_reset for c in chunks)
    assert any(c.aborted for c in chunks)
    # The aborted marker returns immediately: nothing after it is surfaced.
    assert [c.delta_content for c in chunks if c.delta_content] == ["partial"]
