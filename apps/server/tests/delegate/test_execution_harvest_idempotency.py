"""Cross-process harvest idempotency: duplicate execution_id is already-harvested."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from agentcore.db.models.conversations import (
    UQ_MESSAGES_EXECUTION_HARVEST,
    is_execution_harvest_conflict,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    set_active_coordination,
)


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


def test_is_execution_harvest_conflict_reads_constraint_name():
    assert is_execution_harvest_conflict(
        IntegrityError("INSERT", {}, Exception(UQ_MESSAGES_EXECUTION_HARVEST))
    )
    assert not is_execution_harvest_conflict(
        IntegrityError("INSERT", {}, Exception("messages_pkey"))
    )


def _harvest_closing_conflict_harness(*, following, lease_open: bool):
    import agentcore.conversation.execution_harvest as eh

    session = CoordinationSession(
        execution_id="exec-dup",
        total_workers=1,
        conversation_id="conv-dup",
    )
    session.turn_attached = False
    set_active_coordination(session)
    conv = SimpleNamespace(user_id="user-1", folder_id=None, id="conv-dup")
    user = SimpleNamespace(user_id="user-1")
    selection = SimpleNamespace(origin="byok", provider_id="prov-1", model="m")
    byok = LLMCredentials(
        api_key="user-key",
        base_url="https://api.example/v1",
        source="user",
        provider_id="prov-1",
    )

    db = MagicMock()
    db.rollback = AsyncMock()
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    run_mock = AsyncMock()
    msg_repo = MagicMock()
    msg_repo.create = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception(UQ_MESSAGES_EXECUTION_HARVEST))
    )
    claimed = SimpleNamespace(id="user-harvest", created_at="t0")
    msg_repo.get_execution_harvest_user = AsyncMock(return_value=claimed)
    msg_repo.get_first_assistant_after = AsyncMock(return_value=following)

    lease_repo = MagicMock()
    lease_repo.exists_fresh_for_conversation = AsyncMock(return_value=lease_open)

    return eh, db, db_cm, run_mock, msg_repo, lease_repo, conv, user, selection, byok


@pytest.mark.asyncio
async def test_harvest_closing_skips_when_closing_assistant_already_settled():
    """Claim + settled assistant → do not start another CEO turn."""
    from datetime import datetime

    eh, db, db_cm, run_mock, msg_repo, lease_repo, conv, user, selection, byok = (
        _harvest_closing_conflict_harness(
            following=SimpleNamespace(
                id="asst-done",
                created_at=datetime(2026, 1, 1),
                usage={"status": "complete"},
            ),
            lease_open=False,
        )
    )

    with (
        patch.object(eh, "async_session_factory", return_value=db_cm),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "BoardRepository") as board_repo_cls,
        patch.object(eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=byok)),
        patch.object(eh, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_permission_axes", AsyncMock(return_value=None)),
        patch.object(eh, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(eh, "build_turn_backend", AsyncMock(return_value=MagicMock())),
        patch.object(eh, "run_and_persist", new=run_mock),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository", return_value=msg_repo),
        patch(
            "agentcore.runtime.leases.repo.TurnLeaseRepository",
            return_value=lease_repo,
        ),
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)

        await eh.run_harvest_closing_turn(
            conversation_id="conv-dup",
            execution_id="exec-dup",
        )

    run_mock.assert_not_called()
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_harvest_closing_continues_when_claim_has_no_assistant():
    """Crash after user-row insert must still run the CEO turn."""
    eh, db, db_cm, run_mock, msg_repo, lease_repo, conv, user, selection, byok = (
        _harvest_closing_conflict_harness(following=None, lease_open=False)
    )

    with (
        patch.object(eh, "async_session_factory", return_value=db_cm),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "BoardRepository") as board_repo_cls,
        patch.object(eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=byok)),
        patch.object(eh, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_permission_axes", AsyncMock(return_value=None)),
        patch.object(eh, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(eh, "build_turn_backend", AsyncMock(return_value=MagicMock())),
        patch.object(eh, "run_and_persist", new=run_mock),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository", return_value=msg_repo),
        patch(
            "agentcore.runtime.leases.repo.TurnLeaseRepository",
            return_value=lease_repo,
        ),
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)

        await eh.run_harvest_closing_turn(
            conversation_id="conv-dup",
            execution_id="exec-dup",
        )

    run_mock.assert_awaited()
    db.rollback.assert_awaited()
