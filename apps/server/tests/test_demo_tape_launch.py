"""Unit tests for demo-tape catalog + one-click start (dev-only)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.demo_tape.catalog import list_tapes, resolve_tape
from agentcore.demo_tape.launch import prepare_demo_tape_launch, require_replay_enabled


def test_require_replay_enabled_gates(monkeypatch):
    from agentcore.demo_tape import launch as launch_mod

    monkeypatch.setattr(launch_mod.settings, "demo_tape_replay_enabled", False)
    with pytest.raises(NotFoundError):
        require_replay_enabled()

    monkeypatch.setattr(launch_mod.settings, "demo_tape_replay_enabled", True)
    require_replay_enabled()  # no raise


def test_list_and_resolve_tapes(tmp_path: Path, monkeypatch):
    from agentcore.demo_tape import catalog as catalog_mod

    tapes = tmp_path / "demos" / "tapes"
    tapes.mkdir(parents=True)
    doc = {
        "version": 1,
        "meta": {
            "title": "演示案",
            "user_prompt": "请启动辩论",
            "duration_ms": 1200,
            "event_count": 3,
        },
        "events": [],
    }
    (tapes / "sample-case.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )
    (tapes / "ignore.txt").write_text("nope", encoding="utf-8")

    monkeypatch.setattr(catalog_mod, "PROJECT_ROOT", tmp_path)
    found = list_tapes()
    assert len(found) == 1
    assert found[0].id == "sample-case"
    assert found[0].title == "演示案"
    assert found[0].user_prompt == "请启动辩论"
    assert found[0].turn_count == 1
    assert found[0].repo_relative == "demos/tapes/sample-case.json"

    assert resolve_tape("sample-case") is not None
    assert resolve_tape("missing") is None
    assert resolve_tape("../escape") is None


@pytest.mark.asyncio
async def test_prepare_demo_tape_launch_creates_cloud_and_binds(
    tmp_path: Path, monkeypatch
):
    from agentcore.demo_tape import catalog as catalog_mod
    from agentcore.demo_tape import launch as launch_mod

    tapes = tmp_path / "demos" / "tapes"
    tapes.mkdir(parents=True)
    (tapes / "demo.json").write_text(
        json.dumps(
            {
                "version": 1,
                "meta": {"title": "T", "user_prompt": "原始用户请求"},
                "events": [{"kind": "run_started", "payload": {}, "t_ms": 0}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launch_mod.settings, "demo_tape_replay_enabled", True)
    monkeypatch.setattr(launch_mod.settings, "demo_tape_speed", 4.0)
    monkeypatch.setattr(launch_mod.settings, "demo_tape_max_gap_ms", 2000)

    bindings_file = tmp_path / "bindings.json"
    monkeypatch.setattr(
        "agentcore.demo_tape.binding.bindings_path", lambda: bindings_file
    )

    fake_conv = MagicMock()
    fake_conv.id = "conv-demo-1"
    fake_user = MagicMock()
    fake_user.user_id = "user-1"
    session = MagicMock()

    # default_permission_axes_for_user → PermissionAxes with .to_dict()
    axes = MagicMock()
    axes.to_dict.return_value = {
        "file_write": "session",
        "command": "auto",
        "host": "session",
    }

    with (
        patch(
            "agentcore.demo_tape.launch.default_permission_axes_for_user",
            new=AsyncMock(return_value=axes),
        ),
        patch(
            "agentcore.demo_tape.launch.ConversationRepository"
        ) as repo_cls,
    ):
        repo_cls.return_value.create = AsyncMock(return_value=fake_conv)
        result = await prepare_demo_tape_launch(
            tape_id="demo",
            user=fake_user,
            session=session,
        )

    assert result.conversation_id == "conv-demo-1"
    assert result.user_prompt == "原始用户请求"
    assert result.speed == 4.0
    assert result.max_gap_ms == 2000
    create_kwargs = repo_cls.return_value.create.await_args.kwargs
    assert create_kwargs["local_container_root_id"] is None
    assert create_kwargs["folder_id"] is None
    assert create_kwargs["permission_axes"] == {
        "file_write": "session",
        "command": "auto",
        "host": "session",
    }
    data = json.loads(bindings_file.read_text(encoding="utf-8"))
    assert data["conv-demo-1"]["tape"] == "demos/tapes/demo.json"
    assert data["conv-demo-1"]["turn_index"] == 0


@pytest.mark.asyncio
async def test_prepare_rejects_missing_user_prompt(tmp_path: Path, monkeypatch):
    from agentcore.demo_tape import catalog as catalog_mod
    from agentcore.demo_tape import launch as launch_mod

    tapes = tmp_path / "demos" / "tapes"
    tapes.mkdir(parents=True)
    (tapes / "empty.json").write_text(
        json.dumps({"version": 1, "meta": {"title": "x"}, "events": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launch_mod.settings, "demo_tape_replay_enabled", True)

    with pytest.raises(ValidationError):
        await prepare_demo_tape_launch(
            tape_id="empty",
            user=MagicMock(user_id="u"),
            session=MagicMock(),
        )


def _prepared_launch():
    from agentcore.demo_tape.catalog import TapeInfo
    from agentcore.demo_tape.launch import PreparedLaunch

    return PreparedLaunch(
        conversation_id="cid-1",
        tape=TapeInfo(
            id="demo",
            path=Path("x.json"),
            repo_relative="demos/tapes/demo.json",
            title="T",
            user_prompt="原始用户请求",
            duration_ms=1,
            event_count=1,
        ),
        user_prompt="原始用户请求",
        title="T",
        speed=4.0,
        max_gap_ms=2000,
    )


@pytest.mark.asyncio
async def test_prepare_route_binds_without_starting_turn(monkeypatch):
    """POST /prepare returns a bound session and does not register a turn."""
    from agentcore.api.routes import demo_tape as route_mod

    prepared = _prepared_launch()
    registered: dict = {}

    monkeypatch.setattr(
        route_mod,
        "prepare_demo_tape_launch",
        AsyncMock(return_value=prepared),
    )
    monkeypatch.setattr(
        route_mod.turn_runs, "register", lambda **kwargs: registered.update(kwargs)
    )
    created_tasks: list = []
    monkeypatch.setattr(
        route_mod.asyncio,
        "create_task",
        lambda coro: created_tasks.append(coro) or coro.close() or MagicMock(),
    )

    resp = await route_mod.prepare_demo_tape(
        body=route_mod.DemoTapePrepareRequest(tape_id="demo"),
        user=MagicMock(user_id="u1"),
        session=MagicMock(),
    )

    assert resp.conversation_id == "cid-1"
    assert resp.user_prompt == "原始用户请求"
    assert resp.tape_id == "demo"
    assert registered == {}
    assert created_tasks == []


@pytest.mark.asyncio
async def test_start_route_registers_detached_turn(monkeypatch):
    """POST /start wires prepare → stream_chat → turn_runs (unit, no DB)."""
    from agentcore.api.routes import demo_tape as route_mod

    prepared = _prepared_launch()
    preflight = MagicMock()
    preflight.credentials = None
    preflight.supports_tools = True
    preflight.warnings = []

    registered: dict = {}

    def _register(**kwargs):
        registered.update(kwargs)

    monkeypatch.setattr(
        route_mod,
        "prepare_demo_tape_launch",
        AsyncMock(return_value=prepared),
    )
    monkeypatch.setattr(
        route_mod,
        "_preflight_turn_llm",
        AsyncMock(return_value=preflight),
    )
    monkeypatch.setattr(
        route_mod, "release_request_db_before_sse", AsyncMock()
    )
    monkeypatch.setattr(route_mod, "emit_preflight_warnings", lambda *_a, **_k: None)
    monkeypatch.setattr(route_mod, "_wait_for_user_message", AsyncMock())
    monkeypatch.setattr(route_mod, "_wait_for_paused_or_settled", AsyncMock())
    monkeypatch.setattr(route_mod.turn_runs, "register", _register)

    created_tasks: list = []

    def _create_task(coro):
        created_tasks.append(coro)
        # Prevent "coroutine was never awaited" — close without running.
        coro.close()
        task = MagicMock()
        return task

    monkeypatch.setattr(route_mod.asyncio, "create_task", _create_task)

    body = route_mod.DemoTapeStartRequest(tape_id="demo")
    user = MagicMock(user_id="u1")
    session = MagicMock()

    resp = await route_mod.start_demo_tape(
        body=body,
        user=user,
        session=session,
        x_client_platform="desktop",
    )

    assert resp.conversation_id == "cid-1"
    assert resp.user_prompt == "原始用户请求"
    assert registered["conversation_id"] == "cid-1"
    assert len(created_tasks) == 1
