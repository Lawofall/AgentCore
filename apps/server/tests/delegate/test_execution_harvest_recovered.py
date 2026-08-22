"""崩溃重驱恢复的收口归属原回合（本次审计议题 D5）。

重驱把原回合跑完后，收口是那条消息的续写：不落合成用户消息、不新开助手消息，
成果与「曾中断恢复」标记都留在原气泡上。这里钉住 harvest 侧的三条分叉：
LLM 收口、无凭证兜底、普通（非恢复）detached drive 保持旧行为。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.errors import BYOKKeyMissingError
from agentcore.llm.credentials import LLMCredentials
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)

_ORIGINAL_TURN = "d5000000-0000-0000-0000-00000000turn"
_CONV = "d5000000-0000-0000-0000-00000000conv"
_EXEC = "d5000000-0000-0000-0000-00000000exec"


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


def _session(*, recovered_turn_id: str = "") -> CoordinationSession:
    s = CoordinationSession(
        execution_id=_EXEC,
        total_workers=1,
        conversation_id=_CONV,
    )
    s.turn_attached = False
    s.recovered_turn_id = recovered_turn_id
    return s


def _db_cm() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _conv_ctx():
    return (
        SimpleNamespace(user_id="user-1", folder_id=None, id=_CONV),
        SimpleNamespace(user_id="user-1"),
        SimpleNamespace(origin="byok", provider_id="prov-1", model="m"),
    )


@pytest.mark.asyncio
async def test_recovered_harvest_continues_original_turn():
    """恢复态收口：续写原消息，不建合成用户消息，journal 前缀带上原回合事实。"""
    import agentcore.conversation.execution_harvest as eh

    set_active_coordination(_session(recovered_turn_id=_ORIGINAL_TURN))
    conv, user, selection = _conv_ctx()
    byok = LLMCredentials(
        api_key="k", base_url="https://api.example/v1", source="user", provider_id="prov-1"
    )
    prior_journal = [{"kind": "run_plan", "payload": {"execution_id": _EXEC}, "ts": "t0"}]
    history = [
        {"role": "user", "content": "帮我做完这件事"},
        {"role": "assistant", "content": "开工中…"},
    ]
    captured: dict = {}

    async def _capture_run(**kwargs):
        captured.update(kwargs)
        return {"message_id": kwargs.get("continue_message_id"), "content": "终稿"}

    with (
        patch.object(eh, "async_session_factory", return_value=_db_cm()),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "BoardRepository") as board_repo_cls,
        patch.object(
            eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)
        ),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=byok)),
        patch.object(eh, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_permission_axes", AsyncMock(return_value=None)),
        patch.object(eh, "load_chat_context", AsyncMock(return_value=list(history))),
        patch.object(eh, "build_turn_backend", AsyncMock(return_value=MagicMock())),
        patch.object(eh, "run_and_persist", new=_capture_run),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch("agentcore.db.repositories.TurnJournalRepository") as journal_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)
        msg_repo_cls.return_value.create = AsyncMock()
        journal_repo_cls.return_value.load = AsyncMock(return_value=prior_journal)

        await eh.run_harvest_closing_turn(conversation_id=_CONV, execution_id=_EXEC)

        msg_repo_cls.return_value.create.assert_not_awaited()
        journal_repo_cls.return_value.load.assert_awaited_once_with(_ORIGINAL_TURN)

    assert captured["continue_message_id"] == _ORIGINAL_TURN
    assert captured["inherited_journal_entries"] == prior_journal
    # Nothing was appended to the window, so nothing may be sliced off it either.
    assert captured["history"] == history


@pytest.mark.asyncio
async def test_recovered_harvest_fallback_closes_original_turn_in_place():
    """无凭证兜底也归原回合：升级原行到终态，而不是追加一条助手消息。"""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.core.message_merge import MESSAGE_STATUS_COMPLETE

    session = _session(recovered_turn_id=_ORIGINAL_TURN)
    session.update_draft("## 结论\n已完成三件事。")
    set_active_coordination(session)
    conv, user, selection = _conv_ctx()
    run_mock = AsyncMock()

    with (
        patch.object(eh, "async_session_factory", return_value=_db_cm()),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(
            eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)
        ),
        patch.object(
            eh,
            "preflight_llm_credentials",
            AsyncMock(side_effect=BYOKKeyMissingError("请先配置 Key")),
        ),
        patch.object(eh, "run_and_persist", new=run_mock),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
    ):
        msg_repo_cls.return_value.create = AsyncMock()
        msg_repo_cls.return_value.upsert_assistant = AsyncMock()
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)

        await eh.run_harvest_closing_turn(conversation_id=_CONV, execution_id=_EXEC)

        run_mock.assert_not_called()
        msg_repo_cls.return_value.create.assert_not_awaited()
        kwargs = msg_repo_cls.return_value.upsert_assistant.await_args.kwargs

    assert kwargs["message_id"] == _ORIGINAL_TURN
    assert "已完成三件事" in kwargs["content"]
    # Terminal status is what stops the recovered bubble from spinning forever.
    assert kwargs["metadata"]["status"] == MESSAGE_STATUS_COMPLETE
    assert kwargs["metadata"]["origin"] == "execution_harvest_fallback"
    assert kwargs["merge"] is True


@pytest.mark.asyncio
async def test_plain_detached_harvest_still_opens_its_own_turn():
    """非恢复态不变：合成用户消息照发，收口仍是一条新助手消息。"""
    import agentcore.conversation.execution_harvest as eh

    set_active_coordination(_session())
    conv, user, selection = _conv_ctx()
    byok = LLMCredentials(
        api_key="k", base_url="https://api.example/v1", source="user", provider_id="prov-1"
    )
    history = [
        {"role": "user", "content": "帮我做完这件事"},
        {"role": "user", "content": "【系统收口】…"},
    ]
    captured: dict = {}

    async def _capture_run(**kwargs):
        captured.update(kwargs)
        return {"message_id": "fresh", "content": "终稿"}

    with (
        patch.object(eh, "async_session_factory", return_value=_db_cm()),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "BoardRepository") as board_repo_cls,
        patch.object(
            eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)
        ),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=byok)),
        patch.object(eh, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_permission_axes", AsyncMock(return_value=None)),
        patch.object(eh, "load_chat_context", AsyncMock(return_value=list(history))),
        patch.object(eh, "build_turn_backend", AsyncMock(return_value=MagicMock())),
        patch.object(eh, "run_and_persist", new=_capture_run),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch("agentcore.db.repositories.TurnJournalRepository") as journal_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)
        msg_repo_cls.return_value.create = AsyncMock()
        journal_repo_cls.return_value.load = AsyncMock()

        await eh.run_harvest_closing_turn(conversation_id=_CONV, execution_id=_EXEC)

        assert msg_repo_cls.return_value.create.await_args.kwargs["role"] == "user"
        journal_repo_cls.return_value.load.assert_not_awaited()

    assert captured["continue_message_id"] is None
    assert captured["inherited_journal_entries"] is None
    assert captured["history"] == history[:-1]
