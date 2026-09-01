"""Harness: real ``run_chat_pipeline`` + scripted LLM; memory / DB seams stubbed."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from agentcore.config import settings
from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink
from agentcore.runtime.journal import last_turn_end_finish
from agentcore.runtime.journal.writer import TurnJournalWriter
from agentcore.runtime.suspension import TurnSuspension, suspension_from_json
from agentcore.tools.builtin.delegate import DelegateTool
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _upstream_body
from tests.llm_helpers import make_turn_profiles

CEO_FINAL = "CEO_FINAL"
WORKER_MARK = "WORKER_OUT"
ASK_MESSAGE = "需要你拍板方向后再继续"
ESC_QUESTION = "数据库选 Postgres 还是 MySQL？"
ESC_ASSUMPTION = "暂按 Postgres 推进"

# form=prose: bare-chat write-desk gate lets this through without target_folder_id.
_SINGLE_TASK = [
    {
        "role": "研究员",
        "task": "调研并给出结论",
        "deliverable": {"form": "prose"},
    }
]


class EmptyMemoryStore:
    """In-memory empty store so prepare never hits DocumentMemoryStore / Postgres."""

    async def list(self, _user_id: str, scope: str | None = None) -> list:
        return []

    async def load(self, _user_id: str, _path: str, scope: str | None = None) -> str:
        return ""

    async def save(self, _user_id: str, _path: str, _markdown: str, scope: str | None = None) -> None:
        return None

    async def delete(self, _user_id: str, _path: str, scope: str | None = None) -> None:
        return None

    async def project_scopes(self, _user_id: str) -> list[str]:
        return []


class RoleScriptedProvider:
    """CEO (``scenario=chat``) vs worker (``scenario=agent``); one scripted act per call.

    Modes:
    - ``delegate`` — CEO delegates one worker (coordinate=false); worker writes; CEO closes.
    - ``ask`` — CEO calls ``ask_user`` (durable checkpoint). After resume the
      window carries the tool result, so the next CEO call closes.
    - ``escalate`` — worker non-blocking ``escalate`` then writes; CEO closes.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):  # noqa: ANN001 — duck-typed LLMProvider
        self.requests.append(request)
        self.calls += 1
        is_ceo = (request.scenario or "chat") == "chat"
        has_tool = any(getattr(m, "role", None) == "tool" for m in request.messages)

        if is_ceo:
            if not has_tool:
                if self.mode == "ask":
                    yield _tool_chunk(
                        "ask_user",
                        json.dumps({"message": ASK_MESSAGE}, ensure_ascii=False),
                    )
                    return
                yield _tool_chunk(
                    "delegate",
                    json.dumps(
                        {"tasks": _SINGLE_TASK, "coordinate": False},
                        ensure_ascii=False,
                    ),
                )
                return
            yield LLMChunk(delta_content=CEO_FINAL)
            return

        if self.mode == "escalate" and not has_tool:
            yield _tool_chunk(
                "escalate",
                json.dumps(
                    {
                        "question": ESC_QUESTION,
                        "assumption": ESC_ASSUMPTION,
                        "kind": "normal",
                        "blocking": False,
                    },
                    ensure_ascii=False,
                ),
            )
            return
        yield LLMChunk(delta_content=_upstream_body(WORKER_MARK))

    async def close(self) -> None:
        return None


def _tool_chunk(name: str, args: str, *, call_id: str = "tc1") -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


@asynccontextmanager
async def _no_db():
    raise RuntimeError("orchestration_scenarios: no database")
    yield  # pragma: no cover


async def _fast_journal_drain(self: TurnJournalWriter) -> None:
    """Resolve queued appends in-memory so emit barriers never wait on Postgres."""
    while self._buffer:
        _seq_hint, _entry, future, _critical = self._buffer.popleft()
        if not future.done():
            future.set_result(1)
    overflow = self._overflow
    if overflow is not None and overflow is not self:
        await overflow.flush()


def patch_orchestration_seams(monkeypatch, provider: RoleScriptedProvider) -> None:
    """LLM + memory + fail-fast DB; keep real ``_assemble_ceo_toolset`` (true DelegateTool)."""

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)

    store = EmptyMemoryStore()
    monkeypatch.setattr("agentcore.runtime.pipeline.run.default_memory_store", lambda: store)
    monkeypatch.setattr(
        "agentcore.runtime.resolve.prepare.default_memory_store", lambda: store
    )

    real_assemble = pipeline._assemble_ceo_toolset

    def _assemble_keeping_delegate(**kwargs):
        delegate, debate, chat_tools = real_assemble(**kwargs)
        assert isinstance(delegate, DelegateTool)
        assert chat_tools.get("delegate") is delegate
        return delegate, debate, chat_tools

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.run._assemble_ceo_toolset",
        _assemble_keeping_delegate,
    )

    async def _no_vision(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.resolve_vision_reader_for_conversation",
        _no_vision,
    )
    monkeypatch.setattr(TurnJournalWriter, "_drain", _fast_journal_drain)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", _no_db)
    monkeypatch.setattr(
        "agentcore.runtime.pipeline.prepare.async_session_factory",
        _no_db,
    )

    async def _noop_audit(self, _draft) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(
        "agentcore.runtime.audit.recorder.AuditRecorder._append", _noop_audit
    )


async def run_orchestration_turn(
    monkeypatch,
    tmp_path: Path,
    *,
    mode: str,
    approvals_enabled: bool = False,
    suspension_saver=None,
    suspension_deleter=None,
):
    """Drive ``run_chat_pipeline`` once; caller owns the sink (closes after return)."""
    provider = RoleScriptedProvider(mode)
    patch_orchestration_seams(monkeypatch, provider)
    if approvals_enabled:
        # Checkpoint / ask_user stay armed; approval cards would hang with no client.
        monkeypatch.setattr(settings, "approval_gate_enabled", False)

    sink = EventSink()
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    result = await pipeline.run_chat_pipeline(
        conversation_id=f"orch-{mode}",
        user_message="请组队完成调研",
        history=[],
        sink=sink,
        user_id="user-orch",
        backend=backend,
        approvals_enabled=approvals_enabled,
        profile_set=make_turn_profiles(model="chat-model"),
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
    )
    sink.close()
    events = [e async for e in sink]
    return result, events, provider


def cold_claim_frame(frame: TurnSuspension) -> TurnSuspension:
    """JSON round-trip as ``paused_turns`` + re-attach ``journal_entries`` (claim)."""
    restored = suspension_from_json(frame.to_json())
    assert not restored.transcript, "cold-claimed frame must not carry in-memory transcript"
    restored.journal_entries = list(frame.journal_entries or [])
    return restored


async def run_orchestration_resume(
    monkeypatch,
    tmp_path: Path,
    *,
    suspension: TurnSuspension,
    decision: CheckpointDecision = CheckpointDecision.CONTINUE,
    note: str = "按这个方向继续",
    selected: list[str] | None = None,
    mode: str = "ask",
    suspension_saver=None,
    suspension_deleter=None,
):
    """Drive ``resume_chat_pipeline`` with the same scripted LLM / no-DB seams."""
    provider = RoleScriptedProvider(mode)
    patch_orchestration_seams(monkeypatch, provider)
    monkeypatch.setattr(settings, "approval_gate_enabled", False)

    sink = EventSink()
    backend = ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox())
    result = await pipeline.resume_chat_pipeline(
        suspension=suspension,
        decision=decision,
        note=note,
        selected=selected or [],
        sink=sink,
        backend=backend,
        profile_set=make_turn_profiles(model="chat-model"),
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
    )
    sink.close()
    events = [e async for e in sink]
    return result, events, provider


def event_types(events) -> list:
    return [e.type for e in events]


def journal_kinds(result: dict) -> list[str]:
    return [str(e.get("kind") or "") for e in (result.get("journal_entries") or [])]


def turn_end_finish(result: dict) -> str | None:
    return last_turn_end_finish(result.get("journal_entries"))
