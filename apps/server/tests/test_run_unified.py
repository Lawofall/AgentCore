"""Unified ``run`` face — classify human commands, no old-name aliases."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from agentcore.tools.builtin import build_builtin_registry
from agentcore.tools.builtin.run import (
    RunTool,
    _is_verify_command,
    _surface_result,
    _wants_background,
    run_description,
)
from agentcore.tools.builtin.run_verify import _is_pnpm_filter_verify_argv
from agentcore.tools.builtin.test_parsers import parse_vitest_output
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.sandbox.protocol import ExecutionRequest, ExecutionResult


def test_registry_exposes_run_not_old_names():
    names = {schema.name for schema in build_builtin_registry().list_all()}
    assert "run" in names
    assert "code_execute" not in names
    assert "test_run" not in names
    assert "terminal" not in names


def test_schema_is_one_command_face():
    schema = RunTool().schema
    assert schema.name == "run"
    props = schema.parameters["properties"]
    assert "command" in props
    assert "background" in props
    assert "action" in props
    assert "code" not in props
    assert "check" not in props
    assert "subcommand" not in props


def test_classify_verify_and_long_running():
    assert _is_verify_command("pnpm --filter @whiteboard/core test")
    assert _is_verify_command("pnpm typecheck")
    assert _is_verify_command("pnpm test")
    assert _is_verify_command("pnpm add vitest 2>&1 | tail -20")
    assert _is_verify_command("pnpm test | grep FAIL")
    assert _is_verify_command("cd sub && pnpm test")
    assert _is_verify_command("CI=1 pnpm test")
    assert not _is_verify_command("python -c 'print(1)'")
    assert not _is_verify_command("pnpm test && echo hi")
    assert _wants_background({"background": True, "command": "echo hi"})
    assert _wants_background({"command": "pnpm dev"})
    assert not _wants_background({"command": "pnpm test"})
    assert not _wants_background({"command": "cd sub && pnpm test"})


def test_pnpm_filter_is_allowed_verify():
    assert _is_pnpm_filter_verify_argv(
        ["pnpm", "--filter", "@whiteboard/core", "test"]
    )
    assert _is_pnpm_filter_verify_argv(
        ["pnpm", "--filter", "@whiteboard/core", "typecheck"]
    )
    assert not _is_pnpm_filter_verify_argv(["pnpm", "--filter", "x", "dev"])


def test_vitest_parser_keeps_assertion_and_strips_ansi():
    raw = (
        "\x1b[22mFAIL\x1b[2m src/scene/Scene.test.ts > "
        "\x1b[22madd：自动分配 id\n"
        "AssertionError: expected '1' to be '2'\n"
        "Expected: '2'\n"
        "Received: '1'\n"
        "Tests 0 passed | 1 failed\n"
    )
    parsed = parse_vitest_output(raw, "")
    assert parsed.failed == 1
    assert "自动分配" in parsed.failures[0].test_name
    assert "\x1b" not in parsed.failures[0].test_name
    assert parsed.failures[0].message
    assert parsed.failures[0].snippet


def test_run_description_does_not_role_split_ceo():
    desc = run_description()
    assert "CEO 只启停" not in desc
    assert "验收与短命令由队员" not in desc
    assert "ceo_run_scope" not in desc
    assert "命令" in desc
    assert "HOW→consult(run)" in desc


class _FakeShortBackend:
    location = "server"

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            success=True, stdout="1\n", stderr="", exit_code=0, duration_ms=1
        )


async def test_ceo_short_command_runs_like_worker():
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="ceo",
        backend=_FakeShortBackend(),  # type: ignore[arg-type]
        user_id="u",
    )
    assert ctx.write_coordinator is None
    assert ctx.escalation is None
    result = await RunTool().execute({"command": "print(1)"}, ctx)
    assert result.success is True
    assert "1" in (result.output or "")
    assert (result.metadata or {}).get("code") != "ceo_run_scope"


async def test_foreground_wait_timeout_is_contract_failure():
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="worker",
        backend=_FakeShortBackend(),  # type: ignore[arg-type]
        user_id="u",
    )
    ctx = replace(ctx, write_coordinator=MagicMock())
    for command in ("pnpm test", "print(1)"):
        result = await RunTool().execute(
            {"command": command, "wait_timeout_seconds": 30},
            ctx,
        )
        assert result.success is False
        assert result.contract_failure is True
        assert "wait_timeout_seconds" in (result.error or "")


async def test_cd_dotdot_from_root_is_contract_failure():
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="worker",
        backend=_FakeShortBackend(),  # type: ignore[arg-type]
        user_id="u",
    )
    ctx = replace(ctx, write_coordinator=MagicMock())
    result = await RunTool().execute({"command": "cd .."}, ctx)
    assert result.success is False
    assert result.contract_failure is True
    assert "工作区" in (result.error or "")


def test_surface_result_rewrites_terminal_in_error():
    raw = ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error="请改用 terminal",
        duration_ms=0,
    )
    out = _surface_result(raw)
    assert out.error == "请改用 run"
