"""End-to-end: a durable resume re-wires consult to the SAME scope the turn
paused in, so the resumed CEO loop actually reaches the PROJECT topic (project 主题 first).

Where the unit tests pin the pieces in isolation —
- ``consult`` resolves project-then-global (``test_consult``),
- the durable frame carries ``folder_id`` (``test_durable`` / ``test_sidecar_paused``),
- ``_assemble_ceo_toolset`` maps ``folder_id`` → ``consult.folder_id`` —
this drives the REAL :func:`resume_chat_pipeline` (the same entry the cloud
``POST .../resume`` route and the Sidecar both call) end to end, folding the whole chain
``frame → assemble → CEO loop → consult → project store`` so a regression ANYWHERE
along it surfaces here, not just in an isolated seam.

The consult wiring is kind-AGNOSTIC: ``resume_chat_pipeline`` assembles the CEO
toolset ONCE (with the frame's ``folder_id``) BEFORE the kind-specific
settle, so the ask_user frame used here exercises the exact same wiring a plan_review frame
would — ask_user is chosen because it has no plan tail to rebuild from the journal.
"""

from __future__ import annotations

from pathlib import Path

from agentcore.config import settings
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.memory.store import FileMemoryStore, topic_path
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType, FinishReason
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles

USER_ID = "u1"
FOLDER_ID = "F1"
TOPIC = "部署流程"
PROJECT_BODY = "## 本项目部署\n- 用 pnpm deploy:backend\n- 生产机构建镜像\n"
GLOBAL_BODY = "## 全局部署\n- 通用 CI 流程\n"


def _tool_chunk(name: str, args: str, *, call_id: str) -> LLMChunk:
    return LLMChunk(
        delta_tool_calls=[
            ToolCallDelta(index=0, id=call_id, function_name=name, arguments_delta=args)
        ]
    )


def _content_chunk(text: str) -> LLMChunk:
    return LLMChunk(delta_content=text)


class _ScriptedProvider:
    """Fake LLM: one scripted round of chunks per ``stream`` call, recording each request.

    The recorded requests are how the test observes what ``consult`` fed back into
    the loop — the topic note's body rides the NEXT round as a ``tool`` message.
    """

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the engine loop
        self.requests.append(request)
        # Past the script, answer tool-free so the loop always finalizes (never hangs).
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else [_content_chunk("收尾")]
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:  # resume_chat_pipeline awaits llm.close() in finally
        return None


def _patch_seams(monkeypatch, provider: _ScriptedProvider, store: FileMemoryStore) -> None:
    """Swap ONLY the LLM provider and the memory store; keep the REAL toolset assembly.

    The point of an e2e is that ``_assemble_ceo_toolset`` (which wires consult to the
    frame's project scope) stays REAL — so patch each seam WHERE IT IS LOOKED UP:
    ``resume_chat_pipeline`` reads ``build_turn_router`` off the package facade
    (``pipeline_pkg.X``), and the real assembly in ``resolve.prepare`` calls
    ``default_memory_store`` as a module local.
    """

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)
    monkeypatch.setattr(
        "agentcore.runtime.resolve.prepare.default_memory_store", lambda: store
    )
    # No live client on a resume test → keep the loop free of the approval gate.
    monkeypatch.setattr(settings, "approval_gate_enabled", False)


def _ask_frame() -> AskUserSuspension:
    """An ask_user pause in project ``F1`` whose in-memory transcript ends at the suspended
    ``ask_user`` call (empty journal ⇒ ``resumed_captain_window`` folds back this transcript —
    the supported same-process resume carrier)."""
    susp = AskUserSuspension(
        message_id="m1",
        conversation_id="c1",
        user_id=USER_ID,
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="SYS",
        user_message="帮我按本项目流程部署",
        folder_id=FOLDER_ID,
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="帮我按本项目流程部署"),
            LLMMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_ask",
                        function=ToolCallFunction(name="ask_user", arguments="{}"),
                    )
                ],
            ),
        ],
        question="确认要继续吗？",
    )
    # Seed the surface checkpoint via journal_entries (唯一权威载体; the display seed derives —
    # P0-B Phase 3) so settle's journaled checkpoint_resolved has its pair. No turn_started
    # anchor ⇒ resumed_captain_window still folds back the in-memory transcript.
    susp.journal_entries = [
        {"kind": EventType.CHECKPOINT_REQUIRED.value, "payload": {}, "ts": "t"}
    ]
    return susp


async def _seed_topics(store: FileMemoryStore) -> None:
    # SAME topic name in both scopes, different bodies → a project-first hit must return
    # the PROJECT body, proving scope precedence survives the resume boundary.
    await store.save(USER_ID, topic_path(TOPIC), GLOBAL_BODY)
    await store.save(USER_ID, topic_path(TOPIC), PROJECT_BODY, scope=FOLDER_ID)


def _backend() -> ServerWorkspace:
    # consult never touches the backend; a plain "." root keeps it inert + hermetic.
    return ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())


def _tool_messages(request) -> list[str]:
    return [m.content or "" for m in request.messages if m.role == "tool"]


async def test_resume_consult_hits_project_topic(monkeypatch, tmp_path):
    # Resume a project turn (memory ON): the CEO loop calls consult(部署流程) and the
    # REAL re-wired tool resolves it in PROJECT scope first → the project note's body is what
    # rides back into the loop, NOT the same-named global note.
    store = FileMemoryStore(tmp_path / "memory")
    await _seed_topics(store)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("consult", f'{{"name": "{TOPIC}"}}', call_id="cm1")],
            [_content_chunk("已读取本项目部署流程，开始执行。")],
        ]
    )
    _patch_seams(monkeypatch, provider, store)

    result = await pipeline.resume_chat_pipeline(
        suspension=_ask_frame(),
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        sink=EventSink(),
        backend=_backend(),
        profile_set=make_turn_profiles(model="chat-model"),
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert "本项目部署流程" in result["content"]
    # The consult result rides the 2nd round as a tool message: it MUST be the
    # project body (more specific), and the global body of the same name must NOT leak.
    fed_back = _tool_messages(provider.requests[1])
    assert PROJECT_BODY in fed_back
    assert all(GLOBAL_BODY not in msg for msg in fed_back)


async def test_resume_ignores_legacy_memory_off_frame(monkeypatch, tmp_path):
    # Leftover ``memory_enabled=False`` on an old frame must not unload consult.
    store = FileMemoryStore(tmp_path / "memory")
    await _seed_topics(store)
    provider = _ScriptedProvider(
        [
            [_tool_chunk("consult", f'{{"name": "{TOPIC}"}}', call_id="cm1")],
            [_content_chunk("已读取本项目部署流程，开始执行。")],
        ]
    )
    _patch_seams(monkeypatch, provider, store)
    from agentcore.runtime.suspension import suspension_from_json

    raw = _ask_frame().to_json()
    raw["memory_enabled"] = False
    suspension = suspension_from_json(raw)
    suspension.transcript = _ask_frame().transcript
    suspension.journal_entries = _ask_frame().journal_entries

    result = await pipeline.resume_chat_pipeline(
        suspension=suspension,
        decision=CheckpointDecision.CONTINUE,
        note="继续",
        sink=EventSink(),
        backend=_backend(),
        profile_set=make_turn_profiles(model="chat-model"),
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    fed_back = _tool_messages(provider.requests[1])
    assert PROJECT_BODY in fed_back
