"""Opt-in live probe: vendor prefix cache vs our compact ``llm.call`` attribution.

Does **not** run in the default unit suite. Classification of ``tools`` vs
``history_growth`` is already covered with fake usage in
``test_prefix_cache_probe.py``. This file only asks a real provider, in one
process, whether an append can carry ``cache_hit_tokens`` and whether mutating
the opening ``tools[]`` is still classified as ``prefix_breach=tools``.

Hop 3 appends the real ``host`` OpenAI schema (consult-shaped, ~1.6k chars), not
an empty stub — a one-line extra tool cannot falsify「vendor keys cache on tools[]」.
Hop 4 keeps that table and rewrites one character of ``system``: if append hits
stay high here too, the vendor ``cache_hit`` field is not a real prefix cache.

A second test asks the cross-user-turn question: product T2 splices the previous
``[系统提示]`` envelope into history (``opening_ceo_messages``) and appends a new
envelope only when it differs, plus the new utterance. History body is padded so a system-only hit is
distinguishable from history reuse. Classification of growth is asserted; vendor
``cache_hit`` is printed and warned, not asserted.

A third test is the cheap anti-pattern (two hops, ``max_tokens=16``): T2 drops the
prior envelope. Classification must be ``history_rewrite``; vendor hit is printed,
not asserted. Do not add window-compact live hops.

Run (apps/server, your own Key — never the production pool)::

    AGENTCORE_LIVE_PREFIX_CACHE=1 uv run pytest tests/test_prefix_cache_live.py -q -s
    AGENTCORE_LIVE_PREFIX_CACHE=1 uv run pytest tests/test_prefix_cache_live.py::test_vendor_prefix_cache_dropped_envelope_logs_history_rewrite -q -s

Credentials: ``eval_credentials()`` (EVAL_DEEPSEEK_* → seeded dev BYOK). Platform
pool fallback is refused. Rate-limit / auth / empty-wallet skip, they do not fail
the suite. A 0% vendor hit on the append hop is recorded, not asserted — disk
cache and minimum cacheable length are best-effort.
"""

from __future__ import annotations

import json
import os
import uuid
import warnings

import pytest
from structlog.testing import capture_logs

from agentcore.core.errors import LLMError
from agentcore.core.log_context import log_context, new_trace_id
from agentcore.costing.ledger import ROLE_CAPTAIN
from agentcore.llm.factory import build_provider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.observability.prefix_cache import (
    BREACH_COLD_CHAIN,
    BREACH_HISTORY_GROWTH,
    BREACH_HISTORY_REWRITE,
    BREACH_SYSTEM_PROMPT,
    BREACH_TOOLS,
    reset_prefix_cache_state,
)
from agentcore.runtime.resolve.prompt.envelope import (
    TURN_ENVELOPE_FENCE,
    opening_ceo_messages,
)

_LIVE_FLAG = "AGENTCORE_LIVE_PREFIX_CACHE"

# Long enough to clear typical vendor cache floors (OpenAI 1024; DeepSeek is lower).
_STABLE_SYSTEM = ("Stable prefix block for vendor prompt-cache. " * 160).strip()
# Same length, first byte changed — punches a token-0 prefix without shrinking the prompt.
_MUTATED_SYSTEM = "X" + _STABLE_SYSTEM[1:]

_TOOL_A = [
    {
        "type": "function",
        "function": {
            "name": "alpha_probe",
            "description": "No-op probe tool A. Do not call.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }
]


def _promoted_tools() -> list[dict]:
    """Opening stub + a real consult-sized schema (``host``, not an empty probe)."""
    from agentcore.tools.builtin.host import HostTool
    from agentcore.tools.protocol import tool_schema_to_openai_format

    return [*_TOOL_A, tool_schema_to_openai_format(HostTool().schema)]


def _live_enabled() -> bool:
    return os.environ.get(_LIVE_FLAG, "").strip().lower() in {"1", "true", "yes"}


async def _live_credentials():
    if not _live_enabled():
        pytest.skip(
            f"opt-in live prefix-cache probe: set {_LIVE_FLAG}=1 "
            "(EVAL_DEEPSEEK_API_KEY or seeded dev BYOK; never PLATFORM_API_KEY)"
        )
    from agentcore.evals.harness import (
        _credentials_from_dev_byok,
        _credentials_from_eval_env,
    )

    env_creds = _credentials_from_eval_env()
    if env_creds is not None:
        return env_creds
    try:
        byok = await _credentials_from_dev_byok()
    except Exception as exc:  # noqa: BLE001 — lookup fail is skip, not a product bug
        pytest.skip(f"dev BYOK lookup failed: {exc}")
    if byok is None:
        pytest.skip(
            "no EVAL_DEEPSEEK_API_KEY and no seeded dev BYOK; "
            "refusing PLATFORM_API_KEY"
        )
    return byok


# Long enough that a system-only hit cannot be mistaken for "history survived".
_STABLE_USER = ("Prior user turn body for vendor prompt-cache. " * 80).strip()
_ENV1 = f"{TURN_ENVELOPE_FENCE}\n<运行时>\n当前日期：2026-09-20 UTC\n</运行时>"
_ENV2 = (
    f"{TURN_ENVELOPE_FENCE}\n<运行时>\n当前日期：2026-09-20 UTC\n</运行时>\n"
    "<表格>\n选中 1 行\n</表格>"
)


def _req(messages: list[LLMMessage], *, model: str, tools: list) -> LLMRequest:
    return LLMRequest(
        messages=messages,
        model=model,
        temperature=0.0,
        max_tokens=16,
        tools=tools,
        tool_choice="none",
        stream=False,
        scenario="title",
        thinking=False,
        retry_patience_seconds=0.0,
    )


def _skip_llm_error(exc: LLMError, *, model: str) -> None:
    bits = [f"upstream {type(exc).__name__}: {exc}", f"model={model}"]
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        status = details.get("upstream_status")
        preview = details.get("upstream_body_preview")
        if status is not None:
            bits.append(f"status={status}")
        if preview:
            bits.append(str(preview)[:800])
    pytest.skip("; ".join(bits))


def _llm_calls(caps: list[dict]) -> list[dict]:
    return [row for row in caps if row.get("event") == "llm.call"]


@pytest.fixture(autouse=True)
def _clean_probe_state():
    reset_prefix_cache_state()
    yield
    reset_prefix_cache_state()


@pytest.mark.asyncio
@pytest.mark.live_llm
@pytest.mark.timeout(180)
async def test_vendor_prefix_cache_append_then_tools_mutation():
    creds = await _live_credentials()
    model = (creds.default_model or "").strip()
    if not model:
        pytest.skip("eval credentials have no default_model")

    provider = build_provider(creds)
    promoted = _promoted_tools()
    cid = f"live-prefix-{uuid.uuid4().hex}"
    system = LLMMessage(role="system", content=_STABLE_SYSTEM)
    user1 = LLMMessage(role="user", content="Reply with the single word ping.")
    hop1_messages = [system, user1]

    try:
        with (
            log_context(
                conversation_id=cid,
                trace_id=new_trace_id(),
                cost_role=ROLE_CAPTAIN,
            ),
            capture_logs() as caps,
        ):
            try:
                hop1 = await provider.complete(_req(hop1_messages, model=model, tools=_TOOL_A))
                hop2_messages = [
                    *hop1_messages,
                    LLMMessage(role="assistant", content=hop1.content or "ping"),
                    LLMMessage(role="user", content="Reply with the single word pong."),
                ]
                hop2 = await provider.complete(_req(hop2_messages, model=model, tools=_TOOL_A))
                hop3_messages = [
                    *hop2_messages,
                    LLMMessage(role="assistant", content=hop2.content or "pong"),
                    LLMMessage(role="user", content="Reply with the single word done."),
                ]
                await provider.complete(_req(hop3_messages, model=model, tools=promoted))
                hop4_messages = [
                    LLMMessage(role="system", content=_MUTATED_SYSTEM),
                    *hop3_messages[1:],
                ]
                await provider.complete(_req(hop4_messages, model=model, tools=promoted))
            except LLMError as exc:
                _skip_llm_error(exc, model=model)
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    calls = _llm_calls(caps)
    assert len(calls) == 4, f"expected 4 llm.call lines, got {len(calls)}"

    assert calls[0]["prefix_breach"] == BREACH_COLD_CHAIN
    assert calls[0]["tools_changed"] is False
    assert calls[0]["tools_count"] == 1

    assert calls[1]["prefix_breach"] == BREACH_HISTORY_GROWTH
    assert calls[1]["tools_changed"] is False
    assert calls[1]["tools_count"] == 1

    assert calls[2]["prefix_breach"] == BREACH_TOOLS
    assert calls[2]["tools_changed"] is True
    assert calls[2]["tools_count"] == 2

    assert calls[3]["prefix_breach"] == BREACH_SYSTEM_PROMPT
    assert calls[3]["tools_changed"] is False
    assert calls[3]["tools_count"] == 2

    append_hit = int(calls[1].get("cache_hit_tokens") or 0)
    tools_hit = int(calls[2].get("cache_hit_tokens") or 0)
    system_hit = int(calls[3].get("cache_hit_tokens") or 0)
    append_in = int(calls[1].get("input_tokens") or 0)
    tools_in = int(calls[2].get("input_tokens") or 0)
    system_in = int(calls[3].get("input_tokens") or 0)
    promoted_chars = len(json.dumps(promoted[1], ensure_ascii=False))
    print(
        f"vendor cache: append hit={append_hit}/{append_in} "
        f"tools_changed hit={tools_hit}/{tools_in} "
        f"system_rewrite hit={system_hit}/{system_in} "
        f"promoted_host_chars={promoted_chars}"
    )
    if append_hit <= 0:
        warnings.warn(
            "vendor reported 0 cache_hit on the append hop "
            f"(input_tokens={append_in}, model={model}); "
            "threshold / best-effort, not a product classification bug",
            stacklevel=1,
        )
    elif system_hit >= append_hit:
        warnings.warn(
            "system rewrite did not drop cache_hit below the append hop "
            f"(append={append_hit}, system_rewrite={system_hit}, model={model}); "
            "vendor cache field may be sticky — do not trust the tools[] result",
            stacklevel=1,
        )


@pytest.mark.asyncio
@pytest.mark.live_llm
@pytest.mark.timeout(180)
async def test_vendor_prefix_cache_envelope_cross_turn():
    """Product T2 keeps the prior envelope and only appends the new turn."""
    creds = await _live_credentials()
    model = (creds.default_model or "").strip()
    if not model:
        pytest.skip("eval credentials have no default_model")

    provider = build_provider(creds)
    system = _STABLE_SYSTEM
    user1 = f"{_STABLE_USER}\nReply with the single word ping."
    user2 = "Reply with the single word pong."
    cid = f"live-env-product-{uuid.uuid4().hex}"

    turn1 = opening_ceo_messages(
        system_prompt=system,
        history=None,
        turn_envelope=_ENV1,
        user_content=user1,
    )

    try:
        with (
            log_context(
                conversation_id=cid,
                trace_id=new_trace_id(),
                cost_role=ROLE_CAPTAIN,
            ),
            capture_logs() as caps,
        ):
            try:
                hop1 = await provider.complete(_req(turn1, model=model, tools=_TOOL_A))
                product_t2 = opening_ceo_messages(
                    system_prompt=system,
                    history=[
                        LLMMessage(role="user", content=_ENV1),
                        LLMMessage(role="user", content=user1),
                        LLMMessage(role="assistant", content=hop1.content or "ping"),
                    ],
                    turn_envelope=_ENV2,
                    user_content=user2,
                )
                await provider.complete(_req(product_t2, model=model, tools=_TOOL_A))
            except LLMError as exc:
                _skip_llm_error(exc, model=model)
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    calls = _llm_calls(caps)
    assert len(calls) == 2, f"product hops: {len(calls)}"

    assert calls[0]["prefix_breach"] == BREACH_COLD_CHAIN
    assert calls[1]["prefix_breach"] == BREACH_HISTORY_GROWTH

    t2_hit = int(calls[1].get("cache_hit_tokens") or 0)
    t2_in = int(calls[1].get("input_tokens") or 0)
    t1_in = int(calls[0].get("input_tokens") or 0)
    print(
        f"vendor envelope: product_t2 hit={t2_hit}/{t2_in} "
        f"t1_input={t1_in} model={model}"
    )
    if t2_hit <= 0:
        warnings.warn(
            "vendor reported 0 cache_hit on the keep-envelope append "
            f"(input_tokens={t2_in}, model={model}); "
            "threshold / best-effort, not a product classification bug",
            stacklevel=1,
        )


@pytest.mark.asyncio
@pytest.mark.live_llm
@pytest.mark.timeout(90)
async def test_vendor_prefix_cache_dropped_envelope_logs_history_rewrite():
    """Two hops, max_tokens=16: dropped prior envelope must log history_rewrite."""
    creds = await _live_credentials()
    model = (creds.default_model or "").strip()
    if not model:
        pytest.skip("eval credentials have no default_model")

    provider = build_provider(creds)
    cid = f"live-env-drop-{uuid.uuid4().hex}"
    user1 = f"{_STABLE_USER}\nReply with the single word ping."
    turn1 = opening_ceo_messages(
        system_prompt=_STABLE_SYSTEM,
        history=None,
        turn_envelope=_ENV1,
        user_content=user1,
    )

    try:
        with (
            log_context(
                conversation_id=cid,
                trace_id=new_trace_id(),
                cost_role=ROLE_CAPTAIN,
            ),
            capture_logs() as caps,
        ):
            try:
                hop1 = await provider.complete(_req(turn1, model=model, tools=_TOOL_A))
                dropped_t2 = opening_ceo_messages(
                    system_prompt=_STABLE_SYSTEM,
                    history=[
                        LLMMessage(role="user", content=user1),
                        LLMMessage(role="assistant", content=hop1.content or "ping"),
                    ],
                    turn_envelope=_ENV2,
                    user_content="Reply with the single word pong.",
                )
                await provider.complete(_req(dropped_t2, model=model, tools=_TOOL_A))
            except LLMError as exc:
                _skip_llm_error(exc, model=model)
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    calls = _llm_calls(caps)
    assert len(calls) == 2, f"dropped-envelope hops: {len(calls)}"
    assert calls[0]["prefix_breach"] == BREACH_COLD_CHAIN
    assert calls[1]["prefix_breach"] == BREACH_HISTORY_REWRITE
    assert calls[1]["tools_changed"] is False

    t2_hit = int(calls[1].get("cache_hit_tokens") or 0)
    t2_in = int(calls[1].get("input_tokens") or 0)
    print(
        f"vendor dropped-envelope: t2 hit={t2_hit}/{t2_in} "
        f"breach={calls[1]['prefix_breach']} model={model}"
    )
