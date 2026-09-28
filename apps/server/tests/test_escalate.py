"""EscalateTool — ``reason`` 三选一；日志带 question / assumption / reason。"""

import json
from pathlib import Path

import pytest

import agentcore.tools.builtin.escalate as escalate_mod
from agentcore.runtime.events.interaction import escalation_required
from agentcore.tools.builtin.escalate import EscalateTool
from agentcore.tools.protocol import EscalationChannel, EscalationOutcome, ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.conftest import LogSpy


def test_escalate_schema_has_no_recommended_field():
    props = (
        EscalateTool()
        .schema.parameters["properties"]["questions"]["items"]["properties"]["options"]["items"][
            "properties"
        ]
    )
    assert "recommended" not in props
    assert "（推荐）" not in props["label"]["description"]
    assert "放第一" not in props["label"]["description"]


def _ctx(**kwargs) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        **kwargs,
    )


async def test_worker_escalate_logs_question_and_assumption(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    result = await EscalateTool().execute(
        {
            "question": "该走方案A还是方案B?",
            "assumption": "暂按方案A继续",
            "reason": "adjust",
        },
        _ctx(),
    )

    assert result.success is True
    esc = spy.get("worker.escalate")
    assert esc["run_id"] == "w1"
    assert esc["reason"] == "adjust"
    assert "blocking" not in esc
    assert "kind" not in esc
    assert esc["has_assumption"] is True
    assert esc["question"] == "该走方案A还是方案B?"
    assert esc["assumption"] == "暂按方案A继续"


async def test_worker_escalate_question_preview_is_capped(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    await EscalateTool().execute(
        {"question": "为" * 500, "assumption": "暂按已有口径", "reason": "adjust"},
        _ctx(),
    )

    esc = spy.get("worker.escalate")
    assert esc["question"].endswith("…")
    assert len(esc["question"]) == 201  # 200-char cap + the one ellipsis char
    assert esc["has_assumption"] is True
    assert esc["reason"] == "adjust"


def test_escalation_required_carries_timeout_only_when_ops_configured_one():
    """诚实性：默认部署无超时 ⇒ 字段缺席，卡面不得承诺「未答则按假设继续」。"""
    default_deploy = escalation_required(
        "r1",
        "a1",
        escalation_id="e1",
        question="该走哪个方案?",
        assumption="暂按 A",
        timeout_seconds=None,
    )
    assert "timeout_seconds" not in default_deploy.payload

    with_ceiling = escalation_required(
        "r1",
        "a1",
        escalation_id="e1",
        question="该走哪个方案?",
        assumption="暂按 A",
        timeout_seconds=1800.0,
    )
    assert with_ceiling.payload["timeout_seconds"] == 1800.0


def test_escalate_schema_teaches_reason_not_blocking():
    schema = EscalateTool().schema
    props = schema.parameters["properties"]
    assert "blocking" not in props
    assert "kind" not in props
    desc = schema.description
    assert "向上请示" in desc
    assert "等人定" in desc
    assert "小假设写进交差" in desc
    assert "报一声" not in desc
    assert "猜错作废" not in desc
    assert "默认 false" not in desc
    reason = props["reason"]["description"]
    assert "wait" in reason and "adjust" in reason
    assert "已拒凭据不要 wait" in reason
    assert props["reason"]["enum"] == ["wait", "adjust"]
    question = props["question"]["description"]
    assert "必填" not in question
    assert "要拍板" in question


def test_escalate_schema_stays_off_engine_internals():
    """协调模式 / 超时 / 未武装 是引擎行为，不写进按钮。"""
    schema = EscalateTool().schema
    blob = schema.description + json.dumps(schema.parameters, ensure_ascii=False)
    for phrase in ("协调模式", "经典路径", "near-verbatim", "未武装", "并发满"):
        assert phrase not in blob, phrase


def test_escalate_schema_options_are_one_line():
    """填卡 HOW 在 ask_user；escalate 按钮不抄权衡/推荐。"""
    props = (
        EscalateTool()
        .schema.parameters["properties"]["questions"]["items"]["properties"]["options"]["items"][
            "properties"
        ]
    )
    assert "detail" not in props
    blob = json.dumps(EscalateTool().schema.parameters, ensure_ascii=False)
    assert "第二句" not in blob
    assert "权衡写进" not in blob
    assert "（推荐）" not in blob


def test_escalate_questions_share_ask_user_card_shape():
    """卡形单源：键与 ask_user 相同；队员不广告本机 action；必填/minItems 仍分列。"""
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.ask_user import AskUserTool

    ask = AskUserTool(
        sink=EventSink(),
        conversation_id="c1",
        timeout_seconds=30.0,
    ).schema.parameters["properties"]["questions"]
    esc = EscalateTool().schema.parameters["properties"]["questions"]
    assert set(esc["items"]["properties"]) == set(ask["items"]["properties"])
    assert set(esc["items"]["properties"]) == {
        "prompt",
        "options",
        "multiple",
    }
    assert "action" not in esc["items"]["properties"]["options"]["items"]["properties"]
    assert "action" not in ask["items"]["properties"]["options"]["items"]["properties"]
    assert ask.get("minItems") == 1
    assert "minItems" not in esc
    assert "required" not in esc
    assert not esc["description"].startswith("可选")
    multiple = ask["items"]["properties"]["multiple"]["description"]
    assert not multiple.startswith("可选")


@pytest.mark.asyncio
async def test_wait_without_assumption_is_rejected():
    result = await EscalateTool().execute({"question": "选哪个库?"}, _ctx())
    assert result.success is False
    assert "assumption" in (result.error or "")


@pytest.mark.asyncio
async def test_unknown_reason_defaults_to_wait_and_requires_assumption():
    result = await EscalateTool().execute(
        {"question": "选哪个库?", "reason": "weird"},
        _ctx(),
    )
    assert result.success is False
    assert "assumption" in (result.error or "")


@pytest.mark.asyncio
async def test_wait_escalate_drops_option_detail():
    seen: dict = {}

    async def _request(q, a, questions, reason, awaiting="user", **kwargs):
        seen["questions"] = questions
        seen["reason"] = reason
        return EscalationOutcome(status="resolved", answer="方案A")

    ctx = _ctx(
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "该走哪个方案?",
            "assumption": "暂按方案A继续",
            "reason": "wait",
            "questions": [
                {
                    "prompt": "选一个方案",
                    "options": [
                        {"label": "方案A：先出契约", "detail": "慢但稳"},
                        {"label": "方案B：先一条主路径", "detail": "快但窄"},
                    ],
                }
            ],
        },
        ctx,
    )
    assert result.success is True
    assert seen["reason"] == "wait"
    opts = seen["questions"][0]["options"]
    assert [o["label"] for o in opts] == ["方案A：先出契约", "方案B：先一条主路径"]
    assert all("detail" not in o for o in opts)


@pytest.mark.asyncio
async def test_bracketed_recommendation_label_reaches_the_escalation_card():
    """Tendency markup in the option name is accepted; the card still opens."""
    seen: dict = {}

    async def _request(q, a, questions, reason, awaiting="user", **kwargs):
        seen["questions"] = questions
        return EscalationOutcome(status="resolved", answer="选方案A（推荐）")

    ctx = _ctx(
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "该走哪个方案?",
            "assumption": "暂按方案A继续",
            "questions": [
                {
                    "id": "plan",
                    "prompt": "选一个方案",
                    "options": [
                        {"id": "a", "label": "方案A（推荐）"},
                        {"id": "b", "label": "方案B"},
                    ],
                }
            ],
        },
        ctx,
    )

    assert result.success is True
    assert seen["questions"][0]["options"][0]["label"] == "方案A（推荐）"
    assert "recommended" not in seen["questions"][0]["options"][0]


@pytest.mark.asyncio
async def test_clean_labels_still_reach_the_escalation_card():
    """Bare「推荐」in a product name is not tendency markup; the card still opens."""
    seen: dict = {}

    async def _request(q, a, questions, reason, awaiting="user", **kwargs):
        seen["questions"] = questions
        return EscalationOutcome(status="resolved", answer="选方案A")

    ctx = _ctx(
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "该走哪个方案?",
            "assumption": "暂按方案A继续",
            "questions": [
                {
                    "id": "plan",
                    "prompt": "选一个方案",
                    "options": [
                        {"id": "a", "label": "推荐算法重写"},
                        {"id": "b", "label": "方案B"},
                    ],
                }
            ],
        },
        ctx,
    )

    assert result.success is True
    assert seen["questions"][0]["options"][0]["label"] == "推荐算法重写"


@pytest.mark.asyncio
async def test_old_blocking_and_kind_args_are_ignored():
    """不认旧参数：blocking/kind 不影响分流；缺 reason 按 wait。"""
    seen: list[str] = []

    async def _request(q, a, questions, reason, awaiting="user", **kwargs):
        seen.append(reason)
        return EscalationOutcome(status="resolved", answer="用 Postgres")

    result = await EscalateTool().execute(
        {
            "question": "选库?",
            "assumption": "暂用 PG",
            "blocking": False,
            "kind": "scope",
        },
        _ctx(
            conversation_id="c1",
            escalation=EscalationChannel(armed=True, request=_request),
        ),
    )
    assert result.success is True
    assert seen == ["wait"]
    assert "用户就你的升级问题答复" in result.output
