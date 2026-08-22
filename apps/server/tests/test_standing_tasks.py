"""Standing tasks L1: cron, lease claimability, run status mapping, cloud folder gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.api.routes.standing_tasks import _require_cloud_folder
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.repositories.standing_tasks import is_lease_free, is_task_claimable
from agentcore.runtime.events import FinishReason
from agentcore.standing_tasks.runner import _finish_is_paused, _truncate_summary
from agentcore.standing_tasks.schedule import (
    CRON_PRESETS,
    CronError,
    next_run_after,
    resolve_cron,
    validate_cron,
)


class TestCronNextRun:
    def test_weekly_monday_advances_to_next_monday(self):
        # 2026-07-28 is Tuesday; next Monday 09:00 UTC.
        after = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
        nxt = next_run_after("0 9 * * 1", after)
        assert nxt == datetime(2026, 8, 3, 9, 0, tzinfo=UTC)

    def test_hourly_preset(self):
        after = datetime(2026, 7, 28, 10, 15, tzinfo=UTC)
        cron = resolve_cron(schedule_preset="hourly")
        assert cron == CRON_PRESETS["hourly"]
        nxt = next_run_after(cron, after)
        assert nxt == datetime(2026, 7, 28, 11, 0, tzinfo=UTC)

    def test_daily_same_day_if_before_fire(self):
        after = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
        nxt = next_run_after("0 9 * * *", after)
        assert nxt == datetime(2026, 7, 28, 9, 0, tzinfo=UTC)

    def test_custom_dom_and_dow_both_set_fire_on_either(self):
        # Vixie: 日与周都非 * → 取或。「每月 1 号 或 每周一 09:00」
        # 2026-08-13 is a Thursday → the Monday hit lands first (dow side of the OR).
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 1 * 1", after) == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        # 2026-09-01 is a Tuesday: the dom side fires on its own (under the old AND
        # semantics the next fire would not have come until 2027-02-01).
        after = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 1 * 1", after) == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    def test_star_day_field_keeps_and_semantics(self):
        # Only one day field restricted → the other is trivially true (AND).
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        # Mondays only — the 1st must not pull an extra fire in.
        assert next_run_after("0 9 * * 1", after) == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        # 1st only — Mondays must not pull an extra fire in.
        assert next_run_after("0 9 1 * *", after) == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    def test_step_dom_counts_as_star_for_the_day_rule(self):
        # Vixie sets DOM_STAR for any field starting with ``*`` (``*/n`` included),
        # so ``*/10`` + Monday is AND: the first Monday whose dom ∈ {1,11,21,31}.
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 */10 * 1", after) == datetime(2026, 8, 31, 9, 0, tzinfo=UTC)

    def test_presets_unaffected_by_the_or_rule(self):
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        assert next_run_after(CRON_PRESETS["weekly_mon"], after) == datetime(
            2026, 8, 17, 9, 0, tzinfo=UTC
        )
        assert next_run_after(CRON_PRESETS["monthly_1"], after) == datetime(
            2026, 9, 1, 9, 0, tzinfo=UTC
        )

    def test_invalid_cron_raises(self):
        with pytest.raises(CronError):
            validate_cron("not a cron")
        with pytest.raises(CronError):
            resolve_cron(cron="0 9 * * *", schedule_preset="daily")

    def test_desktop_schedule_presets(self):
        assert resolve_cron(schedule_preset="weekly_mon") == "0 9 * * 1"
        assert resolve_cron(schedule_preset="weekly_fri") == "0 9 * * 5"
        assert resolve_cron(schedule_preset="monthly_1") == "0 9 1 * *"
        assert resolve_cron(schedule_preset="custom", cron="30 8 * * 2") == "30 8 * * 2"
        from agentcore.standing_tasks.schedule import infer_schedule_preset

        assert infer_schedule_preset("0 9 * * 1") == "weekly_mon"
        assert infer_schedule_preset("15 3 * * *") == "custom"
        with pytest.raises(CronError):
            resolve_cron(schedule_preset="weekly")
        with pytest.raises(CronError):
            resolve_cron(schedule_preset="monthly")

class TestLeaseClaimable:
    def test_due_and_unlocked_is_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_task_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
        )

    def test_active_lease_blocks_second_claim(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_task_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=now + timedelta(minutes=10),
            now=now,
        )

    def test_expired_lease_allows_reclaim(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_task_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=now - timedelta(seconds=1),
            now=now,
        )

    def test_disabled_skipped(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_task_claimable(
            enabled=False,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
        )

    def test_future_next_run_skipped(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_task_claimable(
            enabled=True,
            next_run_at=now + timedelta(minutes=5),
            lease_until=None,
            now=now,
        )

    def test_webhook_trigger_never_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_task_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
            trigger_kind="webhook",
        )

    def test_null_next_run_not_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_task_claimable(
            enabled=True,
            next_run_at=None,
            lease_until=None,
            now=now,
            trigger_kind="schedule",
        )


class TestCloudFolderGate:
    def test_missing_folder_404(self):
        with pytest.raises(NotFoundError):
            _require_cloud_folder(None)

    def test_local_folder_rejected(self):
        folder = SimpleNamespace(local_root_id="desktop-root-1", id="f1")
        with pytest.raises(ValidationError, match="云工作区"):
            _require_cloud_folder(folder)

    def test_cloud_folder_ok(self):
        folder = SimpleNamespace(local_root_id=None, id="f1")
        _require_cloud_folder(folder)  # no raise


class TestRunStatusMapping:
    def test_paused_finish_reason(self):
        assert _finish_is_paused(FinishReason.PAUSED)
        assert _finish_is_paused("paused")
        assert not _finish_is_paused(FinishReason.END_TURN)
        assert not _finish_is_paused("stop")

    def test_summary_truncate(self):
        assert _truncate_summary(None) is None
        assert _truncate_summary("short") == "short"
        long = "x" * 600
        out = _truncate_summary(long)
        assert out is not None
        assert len(out) == 500
        assert out.endswith("…")


@pytest.mark.asyncio
async def test_run_job_succeeded(monkeypatch):
    """Pipeline success → inbox status succeeded + summary."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="周一简报",
        name="简报",
        permission_axes={"file_write": "session", "command": "auto", "host": "session"},
        cron="0 9 * * 1",
        enabled=True,
        conversation_id="conv-1",
        local_root_id=None,
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    run_marks: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            run_marks["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            run_marks["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="简报")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(
        runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        runner_mod, "resolve_profile_set", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_pipeline(**kwargs):
        return {"finish_reason": FinishReason.END_TURN, "content": "本周摘要 OK"}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_pipeline)

    await runner_mod.run_standing_task_job(run_id="run-1", task_id="task-1", advance_schedule=False)
    assert "succeeded" in run_marks
    assert run_marks["succeeded"] == "本周摘要 OK"
    assert "awaiting_user" not in run_marks
    assert "failed" not in run_marks


@pytest.mark.asyncio
async def test_run_job_awaiting_user(monkeypatch):
    """Paused finish → awaiting_user."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="需授权",
        name="授权任务",
        permission_axes={},
        cron="0 9 * * *",
        enabled=True,
        conversation_id="conv-1",
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    run_marks: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            run_marks["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            run_marks["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="t")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            return True

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(
        runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(runner_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_pipeline(**kwargs):
        return {"finish_reason": FinishReason.PAUSED, "content": ""}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_pipeline)

    await runner_mod.run_standing_task_job(run_id="run-2", task_id="task-1", advance_schedule=False)
    assert "awaiting_user" in run_marks
    assert "succeeded" not in run_marks


@pytest.mark.asyncio
async def test_run_job_ignores_residual_conversation_pause(monkeypatch):
    """ST-1: old cold pause on another turn must not mark a successful fire awaiting_user."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="周一简报",
        name="简报",
        permission_axes={},
        cron="0 9 * * 1",
        enabled=True,
        conversation_id="conv-1",
        local_root_id=None,
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    run_marks: dict[str, object] = {}
    probed: dict[str, str] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            run_marks["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            run_marks["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="简报")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            # Residual cold pause still present — must NOT drive this fire's status.
            return True

        async def exists_for_message(self, mid):
            probed["message_id"] = mid
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(
        runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        runner_mod, "resolve_profile_set", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_pipeline(**kwargs):
        return {
            "finish_reason": FinishReason.END_TURN,
            "content": "本火成功",
            "message_id": "turn-this-fire",
        }

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_pipeline)

    await runner_mod.run_standing_task_job(run_id="run-new", task_id="task-1", advance_schedule=False)
    assert probed.get("message_id") == "turn-this-fire"
    assert "succeeded" in run_marks
    assert run_marks["succeeded"] == "本火成功"
    assert "awaiting_user" not in run_marks


@pytest.mark.asyncio
async def test_run_job_awaiting_user_via_this_turn_pause(monkeypatch):
    """ST-1: this fire's turn still in paused_turns → awaiting_user (even without PAUSED finish)."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="需授权",
        name="授权任务",
        permission_axes={},
        cron="0 9 * * *",
        enabled=True,
        conversation_id="conv-1",
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    run_marks: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            run_marks["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            run_marks["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="t")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_message(self, mid):
            return mid == "turn-paused-now"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(
        runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(runner_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_pipeline(**kwargs):
        # Production CEO path may omit finish_reason; pause truth is paused_turns.
        return {"content": "", "message_id": "turn-paused-now"}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_pipeline)

    await runner_mod.run_standing_task_job(run_id="run-pause", task_id="task-1", advance_schedule=False)
    assert "awaiting_user" in run_marks
    assert "succeeded" not in run_marks


# ---------------------------------------------------------------------------
# L2a webhook
# ---------------------------------------------------------------------------


class TestWebhookHelpers:
    def test_extract_text_from_json_text_field(self):
        from agentcore.standing_tasks.webhook import extract_event_text

        body = '{"text": "新线索 A", "extra": 1}'.encode()
        assert extract_event_text(body, content_type="application/json") == "新线索 A"

    def test_extract_message_field(self):
        from agentcore.standing_tasks.webhook import extract_event_text

        body = b'{"message": "hello from zapier"}'
        assert extract_event_text(body, content_type="application/json") == "hello from zapier"

    def test_extract_falls_back_to_raw_body(self):
        from agentcore.standing_tasks.webhook import extract_event_text

        assert extract_event_text(b"plain event", content_type="text/plain") == "plain event"

    def test_build_fire_message_appends_event(self):
        from agentcore.standing_tasks.webhook import build_fire_message

        msg = build_fire_message(goal="分诊线索", event_text="张三报名")
        assert msg == "分诊线索\n\n本次事件：张三报名"

    def test_secret_roundtrip(self):
        from agentcore.standing_tasks.webhook import (
            generate_webhook_secret,
            verify_webhook_secret,
        )

        raw, hashed = generate_webhook_secret()
        assert verify_webhook_secret(raw, hashed)
        assert not verify_webhook_secret("wrong", hashed)
        assert not verify_webhook_secret(raw, "0" * 64)

    def test_require_secret_bearer_and_header(self):
        from agentcore.core.errors import AuthenticationError
        from agentcore.standing_tasks.webhook import (
            generate_webhook_secret,
            require_webhook_secret,
        )

        raw, hashed = generate_webhook_secret()
        require_webhook_secret(
            authorization=f"Bearer {raw}",
            x_webhook_secret=None,
            expected_hash=hashed,
        )
        require_webhook_secret(
            authorization=None,
            x_webhook_secret=raw,
            expected_hash=hashed,
        )
        with pytest.raises(AuthenticationError):
            require_webhook_secret(
                authorization="Bearer nope",
                x_webhook_secret=None,
                expected_hash=hashed,
            )

    def test_idempotency_same_key_returns_same_run(self):
        from agentcore.standing_tasks import webhook as wh

        wh.reset_webhook_state()
        assert wh.idempotency_lookup("wid-1", "k1") is None
        wh.idempotency_store("wid-1", "k1", "run-aaa")
        assert wh.idempotency_lookup("wid-1", "k1") == "run-aaa"
        assert wh.idempotency_lookup("wid-1", "k2") is None
        wh.reset_webhook_state()

    def test_rate_limit_trips(self, monkeypatch):
        from agentcore.core.errors import RateLimitedError
        from agentcore.middleware.rate_limit import SlidingWindowRateLimiter
        from agentcore.standing_tasks import webhook as wh

        wh.reset_webhook_state()
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        monkeypatch.setattr(wh, "_webhook_rate_limiter", limiter)
        monkeypatch.setattr(wh.settings, "rate_limit_enabled", True)
        monkeypatch.setattr(wh.settings, "standing_task_webhook_rate_limit_max", 2)
        wh.enforce_webhook_rate_limit("task-rl", now=1000.0)
        wh.enforce_webhook_rate_limit("task-rl", now=1001.0)
        with pytest.raises(RateLimitedError):
            wh.enforce_webhook_rate_limit("task-rl", now=1002.0)
        wh.reset_webhook_state()


class TestWebhookCreateSchema:
    def test_webhook_rejects_cron(self):
        from pydantic import ValidationError as PydValidationError

        from agentcore.api.schemas.standing_tasks import CreateStandingTaskRequest

        with pytest.raises(PydValidationError):
            CreateStandingTaskRequest(
                name="w",
                goal="g",
                folder_id="f1",
                trigger_kind="webhook",
                schedule_preset="daily",
            )

    def test_webhook_ok_without_schedule(self):
        from agentcore.api.schemas.standing_tasks import CreateStandingTaskRequest

        body = CreateStandingTaskRequest(
            name="w",
            goal="g",
            folder_id="f1",
            trigger_kind="webhook",
        )
        assert body.trigger_kind == "webhook"


@pytest.mark.asyncio
async def test_fire_webhook_auth_failure(monkeypatch):
    """Wrong secret → AuthenticationError; schedule task not found by webhook_id."""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.core.errors import AuthenticationError, NotFoundError
    from agentcore.standing_tasks.webhook import generate_webhook_secret

    raw, hashed = generate_webhook_secret()
    webhook_task = SimpleNamespace(
        id="task-w",
        user_id="user-1",
        folder_id="folder-1",
        enabled=True,
        trigger_kind="webhook",
        webhook_id="wid-1",
        webhook_secret_hash=hashed,
    )

    class _Repo:
        async def get_by_webhook_id(self, wid):
            if wid == "wid-1":
                return webhook_task
            return None

    class _Folders:
        async def get_by_id(self, *a, **k):
            return SimpleNamespace(id="folder-1", local_root_id=None)

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"text":"x"}'

    with pytest.raises(NotFoundError):
        await routes.fire_standing_webhook(
            webhook_id="missing",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization="Bearer anything",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )

    with pytest.raises(AuthenticationError):
        await routes.fire_standing_webhook(
            webhook_id="wid-1",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization="Bearer wrong-secret",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )


@pytest.mark.asyncio
async def test_fire_webhook_success_and_idempotent(monkeypatch):
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.standing_tasks import webhook as wh
    from agentcore.standing_tasks.webhook import generate_webhook_secret

    wh.reset_webhook_state()
    raw, hashed = generate_webhook_secret()
    webhook_task = SimpleNamespace(
        id="task-w",
        user_id="user-1",
        folder_id="folder-1",
        enabled=True,
        trigger_kind="webhook",
        webhook_id="wid-1",
        webhook_secret_hash=hashed,
    )
    dispatches: list[dict] = []

    async def fake_dispatch(**kwargs):
        dispatches.append(kwargs)
        return f"run-{len(dispatches)}"

    monkeypatch.setattr(routes, "dispatch_standing_task", fake_dispatch)

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return webhook_task if wid == "wid-1" else None

    class _Folders:
        async def get_by_id(self, *a, **k):
            return SimpleNamespace(id="folder-1", local_root_id=None)

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return '{"text":"线索一"}'.encode()

    r1 = await routes.fire_standing_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=f"Bearer {raw}",
        x_agentcore_webhook_secret=None,
        x_idempotency_key="idem-1",
    )
    r2 = await routes.fire_standing_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=None,
        x_agentcore_webhook_secret=raw,
        x_idempotency_key="idem-1",
    )
    assert r1.run_id == r2.run_id == "run-1"
    assert len(dispatches) == 1
    assert dispatches[0]["trigger_source"] == "webhook"
    assert dispatches[0]["event_text"] == "线索一"
    assert dispatches[0]["advance_schedule"] is False
    wh.reset_webhook_state()


@pytest.mark.asyncio
async def test_fire_webhook_rate_limited(monkeypatch):
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.core.errors import RateLimitedError
    from agentcore.middleware.rate_limit import SlidingWindowRateLimiter
    from agentcore.standing_tasks import webhook as wh
    from agentcore.standing_tasks.webhook import generate_webhook_secret

    wh.reset_webhook_state()
    monkeypatch.setattr(
        wh, "_webhook_rate_limiter", SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    )
    monkeypatch.setattr(wh.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(wh.settings, "standing_task_webhook_rate_limit_max", 1)

    raw, hashed = generate_webhook_secret()
    webhook_task = SimpleNamespace(
        id="task-w",
        user_id="user-1",
        folder_id="folder-1",
        enabled=True,
        trigger_kind="webhook",
        webhook_id="wid-1",
        webhook_secret_hash=hashed,
    )

    async def fake_dispatch(**kwargs):
        return "run-x"

    monkeypatch.setattr(routes, "dispatch_standing_task", fake_dispatch)

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return webhook_task

    class _Folders:
        async def get_by_id(self, *a, **k):
            return SimpleNamespace(id="folder-1", local_root_id=None)

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b"{}"

    await routes.fire_standing_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=f"Bearer {raw}",
        x_agentcore_webhook_secret=None,
        x_idempotency_key=None,
    )
    with pytest.raises(RateLimitedError):
        await routes.fire_standing_webhook(
            webhook_id="wid-1",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization=f"Bearer {raw}",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )
    wh.reset_webhook_state()


@pytest.mark.asyncio
async def test_schedule_task_not_found_via_webhook_lookup():
    """Schedule tasks have no webhook_id → get_by_webhook_id returns None → 404."""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.core.errors import NotFoundError

    class _Repo:
        async def get_by_webhook_id(self, wid):
            # Mimic repo filter: schedule rows never match webhook_id lookup.
            return None

    class _Folders:
        async def get_by_id(self, *a, **k):
            return SimpleNamespace(id="folder-1", local_root_id=None)

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b"{}"

    with pytest.raises(NotFoundError):
        await routes.fire_standing_webhook(
            webhook_id="any-id",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization="Bearer x",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )


@pytest.mark.asyncio
async def test_run_job_includes_event_text(monkeypatch):
    """Webhook fire appends 本次事件 to the user message passed to the pipeline."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="常驻目标",
        name="简报",
        permission_axes={},
        cron=None,
        trigger_kind="webhook",
        enabled=True,
        conversation_id="conv-1",
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    captured: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            captured["advanced"] = True

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            captured["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            captured["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            captured["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, *a, **k):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="t")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            captured["msg_content"] = kwargs.get("content")
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_pipeline(**kwargs):
        captured["pipeline_msg"] = kwargs.get("user_message")
        return {"finish_reason": FinishReason.END_TURN, "content": "ok"}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_pipeline)

    await runner_mod.run_standing_task_job(
        run_id="run-w",
        task_id="task-1",
        advance_schedule=True,  # even if True, webhook has no cron → must not advance
        event_text="外部事件",
    )
    assert captured["msg_content"] == "常驻目标\n\n本次事件：外部事件"
    assert captured["pipeline_msg"] == "常驻目标\n\n本次事件：外部事件"
    assert "advanced" not in captured
    assert "succeeded" in captured


# ---------------------------------------------------------------------------
# GAP-1: task-level lease mutex for webhook / manual dispatch
# ---------------------------------------------------------------------------


class TestDispatchLeaseFree:
    def test_absent_lease_is_free(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_lease_free(lease_until=None, now=now)

    def test_active_lease_not_free(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_lease_free(lease_until=now + timedelta(minutes=5), now=now)

    def test_expired_lease_is_free(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_lease_free(lease_until=now - timedelta(seconds=1), now=now)


@pytest.mark.asyncio
async def test_dispatch_claims_lease_and_rejects_in_flight(monkeypatch):
    """Webhook/manual dispatch claims lease; second claim → ConflictError (409)."""
    from agentcore.core.errors import ConflictError
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(id="task-1", conversation_id="conv-1", user_id="u1")
    claims: list[str] = []
    cleared: list[str] = []

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, *, user_id=None):
            return task if task_id == "task-1" else None

        async def claim_dispatch(self, task_id, *, owner, lease_seconds, now=None):
            if claims:
                return None  # already held
            claims.append(owner)
            return task

        async def clear_lease(self, task_id, *, owner=None):
            cleared.append(owner or "")

    created: list[dict] = []
    failed: list[tuple[str, str]] = []

    class _Runs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id=f"run-{len(claims)}")

        async def mark_failed(self, run_id, *, error):
            failed.append((run_id, error))

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    spawned: list[dict] = []

    def fake_spawn(**kwargs):
        spawned.append(kwargs)

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "spawn_standing_task_run", fake_spawn)

    run1 = await runner_mod.dispatch_standing_task(
        task_id="task-1",
        user_id="u1",
        trigger_source="webhook",
    )
    assert run1 == "run-1"
    assert len(claims) == 1
    assert spawned[0]["lease_owner"] == claims[0]
    assert spawned[0]["run_id"] == "run-1"

    with pytest.raises(ConflictError, match="正在执行"):
        await runner_mod.dispatch_standing_task(
            task_id="task-1",
            user_id="u1",
            trigger_source="webhook",
        )
    assert len(spawned) == 1
    # STD-A4: the dropped event is visible in the inbox, not only a 409 to the caller.
    assert [c["status"] for c in created] == ["running", "failed"]
    assert created[1]["trigger_source"] == "webhook"
    assert created[1]["conversation_id"] == "conv-1"
    assert failed and "未自动补跑" in failed[0][1]


@pytest.mark.asyncio
async def test_manual_dispatch_conflict_writes_no_inbox_row(monkeypatch):
    """「立即跑一次」's caller is the user and sees the 409 — no inbox noise."""
    from agentcore.core.errors import ConflictError
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(id="task-1", conversation_id="conv-1", user_id="u1")
    created: list[dict] = []

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, *, user_id=None):
            return task

        async def claim_dispatch(self, task_id, *, owner, lease_seconds, now=None):
            return None  # already held

    class _Runs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="run-x")

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)

    with pytest.raises(ConflictError, match="正在执行"):
        await runner_mod.dispatch_standing_task(
            task_id="task-1",
            user_id="u1",
            trigger_source="manual",
        )
    assert created == []


@pytest.mark.asyncio
async def test_poll_records_inbox_row_when_dispatch_fails(monkeypatch):
    """STD-A5: the clock is advanced before dispatch, so a failure loses this
    period's fire — it must show up in the inbox, not only in a server log."""
    from agentcore.standing_tasks import scheduler as sched_mod

    task = SimpleNamespace(
        id="task-1", user_id="u1", cron="0 9 * * 1", conversation_id="conv-1"
    )
    advanced: list[str] = []
    cleared: list[str] = []
    created: list[dict] = []
    failed: list[tuple[str, str]] = []

    class _Tasks:
        def __init__(self, session):
            pass

        async def claim_due(self, *, now, owner, lease_seconds, limit):
            return [task]

        async def advance_next_run(self, task_id, *, next_run_at):
            advanced.append(task_id)

        async def clear_lease(self, task_id, *, owner=None):
            cleared.append(task_id)

    class _Runs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="run-missed")

        async def mark_failed(self, run_id, *, error):
            failed.append((run_id, error))

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def boom(**kwargs):
        raise RuntimeError("派单炸了")

    monkeypatch.setattr(sched_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(sched_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(sched_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(sched_mod, "dispatch_standing_task", boom)

    assert await sched_mod.poll_due_standing_tasks(owner="sched-test") == 0
    # Clock stays advanced and the lease is released — the miss is surfaced, not re-run.
    assert advanced == ["task-1"]
    assert cleared == ["task-1"]
    assert [c["status"] for c in created] == ["failed"]
    assert created[0]["trigger_source"] == "schedule"
    assert created[0]["user_id"] == "u1"
    assert failed == [("run-missed", sched_mod._DISPATCH_FAILED_ERROR.format(error="派单炸了"))]


@pytest.mark.asyncio
async def test_scheduled_fire_aborts_when_task_disabled_after_claim(monkeypatch):
    """STD-A5: the guard keys off ``trigger_source``. It used to hang off
    ``advance_schedule``, which the scheduler always passes as False."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(id="task-1", user_id="u1", enabled=False)
    run_marks: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)

    await runner_mod.run_standing_task_job(
        run_id="run-1",
        task_id="task-1",
        advance_schedule=False,
        trigger_source="schedule",
    )
    assert run_marks["failed"] == "站立任务已停用"


@pytest.mark.asyncio
async def test_manual_fire_still_runs_a_disabled_task(monkeypatch):
    """「立即跑一次」is the 验收 / 收件箱重跑 path — disabling must not block it."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="u1",
        folder_id="folder-1",
        enabled=False,
        goal="g",
        name="n",
        permission_axes={},
        cron="0 9 * * 1",
        conversation_id="conv-1",
    )
    run_marks: dict[str, object] = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            run_marks["failed"] = error

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return None  # stop the job right after the enabled guard

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)

    await runner_mod.run_standing_task_job(
        run_id="run-1",
        task_id="task-1",
        advance_schedule=False,
        trigger_source="manual",
    )
    assert run_marks["failed"] == "站立任务仅支持云工作区"


@pytest.mark.asyncio
async def test_dispatch_skips_claim_when_scheduler_owns_lease(monkeypatch):
    """Scheduler already claimed via claim_due — dispatch must not re-claim."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(id="task-1", conversation_id=None, user_id="u1")
    claim_calls = 0

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, *, user_id=None):
            return task

        async def claim_dispatch(self, *a, **k):
            nonlocal claim_calls
            claim_calls += 1
            return task

    class _Runs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="run-sched")

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    spawned: list[dict] = []
    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(
        runner_mod, "spawn_standing_task_run", lambda **k: spawned.append(k)
    )

    run_id = await runner_mod.dispatch_standing_task(
        task_id="task-1",
        user_id="u1",
        lease_owner="sched-abc",
        trigger_source="schedule",
        advance_schedule=True,
    )
    assert run_id == "run-sched"
    assert claim_calls == 0
    assert spawned[0]["lease_owner"] == "sched-abc"


@pytest.mark.asyncio
async def test_fire_webhook_conflict_when_dispatch_busy(monkeypatch):
    """In-flight lease → webhook returns ConflictError, does not open a second run."""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.core.errors import ConflictError
    from agentcore.standing_tasks import webhook as wh
    from agentcore.standing_tasks.webhook import generate_webhook_secret

    wh.reset_webhook_state()
    raw, hashed = generate_webhook_secret()
    webhook_task = SimpleNamespace(
        id="task-w",
        user_id="user-1",
        folder_id="folder-1",
        enabled=True,
        trigger_kind="webhook",
        webhook_id="wid-1",
        webhook_secret_hash=hashed,
    )

    async def busy_dispatch(**kwargs):
        raise ConflictError("站立任务正在执行中，请稍后再试")

    monkeypatch.setattr(routes, "dispatch_standing_task", busy_dispatch)

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return webhook_task if wid == "wid-1" else None

    class _Folders:
        async def get_by_id(self, *a, **k):
            return SimpleNamespace(id="folder-1", local_root_id=None)

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"text":"x"}'

    with pytest.raises(ConflictError, match="正在执行"):
        await routes.fire_standing_webhook(
            webhook_id="wid-1",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization=f"Bearer {raw}",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )
    wh.reset_webhook_state()


# ---------------------------------------------------------------------------
# GAP-2: awaiting_user inbox settlement after resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_after_turn_marks_succeeded(monkeypatch):
    from agentcore.standing_tasks import inbox as inbox_mod

    marks: dict[str, object] = {}

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return [SimpleNamespace(id="run-a", summary="old")]

        async def mark_succeeded(self, run_id, *, summary):
            marks["succeeded"] = summary

        async def mark_failed(self, run_id, *, error):
            marks["failed"] = error

        async def mark_awaiting_user(self, run_id, *, summary=None):
            marks["awaiting_user"] = summary

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, conversation_id):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(inbox_mod, "PausedTurnRepository", _Paused)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.END_TURN,
        content="拍板后完成摘要",
    )
    assert n == 1
    assert marks["succeeded"] == "拍板后完成摘要"
    assert "failed" not in marks
    assert "awaiting_user" not in marks


@pytest.mark.asyncio
async def test_settle_after_turn_keeps_awaiting_on_repause(monkeypatch):
    from agentcore.standing_tasks import inbox as inbox_mod

    marks: dict[str, object] = {}

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return [SimpleNamespace(id="run-a", summary="old")]

        async def mark_succeeded(self, run_id, *, summary):
            marks["succeeded"] = summary

        async def mark_failed(self, run_id, *, error):
            marks["failed"] = error

        async def mark_awaiting_user(self, run_id, *, summary=None):
            marks["awaiting_user"] = summary

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, conversation_id):
            return True

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(inbox_mod, "PausedTurnRepository", _Paused)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.PAUSED,
        content="仍需授权",
        message_id="turn-a",
    )
    assert n == 1
    assert marks["awaiting_user"] == "仍需授权"
    assert "succeeded" not in marks


@pytest.mark.asyncio
async def test_settle_after_turn_ignores_residual_other_pause(monkeypatch):
    """ST-1: residual pause on another turn must not keep awaiting after this turn ends."""
    from agentcore.standing_tasks import inbox as inbox_mod

    marks: dict[str, object] = {}
    probed: dict[str, str] = {}

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return [SimpleNamespace(id="run-a", summary="old")]

        async def mark_succeeded(self, run_id, *, summary):
            marks["succeeded"] = summary

        async def mark_failed(self, run_id, *, error):
            marks["failed"] = error

        async def mark_awaiting_user(self, run_id, *, summary=None):
            marks["awaiting_user"] = summary

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, conversation_id):
            return True

        async def exists_for_message(self, message_id):
            probed["message_id"] = message_id
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(inbox_mod, "PausedTurnRepository", _Paused)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.END_TURN,
        content="本 turn 已收口",
        message_id="turn-resumed",
    )
    assert n == 1
    assert probed.get("message_id") == "turn-resumed"
    assert marks["succeeded"] == "本 turn 已收口"
    assert "awaiting_user" not in marks


@pytest.mark.asyncio
async def test_settle_after_turn_keeps_awaiting_when_this_turn_still_paused(monkeypatch):
    """ST-1: this turn still paused in DB → keep awaiting_user."""
    from agentcore.standing_tasks import inbox as inbox_mod

    marks: dict[str, object] = {}

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return [SimpleNamespace(id="run-a", summary="old")]

        async def mark_succeeded(self, run_id, *, summary):
            marks["succeeded"] = summary

        async def mark_failed(self, run_id, *, error):
            marks["failed"] = error

        async def mark_awaiting_user(self, run_id, *, summary=None):
            marks["awaiting_user"] = summary

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_message(self, message_id):
            return message_id == "turn-repaused"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(inbox_mod, "PausedTurnRepository", _Paused)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.END_TURN,
        content="又卡住了",
        message_id="turn-repaused",
    )
    assert n == 1
    assert marks["awaiting_user"] == "又卡住了"
    assert "succeeded" not in marks


@pytest.mark.asyncio
async def test_settle_after_turn_marks_failed(monkeypatch):
    from agentcore.standing_tasks import inbox as inbox_mod

    marks: dict[str, object] = {}

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return [SimpleNamespace(id="run-a", summary=None)]

        async def mark_succeeded(self, run_id, *, summary):
            marks["succeeded"] = summary

        async def mark_failed(self, run_id, *, error):
            marks["failed"] = error

        async def mark_awaiting_user(self, run_id, *, summary=None):
            marks["awaiting_user"] = summary

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, conversation_id):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(inbox_mod, "PausedTurnRepository", _Paused)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.ERROR,
        error="模型失败",
    )
    assert n == 1
    assert marks["failed"] == "模型失败"


@pytest.mark.asyncio
async def test_settle_after_turn_noop_without_open_rows(monkeypatch):
    from agentcore.standing_tasks import inbox as inbox_mod

    class _Runs:
        def __init__(self, session):
            pass

        async def list_awaiting_for_conversation(self, conversation_id):
            return []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(inbox_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(inbox_mod, "StandingTaskRunRepository", _Runs)

    n = await inbox_mod.settle_after_turn(
        conversation_id="conv-1",
        finish_reason=FinishReason.END_TURN,
        content="x",
    )
    assert n == 0


# ---------------------------------------------------------------------------
# Release-audit non-blocking debt: N1 task_name · N2 folder_id PATCH ·
# N3 last_run_at on dispatch claim · N4 TriggerStandingTaskResponse ·
# N5 delete cascades inbox runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_fills_task_name(monkeypatch):
    """N1: list standing-task-runs joins task name onto each summary."""
    from agentcore.api.routes import standing_tasks as routes

    run = SimpleNamespace(
        id="run-1",
        standing_task_id="task-1",
        conversation_id=None,
        user_message_id=None,
        status="succeeded",
        trigger_source="manual",
        summary="ok",
        error=None,
        acked_at=None,
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        started_at=None,
        finished_at=None,
    )

    class _Runs:
        async def list_for_user(self, user_id, *, status=None, limit=50, unacked_only=False):
            assert user_id == "u1"
            return [(run, "周一简报")]

        async def count_badge(self, user_id):
            return 0

    user = SimpleNamespace(user_id="u1")
    out = await routes.list_standing_task_runs(
        user=user, status=None, unacked=False, limit=50, repo=_Runs()
    )
    assert out.badge == 0
    assert len(out.items) == 1
    assert out.items[0].task_name == "周一简报"
    assert out.items[0].id == "run-1"


@pytest.mark.asyncio
async def test_patch_folder_id_validates_cloud_workspace(monkeypatch):
    """N2: PATCH folder_id is accepted and gated like create."""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.api.schemas.standing_tasks import UpdateStandingTaskRequest

    existing = SimpleNamespace(
        id="task-1",
        user_id="u1",
        folder_id="fold-old",
        name="t",
        goal="g",
        trigger_kind="schedule",
        cron="0 9 * * 1",
        permission_axes={"file_write": "session", "command": "auto", "host": "session"},
        enabled=True,
        next_run_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        conversation_id=None,
        last_run_at=None,
        webhook_id=None,
        webhook_secret_hash=None,
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        updated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    updated = SimpleNamespace(**{**existing.__dict__, "folder_id": "fold-new"})
    captured: dict = {}

    class _Repo:
        async def get_by_id(self, task_id, *, user_id=None):
            return existing if task_id == "task-1" else None

        async def update(self, task_id, *, user_id, **fields):
            captured.update(fields)
            return updated

    class _Folders:
        async def get_by_id(self, folder_id, *, user_id=None):
            if folder_id == "fold-local":
                return SimpleNamespace(id=folder_id, local_root_id="root-1")
            if folder_id == "fold-new":
                return SimpleNamespace(id=folder_id, local_root_id=None)
            return None

    user = SimpleNamespace(user_id="u1")
    body = UpdateStandingTaskRequest(folder_id="fold-new")
    out = await routes.update_standing_task(
        task_id="task-1",
        body=body,
        user=user,
        repo=_Repo(),
        folders=_Folders(),
    )
    assert captured["folder_id"] == "fold-new"
    assert out.folder_id == "fold-new"

    with pytest.raises(ValidationError, match="云工作区"):
        await routes.update_standing_task(
            task_id="task-1",
            body=UpdateStandingTaskRequest(folder_id="fold-local"),
            user=user,
            repo=_Repo(),
            folders=_Folders(),
        )


def test_claim_dispatch_sets_last_run_at():
    """N3: webhook/manual claim_dispatch writes last_run_at (reuse column)."""
    import inspect

    from agentcore.db.repositories.standing_tasks import StandingTaskRepository

    src = inspect.getsource(StandingTaskRepository.claim_dispatch)
    assert "last_run_at" in src


@pytest.mark.asyncio
async def test_trigger_standing_task_returns_run_id(monkeypatch):
    """N4: POST …/run returns TriggerStandingTaskResponse.run_id."""
    from agentcore.api.routes import standing_tasks as routes

    task = SimpleNamespace(
        id="task-1",
        user_id="u1",
        folder_id="fold-1",
        trigger_kind="schedule",
    )

    class _Repo:
        async def get_by_id(self, task_id, *, user_id=None):
            return task

    class _Folders:
        async def get_by_id(self, folder_id, *, user_id=None):
            return SimpleNamespace(id=folder_id, local_root_id=None)

    async def fake_dispatch(**kwargs):
        assert kwargs["trigger_source"] == "manual"
        assert kwargs["advance_schedule"] is False
        return "run-abc"

    monkeypatch.setattr(routes, "dispatch_standing_task", fake_dispatch)
    out = await routes.trigger_standing_task(
        task_id="task-1",
        user=SimpleNamespace(user_id="u1"),
        repo=_Repo(),
        folders=_Folders(),
    )
    assert out.run_id == "run-abc"


@pytest.mark.asyncio
async def test_delete_standing_task_cascades_runs():
    """N5: deleting a task removes standing_task_runs (no orphan inbox rows)."""
    from agentcore.db.repositories.standing_tasks import StandingTaskRepository

    executed: list[object] = []
    deleted_rows: list[object] = []

    class _Session:
        async def execute(self, stmt):
            executed.append(stmt)
            return MagicMock()

        async def delete(self, row):
            deleted_rows.append(row)

        async def commit(self):
            return None

    repo = StandingTaskRepository(_Session())  # type: ignore[arg-type]
    task = SimpleNamespace(id="task-1", user_id="u1")

    async def fake_get(task_id, *, user_id=None):
        return task if task_id == "task-1" and user_id == "u1" else None

    repo.get_by_id = fake_get  # type: ignore[method-assign]
    assert await repo.delete("task-1", user_id="u1") is True
    assert len(executed) == 1
    assert deleted_rows == [task]
    assert await repo.delete("missing", user_id="u1") is False
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_run_job_without_workflow_uses_ceo_pipeline(monkeypatch):
    """Unbound standing fire still calls ``_run_pipeline`` (CEO path)."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="普通目标",
        name="普通",
        permission_axes={},
        cron="0 9 * * *",
        enabled=True,
        conversation_id="conv-1",
        trigger_kind="schedule",
        template_key=None,
        template_config={},
        workflow_id=None,
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    called: dict[str, int] = {"ceo": 0, "wf": 0}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            called["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            called["ok"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            return None

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="x")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_ceo(**kwargs):
        called["ceo"] += 1
        return {"finish_reason": FinishReason.END_TURN, "content": "ceo"}

    async def fake_wf(**kwargs):
        called["wf"] += 1
        return {"finish_reason": FinishReason.END_TURN, "content": "wf"}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_ceo)
    monkeypatch.setattr(runner_mod, "_run_workflow_pipeline", fake_wf)

    await runner_mod.run_standing_task_job(run_id="run-1", task_id="task-1", advance_schedule=False)
    assert called["ceo"] == 1
    assert called["wf"] == 0
    assert called.get("ok") == "ceo"


@pytest.mark.asyncio
async def test_run_job_with_workflow_uses_direct_start(monkeypatch):
    """Bound workflow fire calls ``_run_workflow_pipeline`` (direct-start)."""
    from agentcore.standing_tasks import runner as runner_mod

    task = SimpleNamespace(
        id="task-1",
        user_id="user-1",
        folder_id="folder-1",
        goal="本轮补充",
        name="绑工作流",
        permission_axes={},
        cron="0 9 * * *",
        enabled=True,
        conversation_id="conv-1",
        trigger_kind="schedule",
        template_key=None,
        template_config={},
        workflow_id="wf-1",
    )
    folder = SimpleNamespace(id="folder-1", local_root_id=None)
    called: dict[str, object] = {"ceo": 0, "wf": 0, "wf_kwargs": None}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            called["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            called["ok"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            return None

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return folder

    class _Convs:
        def __init__(self, session):
            pass

        async def get_by_id_unscoped(self, cid):
            return SimpleNamespace(id=cid, folder_id="folder-1", title="x")

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            called["user_message"] = kwargs.get("content")
            return SimpleNamespace(id="msg")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_conversation(self, cid):
            return False

    class _WfRepo:
        def __init__(self, session):
            pass

        async def get_by_id(self, workflow_id, *, user_id=None):
            return SimpleNamespace(
                id="wf-1",
                name="三步质检",
                version=2,
                definition={
                    "nodes": [
                        {
                            "id": "s1",
                            "kind": "agent_step",
                            "role": "质检",
                            "task": "查一查",
                        }
                    ],
                    "edges": [],
                },
            )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", _Convs)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(
        "agentcore.db.repositories.user_workflows.UserWorkflowRepository",
        _WfRepo,
    )
    # Also patch the late import site used inside the job.
    import agentcore.db.repositories.user_workflows as uw_mod

    monkeypatch.setattr(uw_mod, "UserWorkflowRepository", _WfRepo)
    monkeypatch.setattr(
        runner_mod,
        "resolve_conversation_model_selection",
        AsyncMock(return_value=SimpleNamespace(origin="byok", provider_id=None, model="m")),
    )
    monkeypatch.setattr(runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_profile_set", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "resolve_permission_axes", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "build_turn_backend", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))

    async def fake_ceo(**kwargs):
        called["ceo"] = int(called["ceo"]) + 1
        return {"finish_reason": FinishReason.END_TURN, "content": "ceo"}

    async def fake_wf(**kwargs):
        called["wf"] = int(called["wf"]) + 1
        called["wf_kwargs"] = kwargs
        return {"finish_reason": FinishReason.END_TURN, "content": "按图跑完"}

    monkeypatch.setattr(runner_mod, "_run_pipeline", fake_ceo)
    monkeypatch.setattr(runner_mod, "_run_workflow_pipeline", fake_wf)

    await runner_mod.run_standing_task_job(run_id="run-1", task_id="task-1", advance_schedule=False)
    assert called["ceo"] == 0
    assert called["wf"] == 1
    assert called.get("ok") == "按图跑完"
    assert "三步质检" in str(called.get("user_message") or "")
    assert "本轮补充" in str(called.get("user_message") or "")
    wf_kwargs = called["wf_kwargs"]
    assert isinstance(wf_kwargs, dict)
    assert wf_kwargs["workflow_id"] == "wf-1"
    assert wf_kwargs["workflow_version"] == 2


# --- 编辑已跑过的任务：授权轴 / 所属项目要真的到达下一次代跑 -----------------------
#
# 这几条覆盖「任务表 ↔ 钉对话」这条接缝，因此**不 monkeypatch**
# ``resolve_permission_axes``（上面的老用例把它打成 None，等于把接缝屏蔽掉）。


class _FakeConversationRepo:
    """Minimal ConversationRepository stand-in over a shared in-memory row map."""

    def __init__(self, store: _FakeConversationStore) -> None:
        self._store = store

    async def get_by_id(self, conversation_id, *, user_id=None):
        return self._store.rows.get(conversation_id)

    async def get_by_id_unscoped(self, conversation_id):
        return self._store.rows.get(conversation_id)

    async def set_permission_axes(
        self, conversation_id, *, user_id, permission_axes, commit=True
    ):
        conv = self._store.rows.get(conversation_id)
        if conv is None:
            return None
        conv.permission_axes = dict(permission_axes)
        self._store.axes_writes.append((conversation_id, dict(permission_axes), commit))
        return conv

    async def create(self, **kwargs):
        conv = SimpleNamespace(
            id=f"conv-{len(self._store.rows) + 1}",
            title=kwargs.get("title"),
            folder_id=kwargs.get("folder_id"),
            mode=kwargs.get("mode"),
            permission_axes=dict(kwargs.get("permission_axes") or {}),
            model_profile_id=None,
        )
        self._store.rows[conv.id] = conv
        self._store.created.append(dict(kwargs))
        return conv


class _FakeConversationStore:
    """One conversation map shared by the PATCH route and the fire (same seam)."""

    def __init__(self, rows: dict[str, SimpleNamespace] | None = None) -> None:
        self.rows: dict[str, SimpleNamespace] = dict(rows or {})
        self.created: list[dict] = []
        self.axes_writes: list[tuple[str, dict, bool]] = []

    def repo(self, _session: object = None) -> _FakeConversationRepo:
        return _FakeConversationRepo(self)


def _standing_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id="task-1",
        user_id="u1",
        folder_id="fold-a",
        name="每周巡检",
        goal="巡检竞品",
        trigger_kind="schedule",
        cron="0 9 * * 1",
        permission_axes={
            "file_write": "session",
            "command": "auto",
            "host": "session",
        },
        enabled=True,
        next_run_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
        conversation_id=None,
        last_run_at=None,
        webhook_id=None,
        webhook_secret_hash=None,
        template_key=None,
        template_config={},
        workflow_id=None,
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
        updated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _standing_conversation(conv_id: str, task: SimpleNamespace) -> SimpleNamespace:
    """The pinned thread as the first fire left it."""
    return SimpleNamespace(
        id=conv_id,
        title=task.name,
        folder_id=task.folder_id,
        mode="standing",
        permission_axes=dict(task.permission_axes),
        model_profile_id=None,
    )


def _patch_task_route(monkeypatch, task: SimpleNamespace):
    """Repo/folder doubles for the PATCH route + a silenced permission audit."""
    from agentcore.runtime.audit import permission_events

    audited: list[dict] = []

    class _Repo:
        async def get_by_id(self, task_id, *, user_id=None):
            return task if task_id == task.id else None

        async def update(self, task_id, *, user_id, **fields):
            if task_id != task.id:
                return None
            for key, value in fields.items():
                setattr(task, key, value)
            return task

    class _Folders:
        async def get_by_id(self, folder_id, *, user_id=None):
            return SimpleNamespace(id=folder_id, name=folder_id, local_root_id=None)

    async def _record(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(permission_events, "record_permission_axes_change", _record)
    return _Repo(), _Folders(), audited


def _patch_fire(monkeypatch, *, task: SimpleNamespace, store: _FakeConversationStore) -> dict:
    """Wire one standing fire against in-memory rows; returns a capture dict.

    Leaves ``resolve_permission_axes`` real so the axes actually travel task →
    pinned conversation → pipeline.
    """
    import agentcore.db.repositories as repositories
    from agentcore.standing_tasks import runner as runner_mod

    captured: dict = {}

    class _Tasks:
        def __init__(self, session):
            pass

        async def get_by_id(self, task_id, user_id=None):
            return task if task_id == task.id else None

        async def attach_conversation(self, task_id, *, conversation_id, commit=True):
            task.conversation_id = conversation_id
            return task

        async def clear_lease(self, *a, **k):
            return None

        async def advance_next_run(self, *a, **k):
            return None

    class _Runs:
        def __init__(self, session):
            pass

        async def mark_failed(self, run_id, *, error):
            captured["failed"] = error

        async def mark_succeeded(self, run_id, *, summary):
            captured["succeeded"] = summary

        async def mark_awaiting_user(self, run_id, *, summary=None):
            captured["awaiting_user"] = summary

        async def set_conversation_and_message(self, *a, **k):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return SimpleNamespace(id=folder_id, name=folder_id, local_root_id=None)

    class _Msgs:
        def __init__(self, session):
            pass

        async def create(self, **kwargs):
            return SimpleNamespace(id="msg-u1")

        async def list_recent(self, *a, **k):
            return []

    class _Users:
        def __init__(self, session):
            pass

        async def get_by_id(self, uid):
            return SimpleNamespace(user_id=uid)

    class _Paused:
        def __init__(self, session):
            pass

        async def exists_for_message(self, message_id):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

    async def _model_selection(session, conv, user_id):
        captured["model_conv"] = conv
        return SimpleNamespace(origin="byok", provider_id=None, model="m")

    async def _profiles(session, conv, user_id):
        captured["profile_conv"] = conv
        return None

    async def _backend(**kwargs):
        captured["backend_folder_id"] = kwargs.get("folder_id")
        return MagicMock()

    async def _pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return {"finish_reason": FinishReason.END_TURN, "content": "跑完"}

    monkeypatch.setattr(runner_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(runner_mod, "StandingTaskRepository", _Tasks)
    monkeypatch.setattr(runner_mod, "StandingTaskRunRepository", _Runs)
    monkeypatch.setattr(runner_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(runner_mod, "ConversationRepository", store.repo)
    # resolve_permission_axes reaches the row through this late import.
    monkeypatch.setattr(repositories, "ConversationRepository", store.repo)
    monkeypatch.setattr(runner_mod, "MessageRepository", _Msgs)
    monkeypatch.setattr(runner_mod, "PausedTurnRepository", _Paused)
    monkeypatch.setattr(runner_mod, "UserRepository", _Users)
    monkeypatch.setattr(runner_mod, "resolve_conversation_model_selection", _model_selection)
    monkeypatch.setattr(runner_mod, "resolve_profile_set", _profiles)
    monkeypatch.setattr(
        runner_mod, "preflight_resolved_llm_credentials", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(runner_mod, "build_turn_backend", _backend)
    monkeypatch.setattr(runner_mod, "load_chat_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod, "_run_pipeline", _pipeline)
    return captured


@pytest.mark.asyncio
async def test_axes_edit_after_first_fire_reaches_next_run(monkeypatch):
    """收紧自主度后，下一次代跑按新轴开跑（不是首跑那次冻结的轴）。"""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.api.schemas.conversations import PermissionAxesModel
    from agentcore.api.schemas.standing_tasks import UpdateStandingTaskRequest
    from agentcore.standing_tasks import runner as runner_mod

    task = _standing_row(conversation_id="conv-1")
    store = _FakeConversationStore({"conv-1": _standing_conversation("conv-1", task)})
    repo, folders, audited = _patch_task_route(monkeypatch, task)

    out = await routes.update_standing_task(
        task_id="task-1",
        body=UpdateStandingTaskRequest(
            permission_axes=PermissionAxesModel(
                file_write="ask", command="ask", host="off"
            )
        ),
        user=SimpleNamespace(user_id="u1"),
        repo=repo,
        folders=folders,
        conversations=store.repo(),
    )
    assert out.permission_axes.file_write.value == "ask"
    # 授权面的运行时真相在钉对话上，且与任务行同一事务落库。
    assert store.rows["conv-1"].permission_axes["file_write"] == "ask"
    assert store.axes_writes == [
        (
            "conv-1",
            {"file_write": "ask", "command": "ask", "host": "off"},
            False,
        )
    ]
    assert [a["conversation_id"] for a in audited] == ["conv-1"]
    # 只改轴不换项目 → 线程保留。
    assert task.conversation_id == "conv-1"

    captured = _patch_fire(monkeypatch, task=task, store=store)
    await runner_mod.run_standing_task_job(
        run_id="run-2", task_id="task-1", advance_schedule=False
    )
    axes = captured["pipeline"]["permission_axes"]
    assert axes.file_write.value == "ask"
    assert axes.command.value == "ask"
    assert captured.get("succeeded") == "跑完"


@pytest.mark.asyncio
async def test_folder_edit_after_first_fire_rethreads_into_new_project(monkeypatch):
    """换项目：钉对话解绑，下一次代跑在新项目开线程，模型档案 / 项目档案随之走新项目。"""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.api.schemas.standing_tasks import UpdateStandingTaskRequest
    from agentcore.standing_tasks import runner as runner_mod

    task = _standing_row(conversation_id="conv-1")
    store = _FakeConversationStore({"conv-1": _standing_conversation("conv-1", task)})
    repo, folders, _audited = _patch_task_route(monkeypatch, task)

    out = await routes.update_standing_task(
        task_id="task-1",
        body=UpdateStandingTaskRequest(folder_id="fold-b"),
        user=SimpleNamespace(user_id="u1"),
        repo=repo,
        folders=folders,
        conversations=store.repo(),
    )
    assert out.folder_id == "fold-b"
    # 对话出生项目终身不可变 → 解绑重开，而不是把旧线程拖过去。
    assert task.conversation_id is None
    assert store.rows["conv-1"].folder_id == "fold-a"

    captured = _patch_fire(monkeypatch, task=task, store=store)
    await runner_mod.run_standing_task_job(
        run_id="run-2", task_id="task-1", advance_schedule=False
    )
    assert [c["folder_id"] for c in store.created] == ["fold-b"]
    assert task.conversation_id == "conv-2"
    assert captured["model_conv"].id == "conv-2"
    assert captured["model_conv"].folder_id == "fold-b"
    assert captured["profile_conv"].folder_id == "fold-b"
    assert captured["backend_folder_id"] == "fold-b"
    assert captured["pipeline"]["folder_id"] == "fold-b"


@pytest.mark.asyncio
async def test_patch_leaves_pinned_thread_alone_without_a_real_change(monkeypatch):
    """未钉对话 / 项目没变：不写对话、不解绑（只有真实变更才动线程）。"""
    from agentcore.api.routes import standing_tasks as routes
    from agentcore.api.schemas.conversations import PermissionAxesModel
    from agentcore.api.schemas.standing_tasks import UpdateStandingTaskRequest

    unpinned = _standing_row()
    store = _FakeConversationStore()
    repo, folders, audited = _patch_task_route(monkeypatch, unpinned)
    await routes.update_standing_task(
        task_id="task-1",
        body=UpdateStandingTaskRequest(
            permission_axes=PermissionAxesModel(file_write="ask", command="ask")
        ),
        user=SimpleNamespace(user_id="u1"),
        repo=repo,
        folders=folders,
        conversations=store.repo(),
    )
    assert store.axes_writes == []
    assert audited == []
    assert unpinned.permission_axes["file_write"] == "ask"

    pinned = _standing_row(conversation_id="conv-1")
    store2 = _FakeConversationStore({"conv-1": _standing_conversation("conv-1", pinned)})
    repo2, folders2, _ = _patch_task_route(monkeypatch, pinned)
    await routes.update_standing_task(
        task_id="task-1",
        body=UpdateStandingTaskRequest(folder_id="fold-a", enabled=False),
        user=SimpleNamespace(user_id="u1"),
        repo=repo2,
        folders=folders2,
        conversations=store2.repo(),
    )
    assert pinned.conversation_id == "conv-1"
    assert store2.axes_writes == []
