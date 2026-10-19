"""The post-turn tail runs INSIDE the turn's ``log_context`` (trace correlation).

Regression for the 全链路 join-key leak: ``persist_turn_result`` (which emits
``cost.recorded`` / ``obs.turn_spans`` and the turn-metrics / snapshot / title warnings)
used to fire AFTER ``run_and_persist`` had already closed the ``log_context`` scope. So
those tail lines carried no ``trace_id`` / ``attempt_id`` — the single key ``grep
trace_id=<id>`` relies on (conversation-logs.mdc), silently dropping cost + spans from a
by-trace analysis. This locks the tail into the scope so it inherits the correlation ids
from structlog contextvars, exactly like the spine (``chat.turn_start`` / ``turn_complete``).
"""

import structlog

from agentcore.conversation import turn_runner
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.turn.snapshot import ConversationSnapshot


class _FakeBackend:
    """Minimal backend: ``run_and_persist`` only reads ``.location`` before the (mocked) tail."""

    location = "server"
    dirty = False


async def test_persist_tail_runs_inside_trace_scope(monkeypatch):
    captured: dict = {}

    async def _fake_pipeline(**_kwargs):
        return {
            "finish_reason": FinishReason.END_TURN,
            "content": "hi",
            "cost_runs": [],
            "message_id": "m1",
        }

    async def _spy_persist(**kwargs):
        # Snapshot the correlation context AT the moment the tail runs.
        captured["ctx"] = dict(structlog.contextvars.get_contextvars())
        captured["kwargs"] = kwargs

    async def _fake_placeholder(**_kwargs):
        return None

    monkeypatch.setattr(turn_runner, "run_chat_pipeline", _fake_pipeline)
    monkeypatch.setattr(turn_runner, "persist_turn_result", _spy_persist)
    # run_and_persist creates the assistant row before the pipeline; stub so this
    # unit test never hits UUID/DB validation (intent is only the persist-tail scope).
    monkeypatch.setattr(turn_runner, "create_assistant_placeholder", _fake_placeholder)

    await turn_runner.run_and_persist(
        conversation_id="c-scope",
        user_message="go",
        user_id="u1",
        conversation=ConversationSnapshot.default(),
        sink=EventSink(),
        history=[],
        attachments=None,
        backend=_FakeBackend(),  # type: ignore[arg-type]
        llm_credentials=None,
    )

    ctx = captured["ctx"]
    # The tail sees the SAME correlation ids the spine carried — not a cleared context.
    assert ctx.get("trace_id")
    assert ctx.get("attempt_id")
    assert ctx.get("conversation_id") == "c-scope"
    # …and the contextvar attempt_id matches what is threaded explicitly for turn_metrics
    # (persist param still named turn_id = DB column); log lines join via attempt_id.
    assert captured["kwargs"]["trace_id"] == ctx["trace_id"]
    assert captured["kwargs"]["turn_id"] == ctx["attempt_id"]
