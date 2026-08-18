"""Local sidecar harvest: bind_turn → outbox journal → local-turns origin (no PG)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.conversation.store import set_conversation_store
from agentcore.conversation.store.outbox import (
    OutboxStore,
    list_outbox_records,
    to_record_turn_body,
)
from agentcore.llm.credentials import INFERENCE_CONVERSATION_HEADER, LLMCredentials
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)
from agentcore.sidecar.server_pkg.core import (
    SidecarServer,
    reset_active_sidecar_for_tests,
    set_active_sidecar,
)


@pytest.fixture(autouse=True)
def _clean_coordination_and_sidecar():
    clear_active_coordination()
    reset_active_sidecar_for_tests()
    yield
    clear_active_coordination()
    reset_active_sidecar_for_tests()


def _session(
    execution_id: str = "exec-local", conversation_id: str = "conv-local"
) -> CoordinationSession:
    s = CoordinationSession(
        execution_id=execution_id,
        total_workers=1,
        conversation_id=conversation_id,
    )
    s.turn_attached = False
    s.birth_desk_id = "folder-1"
    s.folder_binding_injected = True
    s.folder_local_root_id = "root-1"
    s.folder_local_subpath = ""
    return s


def _sidecar(
    tmp_path,
    *,
    creds: LLMCredentials | None = None,
    write: Callable[[str], Awaitable[None]] | None = None,
) -> SidecarServer:
    async def _write(_line: str) -> None:
        return None

    sidecar = SidecarServer(write or _write)
    sidecar._initialized = True
    sidecar._user_id = "user-1"
    sidecar._root = tmp_path
    sidecar._creds = creds or LLMCredentials(
        api_key="sidecar-key",
        base_url="https://proxy.example/v1",
        source="user",
        provider_id="prov-1",
    )
    sidecar._outbox_store = OutboxStore(tmp_path / "outbox")
    sidecar._make_backend = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    set_conversation_store(sidecar._outbox_store)
    set_active_sidecar(sidecar)
    sidecar.stamp_folder_scope(
        "conv-local",
        folder_id="folder-1",
        binding_injected=True,
        local_root_id="root-1",
        local_subpath="",
    )
    return sidecar


def _pg_forbidden(*_a, **_k):
    raise AssertionError("local harvest must not open PG / write MessageRepository")


@pytest.mark.asyncio
async def test_local_harvest_does_not_write_pg(tmp_path):
    import agentcore.conversation.execution_harvest as eh

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured.update(kwargs)
        return {
            "message_id": kwargs["message_id"],
            "content": "终稿",
            "journal_entries": [],
        }

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "ConversationRepository", side_effect=_pg_forbidden),
        patch.object(eh, "resolve_local_binding", side_effect=_pg_forbidden),
        patch.object(eh, "platform_llm_credentials", side_effect=_pg_forbidden),
        patch.object(eh, "persist_harvest_fallback", side_effect=_pg_forbidden),
        patch.object(eh, "run_and_persist", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch(
            "agentcore.sidecar.server.run_chat_pipeline",
            new=_pipeline,
        ),
        patch.object(eh.turn_runs, "register", side_effect=_pg_forbidden),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["llm_credentials"].api_key == "sidecar-key"
    assert captured["llm_credentials"].extra_headers[INFERENCE_CONVERSATION_HEADER] == "conv-local"
    assert captured.get("continue_message_id") is None
    assert sidecar._turns == {}


@pytest.mark.asyncio
async def test_local_harvest_bind_then_journal_in_outbox(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.conversation.store import get_conversation_store

    session = _session()
    set_active_coordination(session)
    _sidecar(tmp_path)

    appended: list[int | None] = []

    async def _pipeline(**kwargs):
        store = get_conversation_store()
        mid = kwargs["message_id"]
        seq = await store.append_journal(
            turn_id=mid,
            seq=0,
            conversation_id=kwargs["conversation_id"],
            trace_id="a" * 32,
            entry={"kind": "run_started", "payload": {"via": "harvest"}},
        )
        appended.append(seq)
        return {
            "message_id": mid,
            "content": "终稿",
            "journal_entries": [
                {"kind": "run_started", "payload": {"via": "harvest"}},
            ],
        }

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "resolve_local_binding", side_effect=_pg_forbidden),
        patch.object(eh, "platform_llm_credentials", side_effect=_pg_forbidden),
        patch.object(eh, "persist_harvest_fallback", side_effect=_pg_forbidden),
        patch.object(eh, "run_and_persist", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert appended == [0]
    records = list_outbox_records(tmp_path / "outbox")
    assert len(records) == 1
    record = records[0]
    assert "begin_turn" in record["ops"]
    assert record["journal"]
    assert "0" in record["journal"]
    assert record["journal"]["0"]["kind"] == "run_started"
    assert record["origin"] == "execution_harvest"
    body = to_record_turn_body(record)
    assert body["origin"] == "execution_harvest"
    assert body["execution_id"] == "exec-local"
    assert body["harvest_kind"] == "success"
    assert body["journal"]


@pytest.mark.asyncio
async def test_local_harvest_ignores_recovered_turn_id(tmp_path):
    import agentcore.conversation.execution_harvest as eh

    session = _session()
    session.recovered_turn_id = "original-cloud-bubble"
    set_active_coordination(session)
    _sidecar(tmp_path)
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured.update(kwargs)
        return {"message_id": kwargs["message_id"], "content": "终稿", "journal_entries": []}

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "resolve_local_binding", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert "continue_message_id" not in captured
    assert captured["user_message"].startswith("【系统收口】")
    assert captured["history"] == []


@pytest.mark.asyncio
async def test_local_harvest_defers_while_startturn_registered(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.conversation.execution_harvest import HarvestDeferredError

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)

    async def _hang() -> None:
        await asyncio.Event().wait()

    live = asyncio.create_task(_hang())
    sidecar._register_turn("start-turn-id", live, conversation_id="conv-local")
    try:
        with pytest.raises(HarvestDeferredError):
            await eh.run_harvest_closing_turn(
                conversation_id="conv-local",
                execution_id="exec-local",
            )
    finally:
        live.cancel()
        with pytest.raises(asyncio.CancelledError):
            await live
        sidecar._unregister_turn("start-turn-id")


@pytest.mark.asyncio
async def test_local_harvest_uses_creds_for_not_platform_key(tmp_path):
    import agentcore.conversation.execution_harvest as eh

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured["creds"] = kwargs.get("llm_credentials")
        return {"message_id": kwargs["message_id"], "content": "终稿", "journal_entries": []}

    with (
        patch.object(eh, "platform_llm_credentials", side_effect=_pg_forbidden),
        patch.object(eh, "preflight_llm_credentials", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["creds"] is not None
    assert captured["creds"].api_key == "sidecar-key"
    expected = sidecar._creds_for("conv-local", "", "")
    assert expected is not None
    assert captured["creds"].api_key == expected.api_key


@pytest.mark.asyncio
async def test_local_harvest_pumps_turn_event_with_conversation_id(tmp_path):
    """Harvest LLM frames ride the same ``turn/event`` pump as startTurn."""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.runtime.events import content_delta

    sent: list[dict] = []

    async def _write(line: str) -> None:
        sent.append(json.loads(line))

    session = _session()
    session.recovered_turn_id = "original-cloud-bubble"
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path, write=_write)
    pump_calls: list[str] = []
    real_pump = sidecar._pump

    async def _spy_pump(turn_id: str, sink, **kwargs):
        pump_calls.append(turn_id)
        return await real_pump(turn_id, sink, **kwargs)

    sidecar._pump = _spy_pump  # type: ignore[method-assign]

    async def _pipeline(**kwargs):
        kwargs["sink"].emit(content_delta("进行中"))
        return {
            "message_id": kwargs["message_id"],
            "content": "终稿",
            "journal_entries": [],
        }

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "resolve_local_binding", side_effect=_pg_forbidden),
        patch.object(eh, "platform_llm_credentials", side_effect=_pg_forbidden),
        patch.object(eh, "persist_harvest_fallback", side_effect=_pg_forbidden),
        patch.object(eh, "run_and_persist", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert pump_calls
    turn_id = pump_calls[0]
    assert turn_id != "original-cloud-bubble"
    events = [m for m in sent if m.get("method") == "turn/event"]
    assert events
    for note in events:
        assert note["params"]["conversationId"] == "conv-local"
        assert note["params"]["turnId"] == turn_id
    assert any(note["params"]["event"]["type"] == "content_delta" for note in events)


@pytest.mark.asyncio
async def test_local_harvest_uses_stamped_history_not_empty(tmp_path):
    import agentcore.conversation.execution_harvest as eh

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    sidecar.stamp_turn_history(
        "conv-local",
        [{"role": "user", "content": "上一句"}, {"role": "assistant", "content": "上回"}],
    )
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured["history"] = kwargs.get("history")
        captured["user_message"] = kwargs.get("user_message")
        return {"message_id": kwargs["message_id"], "content": "终稿", "journal_entries": []}

    session.draft = "阶段草稿"
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    session._harvest_stash = [
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"completed": 1, "total": 1, "output": "队员成品正文"},
        )
    ]

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["history"] == [
        {"role": "user", "content": "上一句"},
        {"role": "assistant", "content": "上回"},
    ]
    assert "阶段草稿" in captured["user_message"]
    assert "队员成品正文" in captured["user_message"]


@pytest.mark.asyncio
async def test_local_harvest_prefers_cloud_chat_context(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.account.credentials import AccountCredentials

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    sidecar._account_creds = AccountCredentials(
        api_key="acct",
        base_url="https://cloud.example/v1/account",
    )
    sidecar.stamp_turn_history(
        "conv-local",
        [{"role": "user", "content": "stamped"}],
    )
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured["history"] = kwargs.get("history")
        return {"message_id": kwargs["message_id"], "content": "终稿", "journal_entries": []}

    async def _cloud(creds, *, conversation_id):
        assert conversation_id == "conv-local"
        assert creds.api_key == "acct"
        return {"history": [{"role": "user", "content": "from-cloud"}]}

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
        patch("agentcore.sidecar.chat_history.cloud_chat_context", new=_cloud),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["history"] == [{"role": "user", "content": "from-cloud"}]


@pytest.mark.asyncio
async def test_local_harvest_cloud_fail_without_stamp_does_not_run_empty(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.account.credentials import AccountCloudError, AccountCredentials

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    sidecar._account_creds = AccountCredentials(
        api_key="acct",
        base_url="https://cloud.example/v1/account",
    )
    ran = {"pipeline": False}

    async def _pipeline(**_kwargs):
        ran["pipeline"] = True
        return {"message_id": "x", "content": "不应开跑", "journal_entries": []}

    async def _boom(*_a, **_k):
        raise AccountCloudError("down", code="account_cloud_unreachable")

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
        patch("agentcore.sidecar.chat_history.cloud_chat_context", new=_boom),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert ran["pipeline"] is False
    records = list_outbox_records(tmp_path / "outbox")
    assert len(records) == 1
    content = to_record_turn_body(records[0])["content"]
    assert "未能加载对话历史" in content


@pytest.mark.asyncio
async def test_local_harvest_cloud_fail_uses_stamped_window(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.account.credentials import AccountCloudError, AccountCredentials

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    sidecar._account_creds = AccountCredentials(
        api_key="acct",
        base_url="https://cloud.example/v1/account",
    )
    sidecar.stamp_turn_history(
        "conv-local",
        [{"role": "user", "content": "stamped-ok"}],
    )
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured["history"] = kwargs.get("history")
        return {"message_id": kwargs["message_id"], "content": "终稿", "journal_entries": []}

    async def _boom(*_a, **_k):
        raise AccountCloudError("down", code="account_cloud_unreachable")

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
        patch("agentcore.sidecar.chat_history.cloud_chat_context", new=_boom),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["history"] == [{"role": "user", "content": "stamped-ok"}]


@pytest.mark.asyncio
async def test_local_harvest_missing_outbox_is_not_success(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.conversation.execution_harvest import HarvestNotReadyError
    from agentcore.runtime.coordination.session import active_coordination

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    sidecar._outbox_store = None

    with pytest.raises(HarvestNotReadyError):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )
    assert active_coordination("exec-local") is session


@pytest.mark.asyncio
async def test_sidecar_process_without_sidecar_does_not_open_pg(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.conversation.execution_harvest import HarvestNotReadyError
    from agentcore.sidecar.server_pkg.core import (
        reset_active_sidecar_for_tests,
        set_active_sidecar,
    )

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    set_active_sidecar(sidecar)
    set_active_sidecar(None)

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        pytest.raises(HarvestNotReadyError),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )
    reset_active_sidecar_for_tests()


@pytest.mark.asyncio
async def test_local_harvest_reads_this_conversation_permission_axes(tmp_path):
    import agentcore.conversation.execution_harvest as eh
    from agentcore.core.types import AutonomyPolicy, recipe_to_axes

    session = _session()
    set_active_coordination(session)
    sidecar = _sidecar(tmp_path)
    managed = recipe_to_axes(AutonomyPolicy.MANAGED)
    cautious = recipe_to_axes(AutonomyPolicy.CAUTIOUS)
    sidecar._permission_axes = cautious
    sidecar._permission_axes_by_conv["conv-other"] = cautious
    sidecar._permission_axes_by_conv["conv-local"] = managed
    captured: dict = {}

    async def _pipeline(**kwargs):
        captured["axes"] = kwargs.get("permission_axes")
        return {
            "message_id": kwargs["message_id"],
            "content": "终稿",
            "journal_entries": [],
        }

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", new=_pipeline),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-local",
        )

    assert captured["axes"] == managed


@pytest.mark.asyncio
async def test_local_harvest_fallback_persists_user_face_not_ceo_terminal(tmp_path):
    """Sidecar no-LLM fallback uses the same user renderer (not format_for_ceo)."""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    session = _session("exec-fb", "conv-local")
    session.workspace_channel_dead = True
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={
                "output": (
                    "### tool_failures\n"
                    "- `code_execute`：last_error=Sandbox crash\n"
                    "【终稿纪律】交付物在前"
                ),
                "completed": 1,
                "total": 1,
                "user_facts": {
                    "nodes": [
                        {
                            "role": "工程师",
                            "status": "completed",
                            "summary": "脚本已写好",
                            "files": ["run.py"],
                        }
                    ],
                    "files": ["run.py"],
                    "outstanding_tool_failures": [{"role": "工程师", "tool_name": "code_execute"}],
                },
            },
        )
    )
    set_active_coordination(session)
    _sidecar(tmp_path)

    with (
        patch.object(eh, "async_session_factory", side_effect=_pg_forbidden),
        patch.object(eh, "persist_harvest_fallback", side_effect=_pg_forbidden),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.sidecar.server.run_chat_pipeline", side_effect=_pg_forbidden),
    ):
        await eh.run_harvest_closing_turn(
            conversation_id="conv-local",
            execution_id="exec-fb",
        )

    records = list_outbox_records(tmp_path / "outbox")
    assert len(records) == 1
    content = to_record_turn_body(records[0])["content"]
    assert "### tool_failures" not in content
    assert "last_error=" not in content
    assert "终稿纪律" not in content
    assert "运行代码" in content
    assert "工程师" in content
