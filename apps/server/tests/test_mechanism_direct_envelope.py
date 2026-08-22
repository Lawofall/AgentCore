"""M3: mechanism-direct turn envelope (placeholder / lease / persist).

Workflow「跑一次」and standing bound-workflow share
``run_mechanism_direct_and_persist`` — same outer contract as chat
``run_and_persist``, inner pipeline stays ``run_workflow_pipeline``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from agentcore.conversation import turn_runner
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.workflows import runner as wf_runner


class _FakeBackend:
    location = "server"
    dirty = False


@pytest.mark.asyncio
async def test_mechanism_direct_envelope_placeholder_persist_and_trace(monkeypatch):
    """Envelope mints placeholder, passes message_id into pipeline, persists inside log_context."""
    captured: dict = {}

    async def fake_pipeline(**kwargs):
        captured["pipeline_kwargs"] = kwargs
        captured["pipeline_ctx"] = dict(structlog.contextvars.get_contextvars())
        return {
            "finish_reason": FinishReason.END_TURN,
            "content": "按图跑完",
            "rounds": 1,
        }

    async def spy_persist(**kwargs):
        captured["persist_ctx"] = dict(structlog.contextvars.get_contextvars())
        captured["persist_kwargs"] = kwargs

    async def fake_placeholder(**kwargs):
        captured["placeholder"] = kwargs

    # Local import inside the envelope — patch the module attribute.
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.workflow_run.run_workflow_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(turn_runner, "persist_turn_result", spy_persist)
    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", fake_placeholder)
    monkeypatch.setattr(turn_runner.settings, "turn_lease_enabled", False)
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        AsyncMock(),
    )

    result = await turn_runner.run_mechanism_direct_and_persist(
        conversation_id="c-wf",
        user_message="按工作流「质检」执行。",
        user_id="u1",
        folder_id="f1",
        sink=EventSink(),
        history=[],
        backend=_FakeBackend(),  # type: ignore[arg-type]
        llm_credentials=None,
        tasks=[{"id": "a", "role": "质检", "task": "查"}],
        workflow_id="wf-1",
        workflow_version=2,
    )

    assert captured["placeholder"]["conversation_id"] == "c-wf"
    assert captured["placeholder"]["message_id"]
    assert captured["placeholder"]["trace_id"]

    pk = captured["pipeline_kwargs"]
    assert pk["message_id"] == captured["placeholder"]["message_id"]
    assert pk["workflow_id"] == "wf-1"
    assert pk["workflow_version"] == 2
    assert len(pk["tasks"]) == 1

    # Pipeline + persist share the turn's correlation scope.
    assert captured["pipeline_ctx"].get("trace_id")
    assert captured["pipeline_ctx"].get("attempt_id")
    assert captured["pipeline_ctx"].get("conversation_id") == "c-wf"
    assert captured["pipeline_ctx"].get("workflow_id") == "wf-1"

    assert captured["persist_ctx"].get("trace_id") == captured["pipeline_ctx"]["trace_id"]
    assert captured["persist_kwargs"]["trace_id"] == captured["pipeline_ctx"]["trace_id"]
    assert captured["persist_kwargs"]["turn_id"] == captured["pipeline_ctx"]["attempt_id"]

    assert result is not None
    assert result["message_id"] == captured["placeholder"]["message_id"]
    assert result["content"] == "按图跑完"


@pytest.mark.asyncio
async def test_mechanism_direct_envelope_acquires_lease_when_enabled(monkeypatch):
    lease_calls: list[dict] = []

    async def fake_pipeline(**_kwargs):
        return {"finish_reason": FinishReason.END_TURN, "content": "ok"}

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.workflow_run.run_workflow_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", AsyncMock())
    monkeypatch.setattr(turn_runner, "persist_turn_result", AsyncMock())
    monkeypatch.setattr(
        "agentcore.workspace.turn_baseline.maybe_capture_turn_baseline",
        AsyncMock(),
    )
    monkeypatch.setattr(turn_runner.settings, "turn_lease_enabled", True)

    async def fake_acquire(**kwargs):
        lease_calls.append({"op": "acquire", **kwargs})
        return "owner-1"

    async def fake_release(message_id):
        lease_calls.append({"op": "release", "message_id": message_id})

    async def fake_heartbeat(*_a, **_k):
        return None

    monkeypatch.setattr(turn_runner, "acquire_turn_lease", fake_acquire)
    monkeypatch.setattr(turn_runner, "release_turn_lease", fake_release)
    monkeypatch.setattr(turn_runner, "lease_heartbeat_loop", fake_heartbeat)

    await turn_runner.run_mechanism_direct_and_persist(
        conversation_id="c-lease",
        user_message="go",
        user_id="u1",
        folder_id=None,
        sink=EventSink(),
        history=[],
        backend=_FakeBackend(),  # type: ignore[arg-type]
        llm_credentials=None,
        tasks=[],
        workflow_id="wf-x",
        workflow_version=1,
    )

    ops = [c["op"] for c in lease_calls]
    assert "acquire" in ops
    assert "release" in ops
    assert lease_calls[0]["meta"]["workflow_id"] == "wf-x"


@pytest.mark.asyncio
async def test_run_workflow_job_uses_mechanism_direct_envelope(monkeypatch):
    """「跑一次」background job must go through the shared envelope, not bare pipeline."""
    called: dict = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            called["user_msg"] = kwargs.get("content")
            return SimpleNamespace(id="um1")

    monkeypatch.setattr(wf_runner, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(wf_runner, "ConversationRepository", _Convs)
    monkeypatch.setattr(wf_runner, "MessageRepository", _Msgs)
    monkeypatch.setattr(wf_runner, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(wf_runner, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(wf_runner, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(wf_runner, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_envelope(**kwargs):
        called["envelope"] = kwargs
        return {"finish_reason": FinishReason.END_TURN, "content": "done", "message_id": "m1"}

    monkeypatch.setattr(
        "agentcore.conversation.turn_runner.run_mechanism_direct_and_persist",
        fake_envelope,
    )

    await wf_runner.run_workflow_job(
        conversation_id="conv-1",
        user_id="user-1",
        folder_id="folder-1",
        workflow_id="wf-1",
        workflow_version=3,
        workflow_name="质检",
        tasks=[{"id": "a", "role": "质检", "task": "查"}],
        note="补充",
        llm_credentials=None,
    )

    assert "envelope" in called
    env = called["envelope"]
    assert env["workflow_id"] == "wf-1"
    assert env["workflow_version"] == 3
    assert env["conversation_id"] == "conv-1"
    assert "质检" in env["user_message"]
    assert "补充" in env["user_message"]
    assert len(env["tasks"]) == 1


@pytest.mark.asyncio
async def test_standing_bound_workflow_wrapper_uses_same_envelope(monkeypatch):
    """Standing `_run_workflow_pipeline` expands definition then hits the shared envelope."""
    from agentcore.standing_tasks import runner as standing_runner

    called: dict = {}

    async def fake_envelope(**kwargs):
        called["envelope"] = kwargs
        return {
            "finish_reason": FinishReason.END_TURN,
            "content": "站立绑工作流",
            "message_id": "m-standing",
        }

    monkeypatch.setattr(
        "agentcore.conversation.turn_runner.run_mechanism_direct_and_persist",
        fake_envelope,
    )

    result = await standing_runner._run_workflow_pipeline(
        conversation_id="conv-s",
        user_message="按工作流「三步」执行。",
        user_id="u1",
        folder_id="f1",
        sink=EventSink(),
        history=[],
        backend=MagicMock(),
        llm_credentials=None,
        profile_set=None,
        permission_axes=None,
        workflow_id="wf-1",
        workflow_version=2,
        workflow_name="三步",
        definition={
            "nodes": [
                {"id": "s1", "kind": "agent_step", "role": "质检", "task": "查一查"},
            ],
            "edges": [],
        },
    )

    assert called["envelope"]["workflow_id"] == "wf-1"
    assert called["envelope"]["workflow_version"] == 2
    assert called["envelope"]["conversation_id"] == "conv-s"
    assert len(called["envelope"]["tasks"]) == 1
    assert result is not None
    assert result["message_id"] == "m-standing"
