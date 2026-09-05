"""Debate-form parser tests; engine commitment gate is withdrawn."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.core.types import ToolCategory, ToolEffect
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.runtime.engine import react_loop
from agentcore.runtime.engine.debate_commitment import (
    user_selected_debate_form,
)
from agentcore.runtime.engine.governance import (
    note_delegate_batches,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.loop_controller import LoopController, ToolAttempt
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
        name: str,
        *,
        category: ToolCategory = ToolCategory.ORCHESTRATION,
        meta: dict | None = None,
    ) -> None:
        self._name = name
        self._category = category
        self._meta = meta or {"batch_nodes": 3, "batch_has_deps": True}
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
            output="ok",
            effect=ToolEffect.CONTINUE,
            metadata=dict(self._meta),
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


def _kickoff_ask_user_args(*, default: str = "辩论（正反攻防）") -> str:
    return json.dumps(
        {
            "message": "开工提案",
            "questions": [
                {
                    "prompt": "辩论环节采用哪种形式？",
                    "kind": "choice",
                    "options": [
                        {"label": "辩论（正反攻防）"},
                        {"label": "红队压测"},
                        {"label": "圆桌讨论"},
                        {"label": "不需要辩论环节"},
                    ],
                    "default": default,
                }
            ],
        },
        ensure_ascii=False,
    )


def _settled_reply_with_form(form: str = "辩论（正反攻防）") -> str:
    return (
        "用户答复：\n"
        f"· 辩论环节采用哪种形式？：{form}\n"
        "请据此继续。"
    )


def _debate_gate_msgs(messages: list[LLMMessage]) -> list[LLMMessage]:
    return [
        m
        for m in messages
        if m.role == "user" and m.content and "辩论承诺复核" in m.content
    ]


async def _run_captain(
    provider: _ScriptedProvider,
    tools: ToolRegistry,
    *,
    history: list[LLMMessage] | None = None,
) -> tuple[str, list[LLMMessage]]:
    messages: list[LLMMessage] = list(history or [LLMMessage(role="user", content="go")])
    content, *_ = await react_loop(
        messages=messages,
        llm=provider,
        tools=tools,
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=12),
        turn_model="m",
        role="captain",
        approval_gate=None,
    )
    return content or "", messages


def test_nudge_copy_withdrawn():
    from agentcore.runtime.engine import debate_commitment as mod

    assert not hasattr(mod, "debate_gate_nudge_prompt")


def test_user_selected_from_settled_reply():
    messages = [
        LLMMessage(role="tool", content=_settled_reply_with_form("辩论（正反攻防）")),
    ]
    assert user_selected_debate_form(messages) is True


def test_user_declined_debate_form():
    messages = [
        LLMMessage(role="tool", content=_settled_reply_with_form("不需要辩论环节")),
    ]
    assert user_selected_debate_form(messages) is False


def test_user_confirm_honors_ask_user_default():
    args = _kickoff_ask_user_args(default="辩论（正反攻防）")
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="au1",
                    function=ToolCallFunction(name="ask_user", arguments=args),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="au1",
            content="用户确认：按你提出的方向继续。",
        ),
    ]
    assert user_selected_debate_form(messages) is True


def test_user_confirm_honors_recommendation_mark_without_default():
    args = json.dumps(
        {
            "message": "开工提案",
            "questions": [
                {
                    "prompt": "辩论环节采用哪种形式？",
                    "kind": "choice",
                    "options": [
                        {"label": "辩论（正反攻防）（推荐）"},
                        {"label": "红队压测"},
                        {"label": "不需要辩论环节"},
                    ],
                    "default": "",
                }
            ],
        },
        ensure_ascii=False,
    )
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="au1",
                    function=ToolCallFunction(name="ask_user", arguments=args),
                )
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="au1",
            content="用户确认：按你提出的方向继续。",
        ),
    ]
    assert user_selected_debate_form(messages) is True


def test_silent_when_no_kickoff_signal():
    messages = [
        LLMMessage(role="user", content="写一份报告"),
        LLMMessage(role="assistant", content="好的"),
    ]
    assert user_selected_debate_form(messages) is False


def test_should_debate_gate_matrix():
    from agentcore.runtime.engine import governance as gov

    assert not hasattr(gov, "should_debate_gate")


def test_note_delegate_batches_marks_debate_executed():
    controller = LoopController()
    tc = ToolCall(
        id="d1",
        function=ToolCallFunction(name="debate", arguments='{"motion":"m","form":"debate"}'),
    )
    attempt = ToolAttempt(
        fingerprint="debate:1",
        tool_name="debate",
        success=True,
        meta={"batch_nodes": 2},
    )
    note_delegate_batches(controller, [tc], [attempt])
    assert controller.debate_executed is True


@pytest.mark.asyncio
async def test_selected_debate_not_executed_fires_nudge():
    # Debate gate only: one discarded wrap-up draft, then real delivery.
    # Without audit_hard the audit gate does not burn another round.
    delegate = _StubTool("delegate")
    history = [
        LLMMessage(role="tool", content=_settled_reply_with_form("辩论（正反攻防）")),
    ]
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", json.dumps({"tasks": [{"role": "a", "task": "t"}]}), call_id="d1")],
            [_content_chunk("汇总已含论证，直接收官")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate), history=history)
    assert content == "汇总已含论证，直接收官"
    assert _debate_gate_msgs(messages) == []


@pytest.mark.asyncio
async def test_no_selection_does_not_fire():
    # No debate signal → debate gate silent; no audit_hard → no audit burn either.
    delegate = _StubTool("delegate")
    provider = _ScriptedProvider(
        [
            [_tool_chunk("delegate", json.dumps({"tasks": [{"role": "a", "task": "t"}]}), call_id="d1")],
            [_content_chunk("综述交付")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(delegate))
    assert content == "综述交付"
    assert _debate_gate_msgs(messages) == []


@pytest.mark.asyncio
async def test_already_executed_debate_does_not_fire():
    history = [
        LLMMessage(role="tool", content=_settled_reply_with_form("辩论（正反攻防）")),
    ]
    debate = _StubTool("debate", meta={"batch_nodes": 2, "batch_has_deps": False})
    provider = _ScriptedProvider(
        [
            [_tool_chunk("debate", json.dumps({"motion": "m", "form": "debate"}), call_id="db1")],
            [_content_chunk("辩论后收官")],
        ]
    )
    content, messages = await _run_captain(provider, _registry(debate), history=history)
    assert content == "辩论后收官"
    assert debate.calls == 1
    assert _debate_gate_msgs(messages) == []


def test_maybe_inject_is_one_shot():
    from agentcore.runtime.engine import governance as gov

    assert not hasattr(gov, "maybe_inject_debate_gate")
