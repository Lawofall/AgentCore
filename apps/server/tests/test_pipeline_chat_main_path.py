"""Behavioral main-path tests for ``run_chat_pipeline``.

Drives the real pipeline with a scripted LLM + stub CEO toolset (patched at the
lookup seams used by ``test_pipeline_governance``). Asserts public outcomes only:
``result`` content / finish_reason and ``message_end`` — never CEO assemble structure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentcore.core.types import ToolFace
from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime import pipeline
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.tools.protocol import ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles


def _tool_chunk(name: str, args: str = "{}", *, call_id: str = "c1") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:
        return None


class _FailingProvider:
    """First stream call raises (provider retries already exhausted)."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        self.calls += 1
        raise RuntimeError("provider boom")
        yield  # pragma: no cover — make this an async generator

    async def close(self) -> None:
        return None


class _StubTool:
    def __init__(self, name: str = "search", *, success: bool = True) -> None:
        self._name = name
        self._success = success
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        if not self._success:
            return ToolResult(tool_call_id="", success=False, output="", error="boom")
        return ToolResult(tool_call_id="", success=True, output="tool-ok")


def _patch_pipeline(monkeypatch, provider, registry: ToolRegistry) -> None:
    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)

    class _FakeStore:
        async def load(self, _user_id: str, _path: str, scope: str | None = None) -> str:
            return ""

        async def list(self, _user_id: str, scope: str | None = None) -> list:
            return []

    monkeypatch.setattr("agentcore.runtime.pipeline.run.default_memory_store", lambda: _FakeStore())

    async def _noop_dispose() -> None:
        return None

    fake_delegate = SimpleNamespace(
        usage={},
        run_ledger=[],
        citations=[],
        dispose_open_supervised=_noop_dispose,
        collab={"boundary_yields": 0, "scope_signals": 0, "escalations": 0},
        continuation_count=0,
        user_continuation_count=0,
    )
    fake_debate = SimpleNamespace(usage={}, run_ledger=[], citations=[])

    def _fake_assemble(**_kwargs):
        return fake_delegate, fake_debate, registry

    monkeypatch.setattr("agentcore.runtime.pipeline.run._assemble_ceo_toolset", _fake_assemble)


async def _run(monkeypatch, provider, registry: ToolRegistry):
    _patch_pipeline(monkeypatch, provider, registry)
    sink = EventSink()
    backend = ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())
    result = await pipeline.run_chat_pipeline(
        conversation_id="conv-main",
        user_message="你好",
        history=[],
        sink=sink,
        user_id="user-1",
        backend=backend,
        approvals_enabled=False,
        profile_set=make_turn_profiles(model="chat-model"),
    )
    sink.close()
    events = [e async for e in sink]
    return result, events


def _message_end(events):
    return next(e for e in events if e.type == EventType.MESSAGE_END)


async def test_pipeline_content_only_end_turn(monkeypatch):
    registry = ToolRegistry()
    registry.register(_StubTool("noop", success=True))
    provider = _ScriptedProvider([[_content_chunk("直接答复")]])
    result, events = await _run(monkeypatch, provider, registry)

    assert result["content"] == "直接答复"
    assert result["finish_reason"] == FinishReason.END_TURN
    assert _message_end(events).payload["finish_reason"] == FinishReason.END_TURN
    assert provider.calls == 1


async def test_pipeline_tool_then_answer_end_turn(monkeypatch):
    tool = _StubTool("search", success=True)
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("search", '{"q":"x"}')],
            [_content_chunk("综合工具结果")],
        ]
    )
    result, events = await _run(monkeypatch, provider, registry)

    assert tool.calls == 1
    assert result["content"] == "综合工具结果"
    assert result["finish_reason"] == FinishReason.END_TURN
    assert _message_end(events).payload["finish_reason"] == FinishReason.END_TURN


async def test_pipeline_hard_llm_failure_surfaces_error(monkeypatch):
    registry = ToolRegistry()
    registry.register(_StubTool("noop", success=True))
    provider = _FailingProvider()
    result, events = await _run(monkeypatch, provider, registry)

    assert result["content"] == ""
    assert result["finish_reason"] == FinishReason.ERROR
    assert _message_end(events).payload["finish_reason"] == FinishReason.ERROR
    assert any(e.type == EventType.ERROR for e in events)
    assert provider.calls == 1
