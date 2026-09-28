"""协调中用户插话：注入 CEO 上下文后留在本回合。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentcore.runtime.coordination.inject import format_coordination_events
from agentcore.runtime.coordination.session import (
    CoordinationEvent,
    CoordinationEventKind,
    CoordinationSession,
    active_coordination_for_conversation,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.runtime.events import EventSink, user_interjection
from agentcore.runtime.turn.queue import turn_queue
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.attachments import (
    interjection_attachment_meta,
    persist_attachments,
)
from agentcore.workspace.server import ServerWorkspace


@pytest.fixture(autouse=True)
def _clean_coord():
    clear_active_coordination()
    turn_queue.clear("conv-inj")
    yield
    clear_active_coordination()
    turn_queue.clear("conv-inj")


def test_active_coordination_for_conversation_index():
    session = CoordinationSession(
        execution_id="exec-inj",
        total_workers=2,
        conversation_id="conv-inj",
    )
    set_active_coordination(session)
    assert active_coordination_for_conversation("conv-inj") is session
    clear_active_coordination("exec-inj")
    assert active_coordination_for_conversation("conv-inj") is None


def test_user_interjection_is_necessary_decision():
    session = CoordinationSession(execution_id="e", total_workers=2)
    ev = CoordinationEvent(
        kind=CoordinationEventKind.USER_INTERJECTION,
        payload={"interjection_id": "i1", "content": "加一句成本"},
    )
    assert session.is_necessary_decision([ev]) is True


def test_has_unread_user_interjection_peeks_without_consuming():
    session = CoordinationSession(execution_id="e-unread", total_workers=1)
    ev = CoordinationEvent(
        kind=CoordinationEventKind.USER_INTERJECTION,
        payload={"interjection_id": "i-unread", "content": "加一句"},
    )
    assert session.post(ev) is True
    assert session.has_unread_user_interjection() is True
    batch = session.drain_nowait()
    assert any(e.kind is CoordinationEventKind.USER_INTERJECTION for e in batch)
    assert session.has_unread_user_interjection() is False


def test_has_unread_user_interjection_ignores_other_kinds():
    session = CoordinationSession(execution_id="e-unread-other", total_workers=1)
    ev = CoordinationEvent(
        kind=CoordinationEventKind.WORKER_COMPLETED,
        payload={"run_id": "w1"},
    )
    assert session.post(ev) is True
    assert session.has_unread_user_interjection() is False


def test_user_interjection_sse_carries_attachments():
    meta = [
        {
            "name": "成本表.xlsx",
            "workspace_path": "attachments/成本表.xlsx",
            "binary": True,
        }
    ]
    ev = user_interjection(
        interjection_id="inj-a",
        execution_id="exec-a",
        content="对照附件",
        status="received",
        attachments=meta,
    )
    assert ev.payload["attachments"] == meta
    assert ev.payload["status"] == "received"


def test_user_interjection_sse_carries_agent_mentions():
    mentions = [{"agent_id": "agent_research", "role": "研究员"}]
    ev = user_interjection(
        interjection_id="inj-m",
        execution_id="exec-m",
        content="让研究员再核一遍",
        status="received",
        agent_mentions=mentions,
    )
    assert ev.payload["agent_mentions"] == mentions
    empty = user_interjection(
        interjection_id="inj-empty",
        execution_id="exec-m",
        content="无点名",
        status="received",
        agent_mentions=[],
    )
    assert "agent_mentions" not in empty.payload


def test_interjection_attachment_meta_drops_text():
    meta = interjection_attachment_meta(
        [
            {
                "name": "notes.md",
                "path": "/tmp/notes.md",
                "text": "secret body",
                "workspace_path": "attachments/notes.md",
                "binary": False,
            }
        ]
    )
    assert meta == [
        {
            "name": "notes.md",
            "workspace_path": "attachments/notes.md",
            "binary": False,
        }
    ]
    assert "text" not in meta[0]


def test_inject_brief_lists_attachment_paths():
    session = CoordinationSession(execution_id="e", total_workers=2)
    brief = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={
                    "interjection_id": "inj-1",
                    "content": "对照附件",
                    "attachments": [
                        {
                            "name": "成本表.xlsx",
                            "workspace_path": "attachments/成本表.xlsx",
                            "binary": True,
                        }
                    ],
                },
            )
        ],
    )
    assert "成本表.xlsx" in brief
    assert "attachments/成本表.xlsx" in brief
    assert "（二进制）" in brief
    assert "secret" not in brief


def test_inject_brief_lists_agent_mentions():
    session = CoordinationSession(execution_id="e", total_workers=2)
    session.stash_interjection(
        "inj-m",
        {
            "content": "让研究员再核一遍",
            "agent_mentions": [{"agent_id": "agent_research", "role": "研究员"}],
        },
    )
    brief = format_coordination_events(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={
                    "interjection_id": "inj-m",
                    "content": "让研究员再核一遍",
                },
            )
        ],
    )
    assert "让研究员再核一遍" in brief
    assert "用户点名关注以下 Agent（软提示，非强制派单/非硬路由）" in brief
    assert "- 研究员 (id=agent_research)" in brief
    assert "<队员点名>" in brief


@pytest.mark.asyncio
async def test_persist_then_repersist_keeps_text_and_skips_rewrite(tmp_path: Path):
    """Delivered persist → stash → drain re-pass must not rewrite or drop inline text."""
    root = tmp_path / "ws"
    root.mkdir()
    ws = ServerWorkspace(root=root, sandbox=SubprocessSandbox())

    first = await persist_attachments(
        ws,
        [{"name": "notes.md", "path": "/local/notes.md", "text": "hello body"}],
    )
    assert first[0]["workspace_path"] == "attachments/notes.md"
    assert first[0]["text"] == "hello body"
    assert (root / "attachments" / "notes.md").read_text(encoding="utf-8") == "hello body"

    # Simulate a later drain: mutate disk so a rewrite would be visible.
    (root / "attachments" / "notes.md").write_text("SHOULD_NOT_OVERWRITE", encoding="utf-8")
    second = await persist_attachments(ws, first)
    assert second[0]["workspace_path"] == "attachments/notes.md"
    assert second[0]["text"] == "hello body"
    assert (root / "attachments" / "notes.md").read_text(encoding="utf-8") == (
        "SHOULD_NOT_OVERWRITE"
    )


def test_close_leaves_interjection_in_this_turn():
    """收口不把未另作处理的插话升成下一回合。"""
    session = CoordinationSession(
        execution_id="exec-inj",
        total_workers=2,
        conversation_id="conv-inj",
    )
    set_active_coordination(session)
    sink = EventSink()
    session.event_sink = sink
    session.stash_interjection(
        "inj-auto",
        {
            "content": "未消化短讯",
            "user_id": "u1",
            "conversation_id": "conv-inj",
            "attachments": [],
        },
    )
    session.close()
    assert turn_queue.depth("conv-inj") == 0
    assert session.get_interjection("inj-auto") is not None
    assert not any(e.type.value == "user_interjection" for e in sink._history)



@pytest.mark.asyncio
async def test_note_interjections_injected_emits_injected_status():
    from agentcore.runtime.coordination.interjections import note_interjections_injected

    session = CoordinationSession(
        execution_id="exec-inj",
        total_workers=2,
        conversation_id="conv-inj",
    )
    sink = EventSink()
    session.event_sink = sink
    set_active_coordination(session)
    session.stash_interjection(
        "inj-inj",
        {
            "content": "请点明成本",
            "user_id": "u1",
            "conversation_id": "conv-inj",
            "attachments": [],
            "requires_tools": False,
        },
    )
    await note_interjections_injected(
        session,
        [
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={"interjection_id": "inj-inj", "content": "请点明成本"},
            )
        ],
    )
    assert session.get_interjection("inj-inj") is not None
    last = next(e for e in reversed(list(sink._history)) if e.type.value == "user_interjection")
    assert last.payload["status"] == "injected"


@pytest.mark.asyncio
async def test_wait_events_surfaces_user_interjection():
    session = CoordinationSession(execution_id="e2", total_workers=2)

    async def _post_soon() -> None:
        await asyncio.sleep(0.01)
        session.post(
            CoordinationEvent(
                kind=CoordinationEventKind.USER_INTERJECTION,
                payload={"interjection_id": "i", "content": "hi"},
            )
        )

    asyncio.create_task(_post_soon())
    batch = await session.wait_events(timeout=1.0)
    assert len(batch) == 1
    assert batch[0].kind is CoordinationEventKind.USER_INTERJECTION
