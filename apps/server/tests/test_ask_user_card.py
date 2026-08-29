"""ask_user card=organize_plan / daily_review validation + retired-name reject."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.builtin.ask_user.card import (
    CARD_KINDS,
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


def test_ask_user_schema_does_not_expose_blocking():
    tool = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
    )
    props = tool.schema.parameters["properties"]
    assert "blocking" not in props
    assert "context" not in props
    blob = tool.schema.description + json.dumps(tool.schema.parameters, ensure_ascii=False)
    assert "blocking" not in blob
    assert '"context"' not in json.dumps(tool.schema.parameters, ensure_ascii=False)
    card_enum = props["card"]["enum"]
    assert card_enum == ["organize_plan", "daily_review"]
    assert frozenset(card_enum) == CARD_KINDS


async def test_ask_user_drops_extra_context_key():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    token = captain_transcript.set([LLMMessage(role="user", content="选")])
    try:
        res = await tool.execute(
            {"message": "只问这一句", "context": "旧槽不该并进"},
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert saved[0].question == "只问这一句"
    assert "context" not in saved[0].to_json()
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert required.payload["question"] == "只问这一句"
    assert "context" not in required.payload


def test_parse_card_unknown():
    err = parse_card("foo")
    assert isinstance(err, str) and "organize_plan" in err and "daily_review" in err


@pytest.mark.parametrize("card", ["proposal_pick", "risk_ack", "kickoff"])
async def test_schema_rejects_retired_card_names(card):
    """Write path rejects retired names; does not rewrite to decision."""
    tool = _tool()
    res = await tool.execute(
        {
            "message": "挑方案",
            "card": card,
            "questions": [
                {
                    "prompt": "选哪条？",
                    "kind": "choice",
                    "multiple": False,
                    "options": ["A", "B"],
                }
            ],
        },
        _ctx(),
    )
    assert res.success is False
    assert res.error and "未知 card" in res.error
    assert not any(e.type is EventType.CHECKPOINT_REQUIRED for e in tool.sink._history)


def test_validate_organize_plan_matrix():
    ok_q = [
        {
            "id": "q0",
            "prompt": "勾选",
            "kind": "choice",
            "multiple": True,
            "options": [{"label": "a → b", "op": "move", "source": "a", "destination": "b"}],
            "default": "",
        }
    ]
    assert validate_card_shape("organize_plan", questions=ok_q) is None
    assert validate_card_shape("organize_plan", questions=[])
    single = [{**ok_q[0], "multiple": False}]
    err_single = validate_card_shape("organize_plan", questions=single) or ""
    assert "multiple=true" in err_single


async def test_organize_plan_overrides_transcript_intent():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    transcript = [
        LLMMessage(role="user", content="整理桌面"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="ask",
                    function=ToolCallFunction(
                        name="ask_user",
                        arguments=json.dumps({"message": "保留哪些", "card": "organize_plan"}),
                    ),
                )
            ],
        ),
    ]
    token = captain_transcript.set(transcript)
    try:
        res = await tool.execute(
            {
                "message": "保留哪些",
                "card": "organize_plan",
                "questions": [
                    {
                        "prompt": "保留哪些操作？",
                        "kind": "choice",
                        "multiple": True,
                        "options": [
                            {
                                "label": "a → b",
                                "op": "move",
                                "source": "a",
                                "destination": "b",
                            }
                        ],
                    }
                ],
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert res.effect is ToolEffect.SUSPEND
    assert saved[0].intent == "organize_plan"
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert required.payload["intent"] == "organize_plan"
    assert required.payload["questions"][0]["multiple"] is True


async def test_ordinary_choice_ask_is_decision():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    token = captain_transcript.set([LLMMessage(role="user", content="选方案")])
    try:
        res = await tool.execute(
            {
                "message": "挑一条推进",
                "questions": [
                    {
                        "prompt": "选哪条方案？",
                        "kind": "choice",
                        "multiple": False,
                        "options": ["方案 A：快速原型", "方案 B：稳妥重构（推荐）"],
                    }
                ],
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)

    assert res.success is True
    assert saved[0].intent == "decision"
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert required.payload["intent"] == "decision"
    assert required.payload["questions"][0]["multiple"] is False


async def test_ordinary_ask_caps_choice_options_at_six():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    token = captain_transcript.set([LLMMessage(role="user", content="勾选")])
    try:
        res = await tool.execute(
            {
                "message": "勾选要处理的风险",
                "questions": [
                    {
                        "prompt": "勾选要处理的风险",
                        "kind": "choice",
                        "multiple": True,
                        "options": [f"风险 {i}" for i in range(8)],
                    }
                ],
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    assert len(required.payload["questions"][0]["options"]) == 6


def _detailed_choice(*, n: int, multiple: bool, detail: str = "一行取舍"):
    return [
        {
            "prompt": "选？",
            "kind": "choice",
            "multiple": multiple,
            "options": [{"label": f"项 {i}", "detail": detail} for i in range(n)],
        }
    ]


def _with_ask_transcript(*, message: str, card: str | None = None):
    args: dict = {"message": message}
    if card is not None:
        args["card"] = card
    return captain_transcript.set(
        [
            LLMMessage(role="user", content="hi"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="ask",
                        function=ToolCallFunction(
                            name="ask_user",
                            arguments=json.dumps(args),
                        ),
                    )
                ],
            ),
        ]
    )


async def test_ordinary_ask_drops_option_detail_even_if_model_filled():
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    token = _with_ask_transcript(message="选方向")
    try:
        res = await tool.execute(
            {
                "message": "选方向",
                "questions": _detailed_choice(n=2, multiple=False, detail="不该出现"),
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True
    assert saved
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    opts = required.payload["questions"][0]["options"]
    assert [o["label"] for o in opts] == ["项 0", "项 1"]
    assert all("detail" not in o for o in opts)


@pytest.mark.parametrize(
    "card,n,multiple",
    [
        ("organize_plan", 1, True),
        ("daily_review", 1, True),
    ],
)
async def test_dedicated_card_keeps_option_detail(card, n, multiple):
    saved: list = []

    async def _save(frame):
        saved.append(frame)

    tool = _tool(saver=_save)
    token = _with_ask_transcript(message="专用卡", card=card)
    try:
        questions = _detailed_choice(n=n, multiple=multiple)
        if card == "daily_review":
            for opt in questions[0]["options"]:
                opt["review_kind"] = "preference"
                opt["body"] = "x"
        elif card == "organize_plan":
            for i, opt in enumerate(questions[0]["options"]):
                opt["op"] = "mkdir"
                opt["path"] = f"p{i}"
        res = await tool.execute(
            {
                "message": "专用卡",
                "card": card,
                "questions": questions,
            },
            _ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True, res.error
    assert saved
    required = next(e for e in tool.sink._history if e.type is EventType.CHECKPOINT_REQUIRED)
    opts = required.payload["questions"][0]["options"]
    assert all(o.get("detail") == "一行取舍" for o in opts)
    assert saved[0].intent == card
