"""Harvest closing turn must resolve credentials like a normal chat turn."""

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


@pytest.fixture(autouse=True)
def _clean_coordination():
    clear_active_coordination()
    yield
    clear_active_coordination()


def _session(execution_id: str = "exec-h", conversation_id: str = "conv-h") -> CoordinationSession:
    s = CoordinationSession(
        execution_id=execution_id,
        total_workers=1,
        conversation_id=conversation_id,
    )
    s.turn_attached = False
    return s


@pytest.mark.asyncio
async def test_harvest_closing_passes_preflight_credentials_to_run():
    """Regression: llm_credentials=None forced the revoked platform key path."""
    import agentcore.conversation.execution_harvest as eh

    session = _session()
    set_active_coordination(session)
    byok = LLMCredentials(
        api_key="user-key",
        base_url="https://api.example/v1",
        source="user",
        provider_id="prov-1",
    )
    conv = SimpleNamespace(user_id="user-1", folder_id=None, id="conv-h")
    user = SimpleNamespace(user_id="user-1")
    selection = SimpleNamespace(origin="byok", provider_id="prov-1", model="deepseek-v4-flash")

    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    db_cm.__aexit__ = AsyncMock(return_value=None)

    captured: dict = {}

    async def _capture_run(**kwargs):
        captured["llm_credentials"] = kwargs.get("llm_credentials")

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
        patch.object(eh, "run_and_persist", new=_capture_run),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)
        msg_repo_cls.return_value.create = AsyncMock()

        await eh.run_harvest_closing_turn(
            conversation_id="conv-h",
            execution_id="exec-h",
        )

    assert captured["llm_credentials"] is byok
    assert captured["llm_credentials"].source == "user"


@pytest.mark.asyncio
async def test_harvest_closing_persists_fallback_when_preflight_refuses():
    """A1: quota/BYOK refuse → push existing synthesis without LLM turn."""
    import agentcore.conversation.execution_harvest as eh

    session = _session("exec-refuse", "conv-refuse")
    session.update_draft("## 架构结论\n模块 A 依赖 B。")
    session.workspace_channel_dead = True
    set_active_coordination(session)
    conv = SimpleNamespace(user_id="user-1", folder_id=None, id="conv-refuse")
    user = SimpleNamespace(user_id="user-1")
    selection = SimpleNamespace(origin="byok", provider_id=None, model="m")

    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    db_cm.__aexit__ = AsyncMock(return_value=None)

    run_mock = AsyncMock()
    msg_create = AsyncMock()
    notify = AsyncMock()

    with (
        patch.object(eh, "async_session_factory", return_value=db_cm),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)),
        patch.object(
            eh,
            "preflight_llm_credentials",
            AsyncMock(side_effect=BYOKKeyMissingError("请先配置 Key")),
        ),
        patch.object(eh, "run_and_persist", new=run_mock),
        patch.object(eh, "notify_user", new=notify),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        msg_repo_cls.return_value.create = msg_create

        await eh.run_harvest_closing_turn(
            conversation_id="conv-refuse",
            execution_id="exec-refuse",
        )

    run_mock.assert_not_called()
    msg_create.assert_called_once()
    kwargs = msg_create.await_args.kwargs
    assert kwargs["role"] == "assistant"
    assert "架构结论" in kwargs["content"]
    assert "本地文件暂时连不上" in kwargs["content"]
    assert "请先配置 Key" in kwargs["content"]
    assert kwargs["metadata"]["origin"] == "execution_harvest_fallback"
    assert kwargs["metadata"]["no_llm"] is True
    notify.assert_awaited()


def test_build_harvest_fallback_prefers_draft_over_terminal():
    import agentcore.conversation.execution_harvest as eh
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    session = _session("exec-b", "conv-b")
    session.update_draft("草稿优先")
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={"output": "终端正文不应优先", "completed": 1, "total": 1},
        )
    )
    text = eh.build_harvest_fallback_content(session, kind="success", error_message="额度已满")
    assert "草稿优先" in text
    assert "终端正文" not in text
    assert "额度已满" in text


def test_build_harvest_fallback_uses_user_facts_not_ceo_terminal():
    import agentcore.conversation.execution_harvest as eh
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    session = _session("exec-t", "conv-t")
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={
                "output": "## 团队成品\n结论 X\n### tool_failures\nlast_error=boom\n【终稿纪律】",
                "completed": 2,
                "total": 2,
                "user_facts": {
                    "nodes": [
                        {
                            "role": "调研",
                            "status": "completed",
                            "summary": "结论 X",
                            "files": [],
                        }
                    ],
                    "files": [],
                    "outstanding_tool_failures": [],
                },
            },
        )
    )
    text = eh.build_harvest_fallback_content(session, kind="success")
    assert "结论 X" in text
    assert "### tool_failures" not in text
    assert "last_error=" not in text
    assert "终稿纪律" not in text
    assert "本地文件暂时连不上" not in text


def test_build_harvest_fallback_omits_ceo_internals_keeps_outstanding_failures():
    """User bubble must not dump CEO-audience blocks; uncompensated failures stay."""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.runtime.coordination.session import (
        CoordinationEvent,
        CoordinationEventKind,
    )

    ceo = (
        "## 团队执行结果（据此写一段简短概览交给用户；完整详情用户自行查看）\n"
        "### tool_failures\n"
        "- `code_execute`：failures=2，succeeded_after=false，last_error=Sandbox crash\n"
        "【终稿纪律】交付物在前、过程至多一段\n"
    )
    session = _session("exec-honest", "conv-honest")
    session._pending.append(
        CoordinationEvent(
            kind=CoordinationEventKind.ALL_COMPLETED,
            payload={
                "output": ceo,
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
    text = eh.build_harvest_fallback_content(session, kind="failure")
    assert "### tool_failures" not in text
    assert "last_error=" not in text
    assert "终稿纪律" not in text
    assert "Sandbox crash" not in text
    assert "运行代码" in text
    assert "工程师" in text
    assert "run.py" in text
    assert "没做成" in text or "没有成功" in text


def test_build_harvest_fallback_channel_dead_notice_from_flag():
    import agentcore.conversation.execution_harvest as eh
    from agentcore.workspace.limits import CHANNEL_DEAD_USER_VISIBLE

    session = _session("exec-cd", "conv-cd")
    session.workspace_channel_dead = True
    session.update_draft("已有分析")
    text = eh.build_harvest_fallback_content(session, kind="failure")
    assert text.startswith(CHANNEL_DEAD_USER_VISIBLE)
    assert "已有分析" in text


@pytest.mark.asyncio
async def test_harvest_skips_llm_when_session_channel_already_dead():
    """已知通道死：不跑收口 LLM，直接 fallback。"""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT, CHANNEL_DEAD_USER_VISIBLE

    session = _session("exec-dead", "conv-dead")
    session.workspace_channel_dead = True
    session.update_draft("队员已交的综合草稿")
    session.failed_run_ids.add("r1")
    set_active_coordination(session)
    conv = SimpleNamespace(user_id="user-1", folder_id=None, id="conv-dead")
    user = SimpleNamespace(user_id="user-1")
    selection = SimpleNamespace(origin="platform", provider_id=None, model="m")
    creds = LLMCredentials(api_key="k", base_url="https://x", source="platform")

    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    db_cm.__aexit__ = AsyncMock(return_value=None)

    run_mock = AsyncMock()
    msg_create = AsyncMock()
    notify = AsyncMock()

    with (
        patch.object(eh, "async_session_factory", return_value=db_cm),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=creds)),
        patch.object(eh, "platform_llm_credentials", return_value=creds),
        patch.object(eh, "run_and_persist", new=run_mock),
        patch.object(eh, "build_turn_backend", new=AsyncMock()),
        patch.object(eh, "notify_user", new=notify),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        msg_repo_cls.return_value.create = msg_create

        await eh.run_harvest_closing_turn(
            conversation_id="conv-dead",
            execution_id="exec-dead",
        )

    run_mock.assert_not_called()
    msg_create.assert_called_once()
    kwargs = msg_create.await_args.kwargs
    assert kwargs["role"] == "assistant"
    assert CHANNEL_DEAD_USER_VISIBLE in kwargs["content"]
    assert "队员已交的综合草稿" in kwargs["content"]
    assert CHANNEL_DEAD_PREPARE_ABORT in kwargs["content"]
    assert kwargs["metadata"]["origin"] == "execution_harvest_fallback"
    assert kwargs["metadata"]["channel_dead"] is True
    notify.assert_awaited()


def test_result_is_channel_dead_abort_detects_prepare_abort():
    import agentcore.conversation.execution_harvest as eh
    from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT
    from agentcore.workspace.protocol import WorkspaceIOError

    assert eh._result_is_channel_dead_abort({"error": CHANNEL_DEAD_PREPARE_ABORT, "content": ""})
    assert eh._result_is_channel_dead_abort({"error": {"message": CHANNEL_DEAD_PREPARE_ABORT}})
    assert not eh._result_is_channel_dead_abort({"error": None, "content": "ok"})
    assert not eh._result_is_channel_dead_abort({"error": "quota exceeded"})
    assert eh._exc_is_channel_dead(WorkspaceIOError(CHANNEL_DEAD_PREPARE_ABORT))
    assert not eh._exc_is_channel_dead(RuntimeError("other"))


@pytest.mark.asyncio
async def test_harvest_fallback_when_run_returns_channel_dead():
    """收口回合返回通道死错误 → 调用 persist_harvest_fallback。"""
    import agentcore.conversation.execution_harvest as eh
    from agentcore.workspace.limits import CHANNEL_DEAD_PREPARE_ABORT

    session = _session("exec-salv", "conv-salv")
    session.update_draft("终端前已有草稿")
    set_active_coordination(session)
    conv = SimpleNamespace(user_id="user-1", folder_id=None, id="conv-salv")
    user = SimpleNamespace(user_id="user-1")
    selection = SimpleNamespace(origin="byok", provider_id="p", model="m")
    creds = LLMCredentials(api_key="k", base_url="https://x", source="user", provider_id="p")

    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    db_cm.__aexit__ = AsyncMock(return_value=None)

    async def _dead_result(**_kwargs):
        return {
            "message_id": "asst-empty",
            "content": "",
            "error": CHANNEL_DEAD_PREPARE_ABORT,
        }

    fallback = AsyncMock(return_value="fb")

    with (
        patch.object(eh, "async_session_factory", return_value=db_cm),
        patch.object(eh, "ConversationRepository") as conv_repo_cls,
        patch.object(eh, "UserRepository") as user_repo_cls,
        patch.object(eh, "CostEventRepository"),
        patch.object(eh, "BoardRepository") as board_repo_cls,
        patch.object(eh, "resolve_conversation_model_selection", AsyncMock(return_value=selection)),
        patch.object(eh, "preflight_llm_credentials", AsyncMock(return_value=creds)),
        patch.object(eh, "resolve_local_binding", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_profile_set", AsyncMock(return_value=None)),
        patch.object(eh, "resolve_permission_axes", AsyncMock(return_value=None)),
        patch.object(eh, "load_chat_context", AsyncMock(return_value=[])),
        patch.object(eh, "build_turn_backend", AsyncMock(return_value=MagicMock())),
        patch.object(eh, "run_and_persist", new=_dead_result),
        patch.object(eh, "persist_harvest_fallback", new=fallback),
        patch.object(eh, "notify_user", AsyncMock()),
        patch("agentcore.db.repositories.MessageRepository") as msg_repo_cls,
        patch.object(eh.turn_runs, "get", return_value=None),
        patch.object(eh.turn_runs, "register"),
    ):
        conv_repo_cls.return_value.get_by_id_unscoped = AsyncMock(return_value=conv)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)
        board_repo_cls.return_value.get_by_conversation_id = AsyncMock(return_value=None)
        msg_repo_cls.return_value.create = AsyncMock()
        await eh.run_harvest_closing_turn(
            conversation_id="conv-salv",
            execution_id="exec-salv",
        )

    fallback.assert_awaited()
    assert session.workspace_channel_dead is True


def test_collect_harvest_user_facts_outstanding_only():
    from agentcore.runtime.delegate.drive_terminal import collect_harvest_user_facts
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState

    plan = RunPlan(nodes=[RunSpec(run_id="w1", task="跑脚本", role="工程师")])
    results = {
        "w1": RunState(
            phase=RunPhase.COMPLETED,
            content="脚本已写好",
            file_acceptance=[{"path": "run.py", "status": "accepted"}],
            tool_failures=[
                {
                    "tool_name": "code_execute",
                    "failure_count": 2,
                    "last_error": "Sandbox crash",
                    "succeeded_after": False,
                },
                {
                    "tool_name": "web_search",
                    "failure_count": 1,
                    "last_error": "tmp",
                    "succeeded_after": True,
                },
            ],
        )
    }
    facts = collect_harvest_user_facts(plan, results)
    assert facts["files"] == ["run.py"]
    assert facts["nodes"][0]["role"] == "工程师"
    names = {row["tool_name"] for row in facts["outstanding_tool_failures"]}
    assert names == {"code_execute"}
