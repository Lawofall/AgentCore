"""Leaf LLM call fence — wrap providers from ``build_provider``.

Observes every logical ``complete`` / ``stream`` invocation (success → ``llm.call``,
failure → ``llm.call_failed``). Does **not** retry, swap models, or alter chunk
contracts (``stream_reset`` / ``aborted`` pass through unchanged).

Two admission checks run before each upstream call, in the same place and for the
same reason — this call should not be made:

- the turn's auth/balance death latch (``llm.turn_auth_dead``);
- the per-call platform quota brake (``billing.call_quota``), which gives cloud
  in-process turns the granularity the sidecar already gets from its per-call
  ``/inference/`` route gate. Both refusals raise leaf ``LLMError``s, so they land
  on the ``llm.call_failed`` path like any other pre-upstream leaf failure.

Stream interrupt salvage: consumer ``aclose`` / mid-stream exception still emit
``llm.call`` (and thus meter) when a chunk already carried billable usage; with
no seen usage they stay observation-only (``llm.call_failed`` / no fabricated
bill). Chat SSE ``detach_on_disconnect`` is unaffected — this fence only sees
leaf stream lifecycle, not HTTP client detach.

Also forwards leaf-only helpers used outside the chat path (``probe`` /
``probe_tools`` / ``list_models`` for BYOK 设置·测试) — the fence must not
strip those methods.

Inner provider I/O retries stay inside the leaf; this fence sees one attempt per
outer call (``attempt`` defaults to 1; exhausted upstream retries surface via
exception ``retry_attempts`` on failure).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from agentcore.core.errors import AgentCoreError
from agentcore.llm.observability import log_llm_call, log_llm_call_failed
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    TokenUsage,
)


class ObservingLLMProvider:
    """Observing decorator around a leaf :class:`LLMProvider`.

    Observation-only for anything the upstream returns; the only thing it decides
    is whether a call may start at all (auth-dead latch + platform quota).
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return str(getattr(self._inner, "name", getattr(self._inner, "_name", "llm")))

    @property
    def base_url(self) -> str | None:
        url = getattr(self._inner, "base_url", None)
        return str(url) if url else None

    @property
    def _name(self) -> str:
        # Compat for call sites that read ``getattr(llm, "_name", …)``.
        return str(getattr(self._inner, "_name", self.name))

    def clone(self) -> ObservingLLMProvider:
        clone_fn = getattr(self._inner, "clone", None)
        if callable(clone_fn):
            return ObservingLLMProvider(clone_fn())
        return ObservingLLMProvider(self._inner)

    async def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            await close()

    async def probe(self, *, model: str) -> None:
        """Forward connectivity probe to the leaf (BYOK 设置·测试)."""
        probe = getattr(self._inner, "probe", None)
        if probe is None:
            raise AttributeError(
                f"{type(self._inner).__name__} has no probe(); "
                "ObservingLLMProvider cannot test connectivity"
            )
        await probe(model=model)

    async def probe_tools(self, *, model: str) -> bool | None:
        """Forward tools-capability probe to the leaf."""
        probe_tools = getattr(self._inner, "probe_tools", None)
        if probe_tools is None:
            return None
        return await probe_tools(model=model)

    async def list_models(self) -> list[str]:
        """Forward ``GET /models`` discovery to the leaf (BYOK 测连优先路径)."""
        list_models = getattr(self._inner, "list_models", None)
        if list_models is None:
            raise AttributeError(
                f"{type(self._inner).__name__} has no list_models(); "
                "ObservingLLMProvider cannot discover models"
            )
        return await list_models()

    def _provider_name(self) -> str | None:
        name = getattr(self._inner, "name", None) or getattr(self._inner, "_name", None)
        return str(name) if name else None

    @staticmethod
    def _attempt_from_exc(exc: BaseException) -> int:
        """Map leaf ``retry_attempts`` (0-based fail index) → 1-based attempt."""
        if isinstance(exc, AgentCoreError):
            raw = exc.details.get("retry_attempts")
            if isinstance(raw, int) and raw >= 0:
                return raw + 1
        return 1

    @staticmethod
    def _upstream_log_fields(exc: BaseException) -> dict[str, Any]:
        """Pull HTTP upstream context onto ``llm.call_failed`` when present."""
        if not isinstance(exc, AgentCoreError):
            return {}
        out: dict[str, Any] = {}
        status = exc.details.get("upstream_status")
        if isinstance(status, int):
            out["upstream_status"] = status
        preview = exc.details.get("upstream_body_preview")
        if isinstance(preview, str) and preview.strip():
            out["upstream_body_preview"] = preview
        return out

    @staticmethod
    def _billable_usage(usage: TokenUsage | None) -> bool:
        """True when a stream chunk already carried real tokens (no fabrication)."""
        return usage is not None and bool(usage.input_tokens or usage.output_tokens)

    async def _refuse_if_quota_spent(self, request: LLMRequest) -> None:
        """Raise ``LLMQuotaExceededError`` when this call's payer is out of quota.

        Lazy import: ``billing`` reaches back into ``llm`` for pricing, so a
        top-level import here would close the cycle at ``build_provider`` time.
        """
        from agentcore.billing.call_quota import enforce_call_quota

        await enforce_call_quota(
            provider_name=self._provider_name(),
            model=request.model,
            scenario=request.scenario,
        )

    def _sync_platform_credential_context(self, request: LLMRequest) -> None:
        """Stamp or drop ambient ``platform_credential_id`` for this leaf call.

        Platform leaf re-resolves per ``request.model`` (一 key 一模型). BYOK /
        vendor unbind so a prior platform extra in the same task cannot leak
        onto their logs or ledger.
        """
        from agentcore.llm.credentials import bind_platform_credential_id

        if self._provider_name() != "platform":
            bind_platform_credential_id(None)
            return
        from agentcore.llm.resolve import platform_llm_credentials

        creds = platform_llm_credentials(model=request.model)
        bind_platform_credential_id(creds.platform_credential_id if creds is not None else None)

    def _latch_credential_source(self) -> str:
        """Payer of this leaf (``platform`` name → platform; everything else → user)."""
        from agentcore.llm.turn_auth_dead import credential_source_from_provider_name

        return credential_source_from_provider_name(self._provider_name())

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from agentcore.llm.turn_auth_dead import mark_turn_auth_dead, raise_if_turn_auth_dead

        self._sync_platform_credential_context(request)
        start = time.monotonic()
        try:
            raise_if_turn_auth_dead(self._latch_credential_source())
            await self._refuse_if_quota_spent(request)
            response = await self._inner.complete(request)
        except Exception as e:
            mark_turn_auth_dead(e)
            log_llm_call_failed(
                scenario=request.scenario,
                model=request.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                attempt=self._attempt_from_exc(e),
                error=str(e),
                error_type=type(e).__name__,
                stream=False,
                **self._upstream_log_fields(e),
            )
            raise
        log_llm_call(
            scenario=request.scenario,
            model=response.model or request.model,
            usage=response.usage,
            finish_reason=response.finish_reason,
            latency_ms=response.latency_ms
            if response.latency_ms
            else int((time.monotonic() - start) * 1000),
            stream=False,
            messages=request.messages,
            content=response.content,
            reasoning=response.reasoning_content,
            tool_names=[tc.function.name for tc in response.tool_calls]
            if response.tool_calls
            else None,
            provider_name=self._provider_name(),
            attempt=1,
        )
        return response

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        from agentcore.llm.turn_auth_dead import mark_turn_auth_dead, raise_if_turn_auth_dead

        self._sync_platform_credential_context(request)
        start = time.monotonic()
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_names: list[str] = []
        seen_tool_names: set[str] = set()
        aborted = False
        outcome = "open"
        try:
            raise_if_turn_auth_dead(self._latch_credential_source())
            await self._refuse_if_quota_spent(request)
            async for chunk in self._inner.stream(request):
                if chunk.aborted:
                    aborted = True
                if chunk.stream_reset:
                    content_parts.clear()
                    reasoning_parts.clear()
                    tool_names.clear()
                    seen_tool_names.clear()
                    usage = None
                    finish_reason = None
                    aborted = False
                else:
                    if chunk.delta_content:
                        content_parts.append(chunk.delta_content)
                    if chunk.delta_reasoning:
                        reasoning_parts.append(chunk.delta_reasoning)
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.delta_tool_calls:
                        for tc in chunk.delta_tool_calls:
                            name = tc.function_name
                            if name and name not in seen_tool_names:
                                seen_tool_names.add(name)
                                tool_names.append(name)
                yield chunk
            outcome = "ok"
        except GeneratorExit:
            if outcome != "ok":
                outcome = "closed"
            raise
        except Exception as e:
            outcome = "failed"
            mark_turn_auth_dead(e)
            log_llm_call_failed(
                scenario=request.scenario,
                model=request.model,
                latency_ms=int((time.monotonic() - start) * 1000),
                attempt=self._attempt_from_exc(e),
                error=str(e),
                error_type=type(e).__name__,
                stream=True,
                **self._upstream_log_fields(e),
            )
            raise
        finally:
            # Metering: only ``log_llm_call`` enqueues spend. On consumer aclose /
            # mid-stream failure, still bill **already-seen** usage; never invent
            # tokens when none arrived. Complete streams stay on the ``ok`` path.
            latency_ms = int((time.monotonic() - start) * 1000)
            if outcome == "ok":
                log_llm_call(
                    scenario=request.scenario,
                    model=request.model,
                    usage=usage,
                    finish_reason=finish_reason
                    or ("aborted" if aborted else ("tool_calls" if tool_names else "stop")),
                    latency_ms=latency_ms,
                    stream=True,
                    messages=request.messages,
                    content="".join(content_parts) or None,
                    reasoning="".join(reasoning_parts) or None,
                    tool_names=tool_names or None,
                    provider_name=self._provider_name(),
                    attempt=1,
                )
            elif outcome == "closed":
                if self._billable_usage(usage):
                    log_llm_call(
                        scenario=request.scenario,
                        model=request.model,
                        usage=usage,
                        finish_reason=finish_reason or ("aborted" if aborted else "stream_closed"),
                        latency_ms=latency_ms,
                        stream=True,
                        messages=request.messages,
                        content="".join(content_parts) or None,
                        reasoning="".join(reasoning_parts) or None,
                        tool_names=tool_names or None,
                        provider_name=self._provider_name(),
                        attempt=1,
                    )
                else:
                    log_llm_call_failed(
                        scenario=request.scenario,
                        model=request.model,
                        latency_ms=latency_ms,
                        attempt=1,
                        error="stream_closed_by_consumer",
                        error_type="GeneratorExit",
                        stream=True,
                    )
            elif outcome == "failed" and self._billable_usage(usage):
                # ``log_llm_call_failed`` already ran in ``except``; salvage spend only.
                log_llm_call(
                    scenario=request.scenario,
                    model=request.model,
                    usage=usage,
                    finish_reason=finish_reason or "error",
                    latency_ms=latency_ms,
                    stream=True,
                    messages=request.messages,
                    content="".join(content_parts) or None,
                    reasoning="".join(reasoning_parts) or None,
                    tool_names=tool_names or None,
                    provider_name=self._provider_name(),
                    attempt=1,
                )


def observe_provider(provider: LLMProvider) -> LLMProvider:
    """Wrap ``provider`` once (idempotent)."""
    if isinstance(provider, ObservingLLMProvider):
        return provider
    return ObservingLLMProvider(provider)


def unwrap_provider(provider: Any) -> Any:
    """Return the leaf under an observing fence (tests / diagnostics)."""
    if isinstance(provider, ObservingLLMProvider):
        return provider._inner
    return provider
