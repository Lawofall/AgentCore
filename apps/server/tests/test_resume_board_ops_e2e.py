"""End-to-end: a durable resume re-wires ``board_ops`` + its :class:`BoardChannel` when the
turn is a 白板会话, so the resumed CEO loop keeps drawing on the user's canvas AFTER a
checkpoint — and an ordinary (non-board) resume never gets the tool.

Where the unit tests pin the pieces in isolation —
- ``BoardChannel`` suspends a batch + emits ``board_op_required`` and ``BoardOpsTool`` maps
  the desktop's reply (``test_board_channel``),
- the fresh-turn pipeline wires both when ``board_id`` is set (``run.py`` path) —
this drives the REAL :func:`resume_chat_pipeline` (the same entry the cloud ``POST
.../resume`` route and the Sidecar call) end to end, folding the whole chain
``board_id → assemble → register board_ops → CEO loop → BoardChannel → desktop reply`` so a
regression ANYWHERE along the resume wiring surfaces here.

A fake "desktop" runs concurrently: it drains the sink, and when the resumed CEO suspends on
a ``board_op_required`` it settles the SHARED interaction registry (the same singleton the
pipeline binds the channel to) with a canned apply result — exactly as the real desktop's
ops-resolve endpoint would. The ask_user frame is used because it has no plan tail to rebuild
from the journal; the board wiring is kind-agnostic (assembled once before the settle), so a
plan_review frame would exercise the identical path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentcore.config import settings
from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMMessage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
)
from agentcore.memory.store import FileMemoryStore
from agentcore.runtime import pipeline
from agentcore.runtime.checkpoints import CheckpointDecision
from agentcore.runtime.events import EventSink, EventType, FinishReason, SSEEvent
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.suspension import AskUserSuspension
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_turn_profiles

USER_ID = "u1"
CONV_ID = "c1"
BOARD_ID = "B1"
# What the fake desktop reports it applied — rides back into the loop as the tool result.
APPLY_VALUE = {"applied": 1, "created": ["el-1"], "version": 2}


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

    The recorded requests are how the test observes what ``board_ops`` fed back into the
    loop — the apply summary rides the NEXT round as a ``tool`` message.
    """

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.requests: list = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the engine loop
        self.requests.append(request)
        chunks = (
            self._rounds[self.calls] if self.calls < len(self._rounds) else [_content_chunk("收尾")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk

    async def close(self) -> None:  # resume_chat_pipeline awaits llm.close() in finally
        return None


def _patch_seams(monkeypatch, provider: _ScriptedProvider, store: FileMemoryStore) -> None:
    """Swap ONLY the LLM provider + memory store; keep the REAL toolset assembly (which is
    what wires ``board_ops``). Patch each seam WHERE IT IS LOOKED UP (see the consult e2e)."""

    async def _fake_build_turn_router(*_a, **_k):
        return provider

    monkeypatch.setattr(pipeline, "build_turn_router", _fake_build_turn_router)
    monkeypatch.setattr("agentcore.runtime.resolve.prepare.default_memory_store", lambda: store)
    # No live client on a resume test → keep the loop free of the approval gate.
    monkeypatch.setattr(settings, "approval_gate_enabled", False)


def _profiles():
    return make_turn_profiles(model="chat-model")


def _backend() -> ServerWorkspace:
    # board_ops never touches the FS backend; a plain "." root keeps it inert + hermetic.
    return ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox())


def _ask_frame() -> AskUserSuspension:
    """An ask_user pause whose in-memory transcript ends at the suspended ``ask_user`` call
    (empty journal ⇒ ``resumed_captain_window`` folds back this transcript)."""
    susp = AskUserSuspension(
        message_id="m1",
        conversation_id=CONV_ID,
        user_id=USER_ID,
        captain_run_id="cap1",
        checkpoint_id="ck1",
        tool_call_id="call_ask",
        base_system_prompt="SYS",
        user_message="在白板上把登录流程画出来",
        folder_id=None,
        transcript=[
            LLMMessage(role="system", content="SYS"),
            LLMMessage(role="user", content="在白板上把登录流程画出来"),
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


async def _drive(
    monkeypatch,
    tmp_path,
    *,
    board_id: str | None,
    provider: _ScriptedProvider,
    answer_desktop: bool,
) -> tuple[dict, list[SSEEvent]]:
    """Run the resume + a concurrent fake desktop; return (result, captured events).

    The desktop drains the sink (the sole consumer) so the suspended board op can be settled
    on the shared registry mid-turn; without a concurrent consumer the resumed loop would
    block forever awaiting the desktop reply.
    """
    _patch_seams(monkeypatch, provider, FileMemoryStore(tmp_path / "memory"))
    sink = EventSink()
    registry = default_interaction_registry()
    captured: list[SSEEvent] = []

    # Bridge fulfill delivery → turn sink so the existing fake desktop can settle.
    from agentcore.fulfill.dispatch import DeliverResult

    def _fake_deliver(
        user_id,
        conversation_id,
        channel,
        root_id,
        event,
        *,
        origin_device_id=None,
        hub=None,
    ):
        sink.emit(event)
        return DeliverResult.DELIVERED

    monkeypatch.setattr(
        "agentcore.fulfill.dispatch.deliver_client_tool", _fake_deliver
    )

    async def desktop() -> None:
        while True:
            ev = await sink.get()
            if ev is None:
                return
            captured.append(ev)
            if answer_desktop and ev.type == EventType.BOARD_OP_REQUIRED:
                registry.resolve(
                    ev.payload["request_id"],
                    {"ok": True, "value": APPLY_VALUE},
                    conversation_id=CONV_ID,
                )

    async def run() -> dict:
        try:
            return await pipeline.resume_chat_pipeline(
                suspension=_ask_frame(),
                decision=CheckpointDecision.CONTINUE,
                note="继续",
                sink=sink,
                backend=_backend(),
                board_id=board_id,
                profile_set=_profiles(),
            )
        finally:
            # The pipeline no longer closes the sink (its owner does); this test owns it,
            # so close it once the turn ends → the concurrent desktop drainer gets the None
            # sentinel and the gather completes instead of blocking forever on sink.get().
            sink.close()

    result, _ = await asyncio.gather(run(), desktop())
    return result, captured


def _tool_messages(request) -> list[str]:
    return [m.content or "" for m in request.messages if m.role == "tool"]


async def test_resume_board_turn_rewires_board_ops(monkeypatch, tmp_path):
    # Resume a 白板会话: the CEO loop calls board_ops, the REAL re-wired channel suspends +
    # emits board_op_required, the fake desktop settles it, and the apply summary rides back
    # into the loop — proving board_ops is reachable on resume, all the way to the canvas.
    provider = _ScriptedProvider(
        [
            [
                _tool_chunk(
                    "board_ops",
                    '{"ops": [{"op": "add_node", "ref": "a", "text": "登录"}], "summary": "加节点"}',
                    call_id="bo1",
                )
            ],
            [_content_chunk("已在白板加好登录节点。")],
        ]
    )
    result, captured = await _drive(
        monkeypatch, tmp_path, board_id=BOARD_ID, provider=provider, answer_desktop=True
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    # The channel was wired + dispatched: a board_op_required carrying THIS board's id fired.
    board_events = [e for e in captured if e.type == EventType.BOARD_OP_REQUIRED]
    assert len(board_events) == 1
    assert board_events[0].payload["board_id"] == BOARD_ID
    assert board_events[0].payload["ops"][0]["op"] == "add_node"
    # The desktop's apply result rode the 2nd round back as a tool message (loop closed).
    fed_back = _tool_messages(provider.requests[1])
    assert any("已在白板应用" in msg for msg in fed_back)


async def test_resume_without_board_has_no_board_ops(monkeypatch, tmp_path):
    # Resume an ordinary chat (no board binding): even though the model TRIES board_ops, the
    # tool is unwired, so NO board_op_required ever fires (nothing reaches a canvas) — yet the
    # turn still finishes cleanly (a stale/typo'd tool call must never break a resume).
    provider = _ScriptedProvider(
        [
            [
                _tool_chunk(
                    "board_ops",
                    '{"ops": [{"op": "add_node", "text": "x"}]}',
                    call_id="bo1",
                )
            ],
            [_content_chunk("这不是白板会话，无法作画。")],
        ]
    )
    result, captured = await _drive(
        monkeypatch, tmp_path, board_id=None, provider=provider, answer_desktop=False
    )

    assert result["finish_reason"] == FinishReason.END_TURN
    assert result["content"]
    # board_ops was never wired → the channel never engaged → no canvas event at all.
    assert not any(e.type == EventType.BOARD_OP_REQUIRED for e in captured)
    # The loop continued past the rebuffed attempt and finalized (≥2 rounds), proving the
    # unknown/unavailable tool degraded gracefully instead of breaking the resumed turn.
    assert len(provider.requests) >= 2
