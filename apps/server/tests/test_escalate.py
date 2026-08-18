"""EscalateTool logging — ``worker.escalate`` records「为什么升级」(question + assumption).

决策可观测回归：``worker.escalate`` used to carry only ``run_id`` / ``blocking`` / ``kind`` /
``has_assumption`` — i.e. that AN escalation happened and its type, but never its substance.
Now it also logs ``question`` (the待决问题原文, preview-capped) and ``assumption`` (the超时
回落), so an offline analysis of the product-AI logs can read WHY a worker escalated and where
it was blocked, straight from the line — no DB round-trip. These drive the non-blocking path
(no live escalation channel), which still emits the log before returning its CONTINUE ack.
"""

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


def _ctx() -> ToolContext:
    # No escalation channel / on_escalate callback → the non-blocking escalate path, which
    # still emits worker.escalate before returning the "proceed on your assumption" ack.
    return ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def test_worker_escalate_logs_question_and_assumption(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    result = await EscalateTool().execute(
        {"question": "该走方案A还是方案B?", "assumption": "暂按方案A继续", "kind": "scope"},
        _ctx(),
    )

    assert result.success is True  # non-blocking escalate never stops the worker
    esc = spy.get("worker.escalate")
    assert esc["run_id"] == "w1"
    assert esc["kind"] == "scope"
    assert esc["blocking"] is False
    assert esc["has_assumption"] is True
    # the WHY + the fallback — the substance the enrichment adds
    assert esc["question"] == "该走方案A还是方案B?"
    assert esc["assumption"] == "暂按方案A继续"
    assert esc["browser_login"] is False


async def test_worker_escalate_question_preview_is_capped(monkeypatch):
    # A long question is clipped to a bounded preview (铁律: never the full 正文); no
    # assumption given → the assumption preview is empty (blocking defaults false, so an
    # assumption is not required).
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    await EscalateTool().execute({"question": "为" * 500}, _ctx())

    esc = spy.get("worker.escalate")
    assert esc["question"].endswith("…")
    assert len(esc["question"]) == 201  # 200-char cap + the one ellipsis char
    assert esc["has_assumption"] is False
    assert esc["assumption"] == ""
    assert esc["browser_login"] is False


async def test_browser_login_promotes_to_blocking_and_requires_assumption(monkeypatch):
    """browser_login=true forces blocking; missing assumption is rejected."""
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    result = await EscalateTool().execute(
        {"question": "请接管登录", "browser_login": True},
        _ctx(),
    )
    assert result.success is False
    assert "assumption" in (result.error or "")
    assert not any(name == "worker.escalate" for name, _ in spy.events)


async def test_browser_login_with_assumption_logs_promoted_blocking(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)

    result = await EscalateTool().execute(
        {
            "question": "请接管登录",
            "assumption": "用户登录后继续",
            "browser_login": True,
            # blocking omitted — should promote
        },
        _ctx(),
    )
    assert result.success is True  # unarmed channel → non-blocking fallthrough after promote
    esc = spy.get("worker.escalate")
    assert esc["blocking"] is True
    assert esc["browser_login"] is True


def test_escalation_required_emits_browser_login_only_when_true():
    with_flag = escalation_required(
        "r1",
        "a1",
        escalation_id="e1",
        question="请登录",
        assumption="登录后继续",
        browser_login=True,
    )
    assert with_flag.payload.get("browser_login") is True

    without = escalation_required(
        "r1",
        "a1",
        escalation_id="e1",
        question="普通问题",
        assumption="暂按 A",
        browser_login=False,
    )
    assert "browser_login" not in without.payload

    omitted = escalation_required(
        "r1",
        "a1",
        escalation_id="e1",
        question="普通问题",
        assumption="暂按 A",
    )
    assert "browser_login" not in omitted.payload


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


def test_escalate_schema_teaches_blocking_choice():
    """Worker 按题自选 blocking：身份段用人话，按钮上仍须写清 JSON 字段默认 false。"""
    schema = EscalateTool().schema
    desc = schema.description
    assert "默认 false" in desc
    assert "猜错作废" in desc
    assert "小事勿升级" in desc
    assert "报一声继续" in desc
    assert "只有上级能定" in desc
    blocking = schema.parameters["properties"]["blocking"]["description"]
    assert "默认 false" in blocking
    assert "报一声继续" in blocking or "原地等" in blocking
    # default philosophy unchanged: missing blocking stays non-blocking
    assert schema.parameters["properties"]["blocking"].get("default") in (None, False)


def test_escalate_schema_stays_off_engine_internals():
    """协调模式 / 超时 / 未武装 是引擎行为，不写进按钮。"""
    schema = EscalateTool().schema
    blob = schema.description + json.dumps(schema.parameters, ensure_ascii=False)
    for phrase in ("协调模式", "经典路径", "near-verbatim", "未武装", "并发满"):
        assert phrase not in blob, phrase


@pytest.mark.asyncio
async def test_blocking_channel_forwards_browser_login():
    seen: dict = {}

    async def _request(
        q,
        a,
        questions,
        kind,
        awaiting="user",
        *,
        browser_login=False,
        ownership_paths=None,
        lock_owner_run_id="",
    ):
        seen["browser_login"] = browser_login
        seen["awaiting"] = awaiting
        return EscalationOutcome(status="resolved", answer="已登录")

    ctx = ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "请接管登录",
            "assumption": "用户登录后继续",
            "blocking": True,
            "browser_login": True,
        },
        ctx,
    )
    assert result.success is True
    assert seen["browser_login"] is True
    assert seen["awaiting"] == "user"
    assert "用户就你的升级问题答复" in result.output


@pytest.mark.asyncio
async def test_browser_login_skips_ceo_arbitration_when_coordination_active(monkeypatch):
    """Password login must stay user-facing even with a living CEO (never CEO-await)."""
    seen: dict = {}

    async def _request(
        q,
        a,
        questions,
        kind,
        awaiting="user",
        *,
        browser_login=False,
        ownership_paths=None,
        lock_owner_run_id="",
    ):
        seen["awaiting"] = awaiting
        seen["browser_login"] = browser_login
        return EscalationOutcome(status="resolved", answer="已登录")

    class _FakeCoord:
        active = True

    monkeypatch.setattr(
        "agentcore.runtime.coordination.session.active_coordination",
        lambda _eid: _FakeCoord(),
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "请接管登录",
            "assumption": "用户登录后继续",
            "browser_login": True,
        },
        ctx,
    )
    assert result.success is True
    assert seen["browser_login"] is True
    assert seen["awaiting"] == "user"


@pytest.mark.asyncio
async def test_bracketed_recommendation_label_is_rejected_not_crashed(monkeypatch):
    """A bad label must cost the model one retry, not the user their escalation.

    ``normalize_questions`` is shared with ask_user, which catches this rejection;
    escalate used to catch only ``ListArgError``, so the same input escaped as a
    crash — server traceback into the event stream, card never opened, human never
    asked. Online 0.6.0 hit exactly that.
    """
    spy = LogSpy()
    monkeypatch.setattr(escalate_mod, "logger", spy)
    called = False

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        nonlocal called
        called = True
        return EscalationOutcome(status="resolved", answer="never reached")

    ctx = ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "该走哪个方案?",
            "assumption": "暂按方案A继续",
            "blocking": True,
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

    assert result.success is False
    assert "recommended" in result.error
    assert called is False  # rejected before delivery — no half-open card
    assert spy.get("worker.escalate_option_label_rejected")["run_id"] == "w1"


@pytest.mark.asyncio
async def test_clean_labels_still_reach_the_escalation_card():
    """Guard the rejection above against over-reach: bare「推荐」in a name is fine."""
    seen: dict = {}

    async def _request(q, a, questions, kind, awaiting="user", **kwargs):
        seen["questions"] = questions
        return EscalationOutcome(status="resolved", answer="选方案A")

    ctx = ToolContext.create(
        execution_id="e",
        run_id="w1",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c1",
        escalation=EscalationChannel(armed=True, request=_request),
    )
    result = await EscalateTool().execute(
        {
            "question": "该走哪个方案?",
            "assumption": "暂按方案A继续",
            "blocking": True,
            "questions": [
                {
                    "id": "plan",
                    "prompt": "选一个方案",
                    "options": [
                        {"id": "a", "label": "推荐算法重写", "recommended": True},
                        {"id": "b", "label": "方案B"},
                    ],
                }
            ],
        },
        ctx,
    )

    assert result.success is True
    assert seen["questions"][0]["options"][0]["label"] == "推荐算法重写"
