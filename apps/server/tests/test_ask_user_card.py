"""ask_user card=proposal_pick / risk_ack validation + intent override."""

from __future__ import annotations

import json
from pathlib import Path

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.ask_user.card import (
    parse_card,
    validate_card_shape,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
    )


def _tool(*, saver=None) -> AskUserTool:
    return AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=1.0,
        message_id="m1" if saver else None,
        suspension_saver=saver,
        captain_run_id="ceo",
        base_system_prompt="sys",
        user_message="hi",
    )


def _proposal_questions(*, n: int = 3, multiple: bool = False, kind: str = "choice"):
    return [
        {
            "prompt": "选哪条？",
            "kind": kind,
            "multiple": multiple,
            "options": [f"方案 {i}" for i in range(n)],
        }
    ]


def _risk_questions(*, n: int = 3, multiple: bool = True, kind: str = "choice"):
    return [
        {
            "prompt": "勾选风险",
            "kind": kind,
            "multiple": multiple,
            "options": [f"风险 {i}" for i in range(n)],
        }
    ]


def test_ask_user_schema_does_not_expose_blocking():
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
    )
    props = tool.schema.parameters["properties"]
    assert "blocking" not in props
    blob = tool.schema.description + json.dumps(tool.schema.parameters, ensure_ascii=False)
    assert "blocking" not in blob


def test_parse_card_unknown():
    err = parse_card("foo")
    assert isinstance(err, str) and "proposal_pick" in err


def test_validate_proposal_pick_matrix():
    ok_q = [
        {
            "id": "q0",
            "prompt": "选",
            "kind": "choice",
            "multiple": False,
            "options": [{"label": "A"}, {"label": "B"}],
            "default": "",
        }
    ]
    assert validate_card_shape("proposal_pick", questions=ok_q) is None
    assert validate_card_shape("proposal_pick", questions=[])
    bad_multi = [{**ok_q[0], "multiple": True}]
    err_multi = validate_card_shape("proposal_pick", questions=bad_multi) or ""
    assert "multiple=false" in err_multi
    one_opt = [{**ok_q[0], "options": [{"label": "A"}]}]
    err_opts = validate_card_shape("proposal_pick", questions=one_opt) or ""
    assert "2" in err_opts and "6" in err_opts


def test_validate_risk_ack_matrix():
    ok_q = [
        {
            "id": "q0",
            "prompt": "勾选",
            "kind": "choice",
            "multiple": True,
            "options": [{"label": "R1"}],
            "default": "",
        }
    ]
    assert validate_card_shape("risk_ack", questions=ok_q) is None
    single = [{**ok_q[0], "multiple": False}]
    err_single = validate_card_shape("risk_ack", questions=single) or ""
    assert "multiple=true" in err_single
    too_many = [{**ok_q[0], "options": [{"label": f"r{i}"} for i in range(11)]}]
    err_many = validate_card_shape("risk_ack", questions=too_many) or ""
    assert "1" in err_many and "10" in err_many


async def test_proposal_pick_overrides_transcript_intent():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    # Transcript would derive kickoff; card must override to proposal_pick.
    transcript = [
        LLMMessage(role="user", content="做网站"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="ask",
                    function=ToolCallFunction(
                        name="ask_user",
                        arguments=json.dumps({"message": "挑方案", "card": "proposal_pick"}),
                    ),
                )
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        res = await tool.execute(
            {
                "message": "挑方案",
                "card": "proposal_pick",
                "questions": _proposal_questions(),
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    assert saved[0].intent == "proposal_pick"
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert required.payload["intent"] == "proposal_pick"
    assert required.payload["questions"][0]["multiple"] is False
    assert 2 <= len(required.payload["questions"][0]["options"]) <= 6


async def test_risk_ack_accepts_up_to_ten_options():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    transcript = [
        LLMMessage(role="user", content="上线"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="ask",
                    function=ToolCallFunction(
                        name="ask_user",
                        arguments=json.dumps({"message": "勾选风险", "card": "risk_ack"}),
                    ),
                )
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        res = await tool.execute(
            {
                "message": "勾选风险",
                "card": "risk_ack",
                "questions": _risk_questions(n=10),
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    assert saved[0].intent == "risk_ack"
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert required.payload["intent"] == "risk_ack"
    assert required.payload["questions"][0]["multiple"] is True
    assert len(required.payload["questions"][0]["options"]) == 10


async def test_proposal_pick_rejects_bad_shape():
    tool = _tool()
    res = await tool.execute(
        {
            "message": "挑方案",
            "card": "proposal_pick",
            "questions": _proposal_questions(n=1),
        },
        _ctx(),
    )
    assert res.success is False
    assert res.error and "proposal_pick" in res.error
