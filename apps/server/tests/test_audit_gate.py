"""Soft audit-gate nudge for the CEO captain ReAct loop (协作优先返工环).

Covers trigger conditions, one-shot latch, second-delegate suppression, light-batch
skip, worker isolation, and nudge copy keywords. Scripted fake provider — zero LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.profiles import ProfileParams
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.captain_profile import apply_captain_max_rounds
from agentcore.runtime.engine import react_loop
from agentcore.runtime.engine.governance import (
    audit_gate_hard_prompt,
    audit_gate_nudge_prompt,
    coordination_injection_has_all_completed,
)
from agentcore.runtime.events import EventSink
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params


def _tool_chunk(name: str, args: str, *, call_id: str = "c") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


def _substantial_tasks_args() -> str:
    tasks = [
        {"role": "调研A", "task": "调研对象A"},
        {"role": "调研B", "task": "调研对象B"},
        {"role": "汇总", "task": "横向对比", "depends_on": ["t1"]},
    ]
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


def _light_tasks_args() -> str:
    tasks = [
        {"role": "工人甲", "task": "做甲"},
        {"role": "工人乙", "task": "做乙"},
    ]
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


class _ScriptedProvider:
    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


class _StubTool:
    def __init__(
        self,
        name: str = "search",
        *,
        category: ToolCategory = ToolCategory.SEARCH,
    ) -> None:
        self._name = name
        self._category = category
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=self._category,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=True,
            output="result",
            effect=ToolEffect.CONTINUE,
        )


def _registry(*tools: _StubTool) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


def _audit_gate_msgs(messages: list[LLMMessage]) -> list[LLMMessage]:
    return [
        m
        for m in messages
        if m.role == "user"
        and m.content
        and (
            "收尾前审计复核" in m.content
            or "成篇审计硬门" in m.content
        )
    ]


async def _run_captain(
    provider: _ScriptedProvider,
    tools: ToolRegistry,
    *,
    role: str = "captain",
    max_rounds: int = 20,
) -> tuple[str, list[LLMMessage]]:
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    profile = make_profile_params(max_rounds=max_rounds)
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=tools,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        role=role,
        approval_gate=None,
    )
    return content, messages


def test_nudge_copy_cites_audit_keywords():
    text = audit_gate_nudge_prompt()
    assert "独立审计" in text
    assert "审计者≠作者" in text
    assert "向用户收口" in text
    assert "不是默认路径" in text
    assert "≤2 轮" in text  # banned default-rework framing (negated in copy)
    assert "禁止把「审完默认修订≤2 轮」" in text
    assert "绝不代派" in text
    assert "成文专线" in text or "结构长文" in text


def test_hard_prompt_cites_new_playbook_ids():
    text = audit_gate_hard_prompt()
    assert "playbook=cite_write_review" in text
    assert "playbook=map_fanout" in text
    assert "research_report" not in text
    assert "parallel_brief" not in text


def test_coordination_injection_has_all_completed():
    assert coordination_injection_has_all_completed(
        [LLMMessage(role="user", content="- all_completed：团队已全部结束")]
    )
    assert not coordination_injection_has_all_completed(
        [LLMMessage(role="user", content="- worker_completed")]
    )


def test_apply_captain_max_rounds_default_does_not_raise():
    base = ProfileParams(max_rounds=0, name="chat")
    assert apply_captain_max_rounds(base).max_rounds == 0
    high = ProfileParams(max_rounds=32, name="chat")
    assert apply_captain_max_rounds(high).max_rounds == 32


def test_apply_captain_max_rounds_raises_when_configured(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "engine_captain_max_rounds", 32)
    base = ProfileParams(max_rounds=0, name="chat")
    assert apply_captain_max_rounds(base).max_rounds == 32


def test_should_audit_gate_requires_hard_flag():
    """Soft gate aligns with hard gate: substantial alone is not enough."""
    from agentcore.runtime.engine.governance import should_audit_gate
    from agentcore.runtime.loop_controller import LoopController

    c = LoopController()
    c.mark_post_delegate(node_count=5, has_deps=True)  # substantial, no audit_hard
    assert should_audit_gate(c, role="captain") is False

    c2 = LoopController()
    c2.mark_post_delegate(node_count=5, has_deps=True, audit_hard=True)
    assert should_audit_gate(c2, role="captain") is True


class _AuditHardStubTool(_StubTool):
    """Delegate stub that stamps audit_hard so soft gate can fire in integration tests.

    Also stamps includes_review so the hard block does not discard the post-nudge
    wrap-up (mirrors cite_write_review playbook with built-in review).
    """

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        return ToolResult(
            tool_call_id="",
            success=True,
            output="result",
            effect=ToolEffect.CONTINUE,
            metadata={
                "audit_hard": True,
                "batch_includes_review": True,
                "batch_nodes": 3,
                "batch_has_deps": True,
            },
        )


@pytest.mark.asyncio
async def test_substantial_batch_fires_once_on_wrap_up():
    delegate = _AuditHardStubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_content_chunk("综述草稿")],
            [_content_chunk("最终交付")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "最终交付"
    gates = _audit_gate_msgs(messages)
    assert len(gates) == 1
    assert "独立审计" in (gates[0].content or "")
    assert "审计者≠作者" in (gates[0].content or "")
    assert "向用户收口" in (gates[0].content or "")


@pytest.mark.asyncio
async def test_hard_required_without_review_blocks_then_second_delegate_delivers():
    """audit_hard without includes_review: soft→hard block; second batch unblocks."""

    class _HardNoReview(_StubTool):
        def __init__(self) -> None:
            super().__init__(name="delegate", category=ToolCategory.ORCHESTRATION)
            self._n = 0

        async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
            self.calls += 1
            self._n += 1
            if self._n == 1:
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output="result",
                    effect=ToolEffect.CONTINUE,
                    metadata={
                        "audit_hard": True,
                        "batch_nodes": 3,
                        "batch_has_deps": True,
                    },
                )
            return ToolResult(
                tool_call_id="",
                success=True,
                output="result",
                effect=ToolEffect.CONTINUE,
                metadata={
                    "audit_hard": True,
                    "batch_includes_review": True,
                    "batch_nodes": 1,
                    "batch_has_deps": False,
                },
            )

    delegate = _HardNoReview()
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_content_chunk("半残稿")],  # soft nudge
            [_content_chunk("仍想收尾")],  # hard block discards
            [_tool_chunk("delegate", _light_tasks_args(), call_id="d2")],
            [_content_chunk("审后收口")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))
    assert content == "审后收口"
    assert any("成篇审计硬门" in (m.content or "") for m in messages if m.role == "user")


@pytest.mark.asyncio
async def test_substantial_without_audit_hard_skips_soft_gate():
    """map_fanout / ordinary multi-angle: substantial but no hard → no soft nudge."""
    delegate = _StubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_content_chunk("摸底综述")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "摸底综述"
    assert _audit_gate_msgs(messages) == []


@pytest.mark.asyncio
async def test_fires_at_most_once():
    delegate = _AuditHardStubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_content_chunk("第一次收尾")],
            [_content_chunk("第二次收尾")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "第二次收尾"
    assert len(_audit_gate_msgs(messages)) == 1


@pytest.mark.asyncio
async def test_second_delegate_suppresses_gate():
    delegate = _AuditHardStubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d2")],
            [_content_chunk("综述")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "综述"
    assert _audit_gate_msgs(messages) == []


@pytest.mark.asyncio
async def test_light_batch_no_gate():
    # 2 nodes, no depends_on → not substantial (even with audit_hard).
    class _HardLight(_StubTool):
        async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
            self.calls += 1
            return ToolResult(
                tool_call_id="",
                success=True,
                output="result",
                effect=ToolEffect.CONTINUE,
                metadata={"audit_hard": True, "batch_nodes": 2, "batch_has_deps": False},
            )

    delegate = _HardLight(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _light_tasks_args(), call_id="d1")],
            [_content_chunk("轻批综述")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "轻批综述"
    assert _audit_gate_msgs(messages) == []


@pytest.mark.asyncio
async def test_light_batch_with_deps_is_substantial():
    tasks = [
        {"role": "甲", "task": "写"},
        {"role": "乙", "task": "审", "depends_on": ["a"]},
    ]
    args = json.dumps({"tasks": tasks}, ensure_ascii=False)
    delegate = _AuditHardStubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", args, call_id="d1")],
            [_content_chunk("草稿")],
            [_content_chunk("交付")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))

    assert content == "交付"
    assert len(_audit_gate_msgs(messages)) == 1


@pytest.mark.asyncio
async def test_worker_role_never_fires():
    delegate = _AuditHardStubTool(name="delegate", category=ToolCategory.ORCHESTRATION)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", _substantial_tasks_args(), call_id="d1")],
            [_content_chunk("工人收尾")],
        ]
    )
    content, messages = await _run_captain(
        provider, _registry(delegate), role="worker"
    )

    assert content == "工人收尾"
    assert _audit_gate_msgs(messages) == []
