"""OpenAI-shaped LLM proxy for on-machine sidecar turns."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import get_cost_event_repo, get_db
from agentcore.api.routes.inference.token import inference_user
from agentcore.api.sse import release_request_db_before_sse
from agentcore.billing.call_meter import PROXY_LLM_SCENARIO
from agentcore.billing.gate import preflight_llm_credentials
from agentcore.conversation.inference_rate_limit import enforce_inference_proxy_rate_limit
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import (
    BYOK_KEY_REQUIRED_MESSAGE,
    BYOKKeyMissingError,
    LLMError,
    QuotaExceededError,
    ValidationError,
)
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.db.models import User
from agentcore.db.repositories import CostEventRepository
from agentcore.llm.credentials import (
    INFERENCE_CONVERSATION_HEADER,
    INFERENCE_MESSAGE_HEADER,
    INFERENCE_TRACE_HEADER,
    LLMCredentials,
)
from agentcore.llm.factory import build_provider
from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL
from agentcore.llm.provider.protocol import (
    LLMMessage,
    LLMRequest,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
    normalize_thinking_blocks,
)
from agentcore.llm.resolve import (
    ModelConfig,
    ModelSelection,
    platform_llm_credentials,
    resolve_conversation_model_selection,
)
from agentcore.runtime.error_fields import error_fields_for

logger = get_logger(__name__)

router = APIRouter()

_CONVERSATION_HEADER = INFERENCE_CONVERSATION_HEADER
_TRACE_HEADER = INFERENCE_TRACE_HEADER
_MESSAGE_HEADER = INFERENCE_MESSAGE_HEADER
_UPSTREAM_RETRIED_HEADER = {"X-Upstream-Retried": str(3)}
_EFFORT_UNSET: object = object()


def _usage_to_openai_wire(usage: TokenUsage) -> dict:
    """OpenAI/DeepSeek-shaped usage for sidecar ``_usage_from`` (cache + reasoning)."""
    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "prompt_cache_hit_tokens": usage.cache_hit_tokens,
        "prompt_cache_miss_tokens": usage.cache_miss_tokens,
        "completion_tokens_details": {
            "reasoning_tokens": usage.reasoning_tokens,
        },
    }


def _credentials_from_config(cfg: ModelConfig, *, conversation_id: str | None, trace_id: str | None) -> LLMCredentials:
    extra: dict[str, str] = {}
    if conversation_id:
        extra[_CONVERSATION_HEADER] = conversation_id
    if trace_id:
        extra[_TRACE_HEADER] = trace_id
    return LLMCredentials(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        default_model=cfg.model,
        extra_headers=extra or None,
        source="platform" if cfg.source == "platform" else "user",
        background_model=cfg.background_model,
        provider_id=cfg.provider_id,
        label=cfg.label,
    )


def _explicit_selection_from_requested(requested: str | None) -> ModelSelection | None:
    """Parse a node route key (``platform/{id}`` / ``{provider_id}/{id}``) into a selection.

    Bare mint / chat model ids are not explicit — callers keep role-slot defaults.
    Vendor router prefixes (``doubao/`` …) are not catalog identity triples; ignore.
    """
    raw = (requested or "").strip()
    if not raw or "/" not in raw:
        return None
    prefix, _, rest = raw.partition("/")
    if not prefix or not rest:
        return None
    from agentcore.llm.pricing import _VENDOR_PREFIXES

    if prefix in _VENDOR_PREFIXES:
        return None
    if prefix == PLATFORM_PROVIDER_SENTINEL:
        return ModelSelection(model=rest, origin="platform", provider_id=None)
    return ModelSelection(model=rest, origin="byok", provider_id=prefix)


async def _resolve_inference_credentials(
    session: AsyncSession,
    cost_repo: CostEventRepository,
    user: User,
    *,
    conversation_id: str | None = None,
    cost_role: str | None = None,
    requested_model: str | None = None,
) -> ModelConfig:
    from agentcore.db.repositories import ConversationRepository, UserRepository
    from agentcore.llm.catalog import validate_model_choice
    from agentcore.llm.resolve import (
        resolve_account_default_model,
        resolve_account_worker_selection,
    )
    from agentcore.runtime.costing import ROLE_MEMBER, ROLE_VISION

    # Before any slot expand: sidecar must not mint a second reader.
    if cost_role == ROLE_VISION:
        raise ValidationError("读图角色已退役：图走当前主力多模态，不再单独调识图槽")

    conv = None
    if conversation_id:
        conv = await ConversationRepository(session).get_by_id(
            conversation_id, user_id=user.user_id
        )
    if conv is not None:
        selection = await resolve_conversation_model_selection(session, conv, user.user_id)
    else:
        selection = await resolve_account_default_model(session, user.user_id)
    explicit = _explicit_selection_from_requested(requested_model)
    if explicit is not None:
        enabled: tuple[str, ...] = ()
        if session is not None:
            enabled = await UserRepository(session).enabled_model_refs(user.user_id)
        ok = await validate_model_choice(
            session,
            user.user_id,
            explicit.model,
            explicit.origin,
            explicit.provider_id,
            enabled_refs=enabled,
        )
        if not ok:
            raise ValidationError(
                "节点模型不可用或未在目录中："
                f"model={explicit.model} · origin={explicit.origin}"
                + (
                    f" · provider_id={explicit.provider_id}"
                    if explicit.provider_id
                    else ""
                )
                + "。请改选可用模型，禁止 silent 回退。"
            )
        selection = explicit
    elif cost_role == ROLE_MEMBER:
        # Delegated workers without a node identity: profile Worker slot (else main).
        # Captain / arena / product-chrome roles keep the conversation main model.
        worker = await resolve_account_worker_selection(
            session, user.user_id, conv=conv
        )
        if worker is not None and (
            worker.model != selection.model
            or worker.origin != selection.origin
            or worker.provider_id != selection.provider_id
        ):
            selection = worker

    credentials = await preflight_llm_credentials(
        session=session,
        user=user,
        cost_repo=cost_repo,
        byok_missing_message=BYOK_KEY_REQUIRED_MESSAGE,
        model_origin=selection.origin,
        provider_id=selection.provider_id,
    )
    if credentials is not None:
        cfg = ModelConfig(
            model=selection.model,
            base_url=credentials.base_url,
            api_key=credentials.api_key,
            source="byok",
            purpose="chat",
            background_model=credentials.background_model,
            provider_id=credentials.provider_id,
            label=credentials.label,
        )
    else:
        # Per-model platform credential (「一 key 一模型」): the selected model's
        # override key/base_url win, else the shared default.
        platform = platform_llm_credentials(model=selection.model)
        if platform is None:
            from agentcore.llm.platform_pool_scheduler import platform_pool_unavailable_error

            raise platform_pool_unavailable_error(blocked=True)
        cfg = ModelConfig(
            model=selection.model,
            base_url=platform.base_url,
            api_key=platform.api_key,
            source="platform",
            purpose="chat",
        )
    return cfg


async def _record_proxy_spend(
    *,
    user_id: str,
    conversation_id: str | None,
    model: str,
    usage: TokenUsage,
    trace_id: str | None = None,
    message_id: str | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    persona: str | None = None,
    call_id: str | None = None,
    credential_source: str | None = None,
) -> None:
    """Enqueue a per-call detail (+ materialize per-run) — never touches primary DB.

    Request path only persists to the process-local durable queue (as-built: 成本配额 §三).
    Drain writes ``cost_calls`` then upserts ``cost_events`` on the telemetry pool;
    ``call_id`` UNIQUE dedupes at-least-once retries. See ``billing.proxy_spend_queue``.

    ``role=vision`` is the same fence as in-process ``call_meter`` (``vision.*``):
    live 读图 rides the main model; historical ``cost_events.role=vision`` still
    fold at settle. Metering the proxied HTTP call would double-bill.
    """
    from agentcore.billing.proxy_spend_queue import get_proxy_spend_queue
    from agentcore.runtime.costing import ROLE_VISION

    if role == ROLE_VISION:
        return

    # Role / credential / token / money fields are assembled inside
    # ``assemble_ledger_call`` (shared with in-process metering).
    with log_context(trace_id=trace_id, conversation_id=conversation_id):
        get_proxy_spend_queue().enqueue(
            user_id=user_id,
            conversation_id=conversation_id,
            model=model,
            usage=usage,
            trace_id=trace_id,
            message_id=message_id,
            run_id=run_id,
            parent_run_id=parent_run_id,
            agent_id=agent_id,
            role=role,
            persona=persona,
            call_id=call_id,
            credential_source=credential_source,
        )


def _error_json(exc: Exception) -> JSONResponse:
    code, message, context = error_fields_for(
        exc,
        fallback_code=ErrorCode.LLM_ERROR,
        fallback_message="上游推理服务错误",
    )
    payload: dict = {"error": {"code": code, "message": message}}
    if context:
        payload["error"]["context"] = context
    status = getattr(exc, "status_code", 502)
    return JSONResponse(status_code=status, content=payload, headers=_UPSTREAM_RETRIED_HEADER)


def _tool_calls_from_payload(raw: list[dict] | None) -> list[ToolCall] | None:
    """Rebuild ``ToolCall``s from an inbound OpenAI ``tool_calls`` array.

    The faithful inverse of the provider's ``_build_payload`` serialization: an
    assistant turn that issued tool calls must reach the upstream unchanged, or
    DeepSeek rejects the next tool round (the thinking-mode tool contract 400s
    when the assistant/tool shape is incomplete). Missing pieces default to
    empty strings — never dropped."""
    if not raw:
        return None
    return [
        ToolCall(
            id=tc.get("id", ""),
            function=ToolCallFunction(
                name=(tc.get("function") or {}).get("name", ""),
                arguments=(tc.get("function") or {}).get("arguments", ""),
            ),
        )
        for tc in raw
    ]


async def _resolve_profile_reasoning_effort(
    session: AsyncSession,
    user_id: str,
    conversation_id: str | None,
) -> str | None:
    """Combination-level vendor token. Sidecar has no combo DB — proxy stamps it."""
    from agentcore.db.repositories import ConversationRepository
    from agentcore.llm.model_profiles import LlmModelProfileService

    conv = None
    if conversation_id:
        conv = await ConversationRepository(session).get_by_id(
            conversation_id, user_id=user_id
        )
    svc = LlmModelProfileService(session)
    if conv is not None:
        expanded = await svc.expand_for_conversation(user_id, conv)
    else:
        expanded = await svc.expand(user_id, None)
    return expanded.reasoning_effort


def _llm_request_from_payload(
    payload: dict,
    cfg: ModelConfig,
    *,
    reasoning_effort: str | None | object = _EFFORT_UNSET,
) -> LLMRequest:
    # Faithful dict→LLMMessage: keep the FULL assistant/tool field set the sidecar
    # sent (tool_calls / tool_call_id / reasoning_content), not just role+content.
    # reasoning_content is preserved verbatim (including "") — the provider's
    # _build_payload re-applies DeepSeek's thinking-mode echo rule on the way out.
    messages = [
        LLMMessage(
            role=m["role"],
            content=m.get("content"),
            tool_calls=_tool_calls_from_payload(m.get("tool_calls")),
            tool_call_id=m.get("tool_call_id"),
            reasoning_content=m.get("reasoning_content"),
            thinking_blocks=normalize_thinking_blocks(m.get("thinking_blocks")),
        )
        for m in payload.get("messages", [])
    ]
    thinking_raw = payload.get("thinking")
    thinking: bool | None = None
    if isinstance(thinking_raw, dict):
        kind = thinking_raw.get("type")
        if kind == "disabled":
            thinking = False
        elif kind == "enabled":
            thinking = True
    raw_effort = payload.get("reasoning_effort")
    payload_effort = (
        (raw_effort.strip() or None) if isinstance(raw_effort, str) else None
    )
    # Expanded combination token wins, including None (vendor default).
    if reasoning_effort is not _EFFORT_UNSET:
        effort = reasoning_effort if isinstance(reasoning_effort, str) else None
    else:
        effort = payload_effort
    return LLMRequest(
        messages=messages,
        # Server-resolved model is authoritative: the sidecar may still send
        # settings.platform_model (e.g. deepseek-v4-flash) while BYOK routes to the user's endpoint.
        model=cfg.model,
        temperature=float(payload.get("temperature", 0.7)),
        max_tokens=payload.get("max_tokens"),
        tools=payload.get("tools"),
        tool_choice=payload.get("tool_choice", "auto"),
        stream=bool(payload.get("stream")),
        thinking=thinking,
        reasoning_effort=effort,
        # Marks proxy-forwarded calls so in-process metering skips (proxy_spend
        # is the sole ledger source); llm.call logging still uses this scenario.
        scenario=PROXY_LLM_SCENARIO,
    )


@router.post("/inference/v1/chat/completions")
async def inference_chat_completions(
    request: Request,
    user: User = Depends(inference_user),
    session: AsyncSession = Depends(get_db),
    cost_repo: CostEventRepository = Depends(get_cost_event_repo),
) -> Response:
    payload = await request.json()
    stream = bool(payload.get("stream"))
    # conversation_id is client-supplied and intentionally NOT ownership-checked here
    # (audit 01 F12): cost_events has no FK to conversations (db/models/billing.py), so an
    # unowned id can't fail the write, and every read aggregates on (user_id,
    # conversation_id) with user_id taken from the authenticated inference token — so a
    # forged id can only mis-file the caller's OWN spend inside their own ledger, never
    # leak across users. Adding a per-turn ownership lookup on this hot path isn't worth it.
    conversation_id = request.headers.get(_CONVERSATION_HEADER) or None
    trace_id = request.headers.get(_TRACE_HEADER) or None
    message_id = request.headers.get(_MESSAGE_HEADER) or None
    from agentcore.billing.attribution import parse_attribution_headers

    attribution = parse_attribution_headers(request.headers)

    with log_context(
        trace_id=trace_id,
        conversation_id=conversation_id,
        user_id=user.user_id,
        # Stamp proxy-side llm.call with the sidecar worker tree (same keys the
        # provider already sent as attribution headers). Empty values are dropped.
        run_id=attribution.get("run_id"),
        parent_run_id=attribution.get("parent_run_id"),
        agent_id=attribution.get("agent_id"),
        cost_role=attribution.get("role"),
        persona=attribution.get("persona"),
    ):
        # Outer rate fence (成本配额与计费.md §一): once per sidecar turn via
        # message_id, reusing the cloud user-message limiter — not per LLM call.
        await enforce_inference_proxy_rate_limit(user.user_id, message_id=message_id)

        try:
            cfg = await _resolve_inference_credentials(
                session,
                cost_repo,
                user,
                conversation_id=conversation_id,
                cost_role=attribution.get("role"),
                requested_model=payload.get("model")
                if isinstance(payload.get("model"), str)
                else None,
            )
        except BYOKKeyMissingError as e:
            return JSONResponse(
                status_code=402, content={"error": {"code": e.code, "message": e.message}}
            )
        except QuotaExceededError as e:
            return JSONResponse(
                status_code=429, content={"error": {"code": e.code, "message": e.message}}
            )
        except ValidationError as e:
            return JSONResponse(
                status_code=e.status_code,
                content={"error": {"code": e.code, "message": e.message}},
            )

        creds = _credentials_from_config(cfg, conversation_id=conversation_id, trace_id=trace_id)
        from agentcore.llm.credentials import bind_credential_pricing_context

        bind_credential_pricing_context(creds)
        provider = build_provider(creds)
        profile_effort = await _resolve_profile_reasoning_effort(
            session, user.user_id, conversation_id
        )
        llm_request = _llm_request_from_payload(
            payload, cfg, reasoning_effort=profile_effort
        )

        # Credentials + gate are done and spend recording goes through the proxy
        # queue, so nothing below needs this session. Release it before waiting on
        # upstream: every in-flight LLM call — streamed turns and unary background
        # jobs alike — would otherwise pin a primary-pool connection for the whole
        # generation, and this is the hottest route in the product.
        await release_request_db_before_sse(session)

        if stream:
            return await _forward_stream(
                provider,
                llm_request,
                user_id=user.user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                message_id=message_id,
                attribution=attribution,
                credential_source=creds.source,
            )
        return await _forward_unary(
            provider,
            llm_request,
            user_id=user.user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            message_id=message_id,
            attribution=attribution,
            credential_source=creds.source,
        )


async def _forward_unary(
    provider,
    request: LLMRequest,
    *,
    user_id: str,
    conversation_id: str | None,
    trace_id: str | None = None,
    message_id: str | None = None,
    attribution: dict | None = None,
    credential_source: str | None = None,
) -> Response:
    try:
        response = await provider.complete(request)
    except Exception as e:
        logger.warning("inference.proxy_upstream_error", error=str(e))
        if isinstance(e, LLMError):
            return _error_json(e)
        return JSONResponse(
            status_code=502,
            content={"error": {"code": ErrorCode.LLM_ERROR, "message": "上游推理服务不可达"}},
            headers=_UPSTREAM_RETRIED_HEADER,
        )
    finally:
        await provider.close()

    tool_calls = None
    if response.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in response.tool_calls
        ]
    message: dict = {"role": "assistant", "content": response.content}
    # Pass the model's reasoning back so a tool-calling turn the sidecar echoes on
    # the next round carries it (DeepSeek thinking-mode 400s without it).
    if response.reasoning_content is not None:
        message["reasoning_content"] = response.reasoning_content
    if response.thinking_blocks:
        message["thinking_blocks"] = response.thinking_blocks
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = json.dumps(
        {
            "id": "chatcmpl-proxy",
            "object": "chat.completion",
            "model": response.model,
            "choices": [{"index": 0, "message": message, "finish_reason": response.finish_reason}],
            "usage": _usage_to_openai_wire(response.usage),
        }
    ).encode()
    attr = attribution or {}
    await _record_proxy_spend(
        user_id=user_id,
        conversation_id=conversation_id,
        model=response.model,
        usage=response.usage,
        trace_id=trace_id,
        message_id=message_id,
        run_id=attr.get("run_id"),
        parent_run_id=attr.get("parent_run_id"),
        agent_id=attr.get("agent_id"),
        role=attr.get("role"),
        persona=attr.get("persona"),
        call_id=attr.get("call_id"),
        credential_source=credential_source,
    )
    return Response(content=body, status_code=200, media_type="application/json")


def _tool_call_deltas_to_wire(deltas: list[ToolCallDelta]) -> list[dict]:
    """Project streamed tool-call deltas back to the OpenAI ``delta.tool_calls``
    shape the sidecar's stream parser accumulates (index/id/function.name/
    function.arguments). Without this relay a proxied tool call never reaches the
    sidecar, so the whole delegate/debate loop can't even start.

    The first delta for a call carries its id + function name; continuation
    deltas carry only an index + an ``arguments`` fragment. Absent pieces are
    omitted (not sent as ``null``) to mirror real upstream streaming and satisfy
    the accumulator's ``if delta.id`` / ``if function_name`` guards."""
    wire: list[dict] = []
    for tc in deltas:
        entry: dict = {"index": tc.index, "type": "function"}
        if tc.id is not None:
            entry["id"] = tc.id
        function: dict = {}
        if tc.function_name is not None:
            function["name"] = tc.function_name
        if tc.arguments_delta is not None:
            function["arguments"] = tc.arguments_delta
        if function:
            entry["function"] = function
        wire.append(entry)
    return wire


async def _forward_stream(
    provider,
    request: LLMRequest,
    *,
    user_id: str,
    conversation_id: str | None,
    trace_id: str | None = None,
    message_id: str | None = None,
    attribution: dict | None = None,
    credential_source: str | None = None,
) -> Response:
    captured: dict = {}
    attr = attribution or {}

    async def _iter_sse():
        # StreamingResponse runs after the request handler's log_context exits.
        # Re-bind for the upstream read so llm.empty_response / llm.call_retried
        # on this hop keep conversation_id / trace_id / run tree.
        with log_context(
            trace_id=trace_id,
            conversation_id=conversation_id,
            user_id=user_id,
            message_id=message_id,
            run_id=attr.get("run_id"),
            parent_run_id=attr.get("parent_run_id"),
            agent_id=attr.get("agent_id"),
            cost_role=attr.get("role"),
            persona=attr.get("persona"),
        ):
            async for chunk in provider.stream(request):
                # Relay the provider's stream-control signals inline (mirror of the
                # empty_diagnosis relay) so the downstream sidecar reconstructs the exact
                # LLMChunk protocol across the proxy hop: a transparent pre-commit retry
                # (stream_reset) and a post-commit disconnect salvage (aborted) would
                # otherwise flatten to an empty delta and be silently lost.
                if chunk.stream_reset:
                    yield f"data: {json.dumps({'stream_reset': True})}\n\n"
                    continue
                if chunk.aborted:
                    yield f"data: {json.dumps({'aborted': True})}\n\n"
                    continue
                data: dict = {"choices": [{"index": 0, "delta": {}, "finish_reason": None}]}
                delta = data["choices"][0]["delta"]
                if chunk.delta_content:
                    delta["content"] = chunk.delta_content
                if chunk.delta_reasoning:
                    delta["reasoning_content"] = chunk.delta_reasoning
                if chunk.delta_tool_calls:
                    delta["tool_calls"] = _tool_call_deltas_to_wire(chunk.delta_tool_calls)
                if chunk.finish_reason:
                    data["choices"][0]["finish_reason"] = chunk.finish_reason
                if chunk.usage:
                    captured["usage"] = chunk.usage
                    captured["model"] = request.model
                    data["usage"] = _usage_to_openai_wire(chunk.usage)
                if chunk.thinking_blocks:
                    data["thinking_blocks"] = chunk.thinking_blocks
                if chunk.empty_diagnosis:
                    # Relay the provider's precise empty-response diagnosis (upstream_non_api /
                    # MODEL_UNKNOWN ...) so the sidecar surfaces the actionable hint instead of
                    # re-deriving a generic SILENT_EMPTY from a bare empty delta (01 F8).
                    diag: dict = {"empty_diagnosis": chunk.empty_diagnosis}
                    if chunk.empty_raw_preview is not None:
                        diag["empty_raw_preview"] = chunk.empty_raw_preview
                    yield f"data: {json.dumps(diag, ensure_ascii=False)}\n\n"
                    continue
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    sse_gen = _iter_sse()
    try:
        first_line = await sse_gen.__anext__()
    except StopAsyncIteration:
        first_line = None
    except Exception as e:
        logger.warning("inference.proxy_stream_error", error=str(e))
        await provider.close()
        if isinstance(e, LLMError):
            return _error_json(e)
        return JSONResponse(
            status_code=502,
            content={"error": {"code": ErrorCode.LLM_ERROR, "message": "上游推理服务不可达"}},
            headers=_UPSTREAM_RETRIED_HEADER,
        )

    async def relay():
        try:
            if first_line is not None:
                yield first_line
            async for line in sse_gen:
                yield line
        finally:
            await provider.close()
            usage = captured.get("usage")
            if usage:
                await _record_proxy_spend(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    model=captured.get("model") or request.model,
                    usage=usage,
                    trace_id=trace_id,
                    message_id=message_id,
                    run_id=attr.get("run_id"),
                    parent_run_id=attr.get("parent_run_id"),
                    agent_id=attr.get("agent_id"),
                    role=attr.get("role"),
                    persona=attr.get("persona"),
                    call_id=attr.get("call_id"),
                    credential_source=credential_source,
                )

    return StreamingResponse(relay(), media_type="text/event-stream")


def usage_from_deepseek(usage: dict) -> TokenUsage:
    """Map a DeepSeek/OpenAI-style usage dict to ``TokenUsage`` (protocol.py parser)."""
    return TokenUsage.from_openai_wire(usage)
