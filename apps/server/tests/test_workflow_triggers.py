"""Workflow clock: cron, lease, PUT XOR, poll miss, webhook, retired standing routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydValidationError

from agentcore.api.routes.workflows import _require_cloud_folder
from agentcore.api.schemas.workflows import PutWorkflowTriggerRequest
from agentcore.core.errors import ConflictError, NotFoundError, ValidationError
from agentcore.db.repositories.user_workflows import is_lease_free, is_trigger_claimable
from agentcore.workflows.schedule import (
    CRON_PRESETS,
    CronError,
    infer_schedule_preset,
    next_run_after,
    resolve_cron,
    validate_cron,
)

_USER = SimpleNamespace(user_id="u1")
_NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _wf(**overrides) -> SimpleNamespace:
    base = dict(
        id="wf-1",
        user_id="u1",
        name="周报",
        description=None,
        definition={"nodes": [], "edges": []},
        source=None,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
        trigger_kind=None,
        trigger_enabled=False,
        trigger_folder_id=None,
        trigger_cron=None,
        trigger_webhook_id=None,
        trigger_webhook_secret_hash=None,
        trigger_next_run_at=None,
        trigger_last_run_at=None,
        last_trigger_error=None,
        trigger_lease_owner=None,
        trigger_lease_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class TestCronNextRun:
    def test_weekly_monday_advances_to_next_monday(self):
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
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 1 * 1", after) == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        after = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 1 * 1", after) == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    def test_star_day_field_keeps_and_semantics(self):
        after = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
        assert next_run_after("0 9 * * 1", after) == datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        assert next_run_after("0 9 1 * *", after) == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    def test_step_dom_counts_as_star_for_the_day_rule(self):
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
        assert infer_schedule_preset("0 9 * * 1") == "weekly_mon"
        assert infer_schedule_preset("15 3 * * *") == "custom"
        with pytest.raises(CronError):
            resolve_cron(schedule_preset="weekly")
        with pytest.raises(CronError):
            resolve_cron(schedule_preset="monthly")


class TestLeaseClaimable:
    def test_due_and_unlocked_is_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_trigger_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
        )

    def test_active_lease_blocks_second_claim(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_trigger_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=now + timedelta(minutes=10),
            now=now,
        )
        assert not is_lease_free(
            lease_until=now + timedelta(minutes=10),
            now=now,
        )

    def test_expired_lease_allows_reclaim(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert is_trigger_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=now - timedelta(seconds=1),
            now=now,
        )

    def test_disabled_skipped(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_trigger_claimable(
            enabled=False,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
        )

    def test_future_next_run_skipped(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_trigger_claimable(
            enabled=True,
            next_run_at=now + timedelta(minutes=5),
            lease_until=None,
            now=now,
        )

    def test_webhook_trigger_never_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_trigger_claimable(
            enabled=True,
            next_run_at=now - timedelta(minutes=1),
            lease_until=None,
            now=now,
            trigger_kind="webhook",
        )

    def test_null_next_run_not_claimable(self):
        now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
        assert not is_trigger_claimable(
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
        _require_cloud_folder(folder)


class TestPutTriggerXor:
    def test_webhook_rejects_schedule_fields(self):
        with pytest.raises(PydValidationError):
            PutWorkflowTriggerRequest(
                kind="webhook",
                folder_id="f1",
                schedule_preset="daily",
            )
        with pytest.raises(PydValidationError):
            PutWorkflowTriggerRequest(
                kind="webhook",
                folder_id="f1",
                cron="0 9 * * *",
            )

    def test_schedule_requires_preset_or_cron(self):
        with pytest.raises(PydValidationError):
            PutWorkflowTriggerRequest(kind="schedule", folder_id="f1")

    def test_webhook_ok_without_schedule(self):
        body = PutWorkflowTriggerRequest(kind="webhook", folder_id="f1")
        assert body.kind == "webhook"


class _Folders:
    def __init__(self, *, local: bool = False):
        self.local = local

    async def get_by_id(self, folder_id, user_id=None):
        if folder_id == "missing":
            return None
        return SimpleNamespace(
            id=folder_id,
            local_root_id="desk-1" if self.local else None,
        )


class _TriggerRepo:
    def __init__(self, row: SimpleNamespace):
        self.row = row
        self.replaced: dict = {}

    async def get_by_id(self, workflow_id, user_id=None):
        if workflow_id != self.row.id or user_id not in (None, self.row.user_id):
            return None
        return self.row

    async def replace_trigger(self, workflow_id, *, user_id, **fields):
        self.replaced = dict(fields)
        for key, value in fields.items():
            setattr(self.row, key, value)
        return self.row

    async def clear_trigger(self, workflow_id, *, user_id):
        return await self.replace_trigger(
            workflow_id,
            user_id=user_id,
            trigger_kind=None,
            trigger_enabled=False,
            trigger_folder_id=None,
            trigger_cron=None,
            trigger_webhook_id=None,
            trigger_webhook_secret_hash=None,
            trigger_next_run_at=None,
            trigger_last_run_at=None,
            last_trigger_error=None,
            trigger_lease_owner=None,
            trigger_lease_until=None,
        )


@pytest.mark.asyncio
async def test_put_schedule_trigger_sets_cron_and_does_not_bump_version():
    from agentcore.api.routes.workflows import put_workflow_trigger

    repo = _TriggerRepo(_wf(version=4))
    out = await put_workflow_trigger(
        workflow_id="wf-1",
        body=PutWorkflowTriggerRequest(
            kind="schedule", folder_id="f1", schedule_preset="daily"
        ),
        user=_USER,
        folders=_Folders(),
        repo=repo,
    )
    assert out.version == 4
    assert out.trigger is not None
    assert out.trigger.kind == "schedule"
    assert out.trigger.cron == CRON_PRESETS["daily"]
    assert out.trigger.webhook_secret is None
    assert repo.replaced["trigger_webhook_id"] is None


@pytest.mark.asyncio
async def test_put_trigger_rejects_local_folder():
    from agentcore.api.routes.workflows import put_workflow_trigger

    with pytest.raises(ValidationError, match="云工作区"):
        await put_workflow_trigger(
            workflow_id="wf-1",
            body=PutWorkflowTriggerRequest(
                kind="schedule", folder_id="f1", schedule_preset="daily"
            ),
            user=_USER,
            folders=_Folders(local=True),
            repo=_TriggerRepo(_wf()),
        )


@pytest.mark.asyncio
async def test_put_webhook_mints_secret_once_then_keeps_id():
    from agentcore.api.routes.workflows import put_workflow_trigger

    repo = _TriggerRepo(_wf())
    first = await put_workflow_trigger(
        workflow_id="wf-1",
        body=PutWorkflowTriggerRequest(kind="webhook", folder_id="f1"),
        user=_USER,
        folders=_Folders(),
        repo=repo,
    )
    assert first.trigger is not None
    assert first.trigger.kind == "webhook"
    assert first.trigger.webhook_secret
    assert first.trigger.webhook_id
    wid = first.trigger.webhook_id
    hashed = repo.row.trigger_webhook_secret_hash

    second = await put_workflow_trigger(
        workflow_id="wf-1",
        body=PutWorkflowTriggerRequest(kind="webhook", folder_id="f1", enabled=False),
        user=_USER,
        folders=_Folders(),
        repo=repo,
    )
    assert second.trigger is not None
    assert second.trigger.webhook_secret is None
    assert second.trigger.webhook_id == wid
    assert repo.row.trigger_webhook_secret_hash == hashed
    assert second.trigger.enabled is False


@pytest.mark.asyncio
async def test_delete_trigger_clears_clock():
    from agentcore.api.routes.workflows import delete_workflow_trigger

    repo = _TriggerRepo(
        _wf(
            trigger_kind="schedule",
            trigger_enabled=True,
            trigger_folder_id="f1",
            trigger_cron="0 9 * * *",
        )
    )
    out = await delete_workflow_trigger(workflow_id="wf-1", user=_USER, repo=repo)
    assert out.trigger is None
    assert repo.row.trigger_kind is None
    assert repo.row.trigger_enabled is False


@pytest.mark.asyncio
async def test_rotate_secret_webhook_only():
    from agentcore.api.routes.workflows import rotate_workflow_trigger_secret
    from agentcore.workflows.webhook import generate_webhook_secret

    raw, hashed = generate_webhook_secret()
    repo = _TriggerRepo(
        _wf(
            trigger_kind="webhook",
            trigger_enabled=True,
            trigger_folder_id="f1",
            trigger_webhook_id="wid-1",
            trigger_webhook_secret_hash=hashed,
        )
    )
    out = await rotate_workflow_trigger_secret(workflow_id="wf-1", user=_USER, repo=repo)
    assert out.webhook_id == "wid-1"
    assert out.webhook_secret
    assert out.webhook_secret != raw
    assert repo.row.trigger_webhook_secret_hash != hashed

    sched = _TriggerRepo(
        _wf(
            trigger_kind="schedule",
            trigger_enabled=True,
            trigger_folder_id="f1",
            trigger_cron="0 9 * * *",
        )
    )
    with pytest.raises(ValidationError, match="Webhook"):
        await rotate_workflow_trigger_secret(workflow_id="wf-1", user=_USER, repo=sched)


class TestWebhookHelpers:
    def test_extract_text_from_json_text_field(self):
        from agentcore.workflows.webhook import extract_event_text

        body = '{"text": "新线索 A", "extra": 1}'.encode()
        assert extract_event_text(body, content_type="application/json") == "新线索 A"

    def test_extract_message_field(self):
        from agentcore.workflows.webhook import extract_event_text

        body = b'{"message": "hello from zapier"}'
        assert extract_event_text(body, content_type="application/json") == "hello from zapier"

    def test_extract_falls_back_to_raw_body(self):
        from agentcore.workflows.webhook import extract_event_text

        assert extract_event_text(b"plain event", content_type="text/plain") == "plain event"

    def test_secret_roundtrip(self):
        from agentcore.workflows.webhook import (
            generate_webhook_secret,
            verify_webhook_secret,
        )

        raw, hashed = generate_webhook_secret()
        assert verify_webhook_secret(raw, hashed)
        assert not verify_webhook_secret("wrong", hashed)

    def test_require_secret_bearer_and_header(self):
        from agentcore.core.errors import AuthenticationError
        from agentcore.workflows.webhook import (
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

    def test_idempotency_same_key_returns_same_conversation(self):
        from agentcore.workflows import webhook as wh

        wh.reset_webhook_state()
        assert wh.idempotency_lookup("wid-1", "k1") is None
        wh.idempotency_store("wid-1", "k1", "conv-aaa")
        assert wh.idempotency_lookup("wid-1", "k1") == "conv-aaa"
        assert wh.idempotency_lookup("wid-1", "k2") is None
        wh.reset_webhook_state()

    def test_rate_limit_trips(self, monkeypatch):
        from agentcore.core.errors import RateLimitedError
        from agentcore.core.rate_limit import SlidingWindowRateLimiter
        from agentcore.workflows import webhook as wh

        wh.reset_webhook_state()
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        monkeypatch.setattr(wh, "_webhook_rate_limiter", limiter)
        monkeypatch.setattr(wh.settings, "rate_limit_enabled", True)
        monkeypatch.setattr(wh.settings, "workflow_trigger_webhook_rate_limit_max", 2)
        wh.enforce_webhook_rate_limit("wf-rl", now=1000.0)
        wh.enforce_webhook_rate_limit("wf-rl", now=1001.0)
        with pytest.raises(RateLimitedError):
            wh.enforce_webhook_rate_limit("wf-rl", now=1002.0)
        wh.reset_webhook_state()


@pytest.mark.asyncio
async def test_fire_dispatches_new_workflow_conversation(monkeypatch):
    from agentcore.workflows import trigger as trigger_mod

    captured: dict = {}

    async def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return "conv-new"

    class _Repo:
        def __init__(self, session):
            pass

        async def get_by_id(self, workflow_id, user_id=None):
            return _wf(
                trigger_kind="schedule",
                trigger_enabled=True,
                trigger_folder_id="f1",
                trigger_cron="0 9 * * *",
                name="周报",
                version=3,
            )

        async def claim_trigger_dispatch(self, workflow_id, *, owner, lease_seconds, now=None):
            return _wf(id=workflow_id)

        async def set_last_trigger_error(self, workflow_id, *, error):
            captured["cleared_error"] = error

        async def clear_trigger_lease(self, workflow_id, *, owner=None):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return SimpleNamespace(id=folder_id, local_root_id=None)

    monkeypatch.setattr(trigger_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(trigger_mod, "UserWorkflowRepository", _Repo)
    monkeypatch.setattr(trigger_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(trigger_mod, "dispatch_workflow_run", fake_dispatch)

    conv_id = await trigger_mod.fire_workflow_trigger(
        workflow_id="wf-1", user_id="u1", note="补充"
    )
    assert conv_id == "conv-new"
    assert captured["conversation_id"] is None
    assert captured["permission_axes"] is None
    assert captured["slot_values"] is None
    assert captured["note"] == "补充"
    assert captured["trigger_lease_owner"]
    assert captured["cleared_error"] is None


@pytest.mark.asyncio
async def test_fire_busy_writes_last_trigger_error(monkeypatch):
    from agentcore.workflows import trigger as trigger_mod

    errors: list[str | None] = []

    class _Repo:
        def __init__(self, session):
            pass

        async def get_by_id(self, workflow_id, user_id=None):
            return _wf(
                trigger_kind="webhook",
                trigger_enabled=True,
                trigger_folder_id="f1",
            )

        async def claim_trigger_dispatch(self, workflow_id, *, owner, lease_seconds, now=None):
            return None

        async def set_last_trigger_error(self, workflow_id, *, error):
            errors.append(error)

        async def clear_trigger_lease(self, workflow_id, *, owner=None):
            return None

    class _Folders:
        def __init__(self, session):
            pass

        async def get_by_id(self, folder_id, user_id=None):
            return SimpleNamespace(id=folder_id, local_root_id=None)

    monkeypatch.setattr(trigger_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(trigger_mod, "UserWorkflowRepository", _Repo)
    monkeypatch.setattr(trigger_mod, "FolderRepository", _Folders)
    monkeypatch.setattr(trigger_mod, "dispatch_workflow_run", AsyncMock())

    with pytest.raises(ConflictError, match="正在执行"):
        await trigger_mod.fire_workflow_trigger(workflow_id="wf-1", user_id="u1")
    assert errors
    assert "未自动补跑" in (errors[0] or "")
    trigger_mod.dispatch_workflow_run.assert_not_called()


@pytest.mark.asyncio
async def test_poll_dispatch_failure_writes_last_trigger_error(monkeypatch):
    from agentcore.workflows import scheduler as sched_mod

    advanced: list[str] = []
    cleared: list[str] = []
    errors: list[str | None] = []
    row = _wf(
        trigger_kind="schedule",
        trigger_enabled=True,
        trigger_folder_id="f1",
        trigger_cron="0 9 * * 1",
    )

    class _Repo:
        def __init__(self, session):
            pass

        async def claim_due_triggers(self, *, now, owner, lease_seconds, limit):
            return [row]

        async def advance_trigger_next_run(self, workflow_id, *, next_run_at):
            advanced.append(workflow_id)

        async def clear_trigger_lease(self, workflow_id, *, owner=None):
            cleared.append(workflow_id)

        async def set_last_trigger_error(self, workflow_id, *, error):
            errors.append(error)

    async def boom(**kwargs):
        raise RuntimeError("派单炸了")

    monkeypatch.setattr(sched_mod, "async_session_factory", lambda: _Session())
    monkeypatch.setattr(sched_mod, "UserWorkflowRepository", _Repo)
    monkeypatch.setattr(sched_mod, "fire_workflow_trigger", boom)

    assert await sched_mod.poll_due_workflow_triggers(owner="sched-test") == 0
    assert advanced == ["wf-1"]
    assert cleared == ["wf-1"]
    assert errors
    assert "未自动补跑" in (errors[0] or "")
    assert "派单炸了" in (errors[0] or "")


@pytest.mark.asyncio
async def test_fire_webhook_auth_failure():
    from agentcore.api.routes import workflows as routes
    from agentcore.core.errors import AuthenticationError
    from agentcore.workflows.webhook import generate_webhook_secret

    raw, hashed = generate_webhook_secret()
    row = _wf(
        trigger_kind="webhook",
        trigger_enabled=True,
        trigger_folder_id="f1",
        trigger_webhook_id="wid-1",
        trigger_webhook_secret_hash=hashed,
    )

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return row if wid == "wid-1" else None

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"text":"x"}'

    with pytest.raises(NotFoundError):
        await routes.fire_workflow_webhook(
            webhook_id="missing",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization="Bearer anything",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )

    with pytest.raises(AuthenticationError):
        await routes.fire_workflow_webhook(
            webhook_id="wid-1",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization="Bearer wrong-secret",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )
    _ = raw


@pytest.mark.asyncio
async def test_fire_webhook_success_and_idempotent(monkeypatch):
    from agentcore.api.routes import workflows as routes
    from agentcore.workflows import webhook as wh
    from agentcore.workflows.webhook import generate_webhook_secret

    wh.reset_webhook_state()
    raw, hashed = generate_webhook_secret()
    row = _wf(
        trigger_kind="webhook",
        trigger_enabled=True,
        trigger_folder_id="f1",
        trigger_webhook_id="wid-1",
        trigger_webhook_secret_hash=hashed,
    )
    fires: list[dict] = []

    async def fake_fire(**kwargs):
        fires.append(kwargs)
        return f"conv-{len(fires)}"

    monkeypatch.setattr(routes, "fire_workflow_trigger", fake_fire)

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return row if wid == "wid-1" else None

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return '{"text":"线索一"}'.encode()

    r1 = await routes.fire_workflow_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=f"Bearer {raw}",
        x_agentcore_webhook_secret=None,
        x_idempotency_key="idem-1",
    )
    r2 = await routes.fire_workflow_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=None,
        x_agentcore_webhook_secret=raw,
        x_idempotency_key="idem-1",
    )
    assert r1.conversation_id == r2.conversation_id == "conv-1"
    assert len(fires) == 1
    assert fires[0]["note"] == "线索一"
    assert fires[0]["workflow_id"] == "wf-1"
    wh.reset_webhook_state()


@pytest.mark.asyncio
async def test_fire_webhook_rate_limited(monkeypatch):
    from agentcore.api.routes import workflows as routes
    from agentcore.core.errors import RateLimitedError
    from agentcore.core.rate_limit import SlidingWindowRateLimiter
    from agentcore.workflows import webhook as wh
    from agentcore.workflows.webhook import generate_webhook_secret

    wh.reset_webhook_state()
    monkeypatch.setattr(
        wh, "_webhook_rate_limiter", SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    )
    monkeypatch.setattr(wh.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(wh.settings, "workflow_trigger_webhook_rate_limit_max", 1)

    raw, hashed = generate_webhook_secret()
    row = _wf(
        trigger_kind="webhook",
        trigger_enabled=True,
        trigger_folder_id="f1",
        trigger_webhook_id="wid-1",
        trigger_webhook_secret_hash=hashed,
    )

    monkeypatch.setattr(routes, "fire_workflow_trigger", AsyncMock(return_value="conv-x"))

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return row

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b"{}"

    await routes.fire_workflow_webhook(
        webhook_id="wid-1",
        request=_Req(),
        repo=_Repo(),
        folders=_Folders(),
        authorization=f"Bearer {raw}",
        x_agentcore_webhook_secret=None,
        x_idempotency_key=None,
    )
    with pytest.raises(RateLimitedError):
        await routes.fire_workflow_webhook(
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
async def test_fire_webhook_busy_conflict(monkeypatch):
    from agentcore.api.routes import workflows as routes
    from agentcore.workflows.webhook import generate_webhook_secret

    raw, hashed = generate_webhook_secret()
    row = _wf(
        trigger_kind="webhook",
        trigger_enabled=True,
        trigger_folder_id="f1",
        trigger_webhook_id="wid-1",
        trigger_webhook_secret_hash=hashed,
    )

    async def busy(**kwargs):
        raise ConflictError("工作流正在执行中，请稍后再试")

    monkeypatch.setattr(routes, "fire_workflow_trigger", busy)

    class _Repo:
        async def get_by_webhook_id(self, wid):
            return row

    class _Req:
        headers = {"content-type": "application/json"}

        async def body(self):
            return b"{}"

    with pytest.raises(ConflictError, match="正在执行"):
        await routes.fire_workflow_webhook(
            webhook_id="wid-1",
            request=_Req(),
            repo=_Repo(),
            folders=_Folders(),
            authorization=f"Bearer {raw}",
            x_agentcore_webhook_secret=None,
            x_idempotency_key=None,
        )


def test_retired_standing_routes_absent():
    import importlib.util

    from agentcore.main import app

    assert importlib.util.find_spec("agentcore.api.routes.standing_tasks") is None
    assert importlib.util.find_spec("agentcore.standing_tasks") is None
    paths = " ".join(getattr(r, "path", "") or "" for r in app.routes)
    assert "/standing-tasks" not in paths
    assert "/standing-task" not in paths
    assert "/hooks/standing" not in paths
    assert "/hooks/workflows/{webhook_id}" in paths
    assert "/workflows/{workflow_id}/trigger" in paths
