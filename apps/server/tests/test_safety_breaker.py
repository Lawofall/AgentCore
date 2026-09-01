"""Unit tests for the P3 safety circuit breaker (heuristic last line).

Honest positioning: these assert the blacklist heuristics we chose to ship —
they do not prove every dangerous command is intercepted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agentcore.core.types import AutonomyPolicy, ToolApproval, ToolCategory, recipe_to_axes
from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction
from agentcore.runtime.approvals import ApprovalDecision, ApprovalGate
from agentcore.runtime.engine import tool_exec as tool_exec_mod
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.safety_breaker import (
    BreakerVerdict,
    SensitivePathClass,
    classify_sensitive_path,
    command_text_for_tool,
    evaluate_tool_call,
    fuse_aligned_deny_rule_ids,
    git_forbidden_subcommands,
    is_sensitive_path,
    scan_destructive_text,
)
from agentcore.runtime.sandbox_approval import execution_tool_auto_passes
from agentcore.tools.builtin.git_ops import _FORBIDDEN_PATTERNS
from agentcore.tools.builtin.host import shell_fuse_blocks
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

pytestmark = pytest.mark.anyio


# ── Pure rule module ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "rm -rf /",
        "sudo rm -rf /",
        "rm -rf /*",
        "rm -rf ~",
        "rm -rf $HOME",
        "rm -rf ${HOME}/",
        "rm -fr /",
        "rm --force --recursive /",
    ],
)
def test_scan_destructive_rm_root_hits(text: str):
    hit = scan_destructive_text(text)
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
    assert hit.rule_id == "destructive.rm_root"
    assert "并非完整拦截" in hit.reason


def test_scan_destructive_rm_workspace_paths_pass():
    """Ordinary workspace relative rm stays off the catastrophic rm_root rule.

    P2 top-tree / whitelist behavior is covered by evaluate_tool_call tests below —
    this asserts the narrow catastrophic scanner does not expand to every ``rm -rf``.
    """
    assert scan_destructive_text("rm -rf /tmp/build") is None
    assert scan_destructive_text("rm -rf ./dist") is None
    assert scan_destructive_text("rm -rf node_modules") is None
    assert scan_destructive_text("rm file.txt") is None


@pytest.mark.parametrize(
    "text,rule_id",
    [
        ("mkfs.ext4 /dev/sda1", "destructive.format_device"),
        ("dd if=/dev/zero of=/dev/sda bs=1M", "destructive.format_device"),
        ("format C:", "destructive.format_device"),
        ("shutdown -h now", "destructive.shutdown"),
        ("Restart-Computer", "destructive.shutdown"),
    ],
)
def test_scan_destructive_other_rules(text: str, rule_id: str):
    hit = scan_destructive_text(text)
    assert hit is not None
    assert hit.rule_id == rule_id
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL


@pytest.mark.parametrize(
    "text",
    [
        "git push --force origin main",
        "git push -f origin master",
        "git push origin main --force-with-lease",
    ],
)
def test_scan_git_force_push_protected_denies(text: str):
    """Shell text force→main|master is DENY (aligned with structured git)."""
    hit = scan_destructive_text(text)
    assert hit is not None
    assert hit.rule_id == "destructive.git_force_push_protected"
    assert hit.verdict is BreakerVerdict.DENY
    assert "硬拒" in hit.reason
    assert "并非完整拦截" in hit.reason


def test_scan_git_push_feature_branch_ok():
    assert scan_destructive_text("git push origin feature/foo") is None
    assert scan_destructive_text("git push --force origin feature/foo") is None


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "config/.env",
        "apps/server/.env",
        "credentials.json",
        ".aws/credentials",
        ".npmrc",
    ],
)
def test_ask_sensitive_paths(path: str):
    assert classify_sensitive_path(path) is SensitivePathClass.ASK
    assert is_sensitive_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "id_rsa",
        ".ssh/id_ed25519",
        "certs/server.pem",
        "secrets/app.key",
    ],
)
def test_deny_sensitive_paths(path: str):
    assert classify_sensitive_path(path) is SensitivePathClass.DENY
    assert is_sensitive_path(path) is True


def test_sensitive_globs():
    assert classify_sensitive_path(".env*") is SensitivePathClass.ASK
    assert classify_sensitive_path("*.pem") is SensitivePathClass.DENY
    assert classify_sensitive_path("config/.env.*") is SensitivePathClass.ASK


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "src/main.py",
        ".gitignore",
        "env.example",
        ".env.example",
        "apps/mobile/.env.example",
        ".env.sample",
        ".env.template",
        "deploy/config/production.env.example",
        "packages/contract-types/src/events.generated.ts",
        ".",
        "",
    ],
)
def test_non_sensitive_paths(path: str):
    assert classify_sensitive_path(path) is SensitivePathClass.NONE
    assert is_sensitive_path(path) is False


def test_evaluate_file_read_credential_asks():
    hit = evaluate_tool_call("file_read", {"path": ".env.local"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
    assert hit.rule_id == "sensitive.path_read_ask"
    assert "模型上下文" in hit.reason
    assert "并非完整拦截" in hit.reason
    assert "粘贴" not in hit.reason


def test_evaluate_file_read_key_material_denies():
    hit = evaluate_tool_call("file_read", {"path": "id_rsa"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "sensitive.path_read"
    assert "私钥" in hit.reason or "密钥材料" in hit.reason
    assert "并非完整拦截" in hit.reason
    # Steer away from chat paste; must not invite「粘贴所需片段」.
    assert "粘贴所需" not in hit.reason
    assert "不要把密钥" in hit.reason


def test_evaluate_file_read_template_passes():
    assert evaluate_tool_call("file_read", {"path": "apps/mobile/.env.example"}) is None
    assert evaluate_tool_call("file_write", {"path": ".env.example", "content": "A=\n"}) is None


def test_evaluate_file_read_normal_passes():
    assert evaluate_tool_call("file_read", {"path": "src/app.py"}) is None


def test_evaluate_file_write_sensitive_path_denies():
    """案 image-gen B：敏感路径写盘硬拒（含凭据 Ask 类路径）。"""
    hit = evaluate_tool_call("file_write", {"path": ".env", "content": "FOO=1"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "sensitive.path_write"


def test_evaluate_file_write_secret_content_denies():
    """案 image-gen B：正文含 API Key 形状 → 拒写入工作区明文。"""
    hit = evaluate_tool_call(
        "file_write",
        {"path": "env", "content": "OPENAI_API_KEY=sk-abcdEFGH1234567890\n"},
    )
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "sensitive.secret_write"
    assert "明文" in hit.reason or "API Key" in hit.reason


def test_evaluate_str_replace_secret_new_string_denies():
    hit = evaluate_tool_call(
        "str_replace",
        {
            "path": "scripts/generate_image.py",
            "old_string": 'MODEL = "imega1"',
            "new_string": 'API_KEY = "sk-abcdEFGH1234567890"',
        },
    )
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "sensitive.secret_write"


def test_evaluate_file_write_erp_field_names_not_secret_deny():
    """ERP md 表字段（task_created_at 等）不得误触发 sensitive.secret_write。"""
    hit = evaluate_tool_call(
        "file_write",
        {
            "path": "docs/erp-schema.md",
            "content": (
                "| field | type |\n"
                "| task_created_at | datetime |\n"
                "| task_priority | int |\n"
            ),
        },
    )
    assert hit is None or hit.rule_id != "sensitive.secret_write"


def test_evaluate_file_write_safe_scaffold_passes():
    assert (
        evaluate_tool_call(
            "file_write",
            {
                "path": "scripts/generate_image.py",
                "content": "import os\nKEY = os.environ['OPENAI_API_KEY']\n",
            },
        )
        is None
    )


def test_evaluate_run_destructive_forces():
    hit = evaluate_tool_call("run", {"command": "rm -rf /"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL


def test_evaluate_run_process_manage_skips():
    assert evaluate_tool_call("run", {"action": "read", "command": "rm -rf /"}) is None


def test_evaluate_run_benign_passes():
    assert evaluate_tool_call("run", {"command": "print(1+1)\n"}) is None


def test_historical_extractors_still_read_retired_exec_names():
    """Journal-era names still extract; live fuse is ``run``."""
    assert command_text_for_tool("code_execute", {"code": "rm -rf /"}) == "rm -rf /"
    assert "git push --force-with-lease origin master" in command_text_for_tool(
        "test_run", {"filter": "git push --force-with-lease origin master"}
    )


def test_evaluate_whitelist_cleanup_passes():
    """P2 whitelist: ordinary dependency/build cleanup must not FORCE_APPROVAL."""
    for code in (
        'shutil.rmtree("node_modules")\n',
        'shutil.rmtree(".venv")\n',
        "rm -rf dist\n",
        "rm -rf ./build\n",
        "Remove-Item -Recurse -Force .next\n",
        "rimraf __pycache__\n",
    ):
        assert (
            evaluate_tool_call("run", {"command": code})
            is None
        ), code
    assert (
        evaluate_tool_call("run", {"command": "rm -rf node_modules"})
        is None
    )


def test_evaluate_top_level_project_rmtree_forces():
    """P2: top-level whole-project tree → FORCE_APPROVAL (honest heuristic)."""
    hit = evaluate_tool_call(
        "run",
        {"command": 'shutil.rmtree(cwd / "ai-team-workbench")\n'},
    )
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
    assert hit.rule_id == "destructive.workspace_top_tree"
    assert "并非完整拦截" in hit.reason


def test_evaluate_nested_rmtree_not_top_tree():
    """Nested project path is not the P2 top-tree gate (P0 baseline gate is separate)."""
    assert (
        evaluate_tool_call(
            "run",
            {"command": 'shutil.rmtree("src/legacy")\n'},
        )
        is None
    )


def test_host_shell_top_tree_forces_not_deny():
    """P2 top-tree is FORCE_APPROVAL; must not be confused with fuse⊆DENY families."""
    hit = evaluate_tool_call(
        "host", {"action": "shell", "command": "rm -rf ./my-app"}
    )
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
    assert hit.rule_id == "destructive.workspace_top_tree"
    assert hit.rule_id not in fuse_aligned_deny_rule_ids()


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("run", {"command": "git push --force origin main"}),
        ("host", {"action": "shell", "command": "git push --force origin main"}),
        ("run", {"command": "git push --force-with-lease origin master"}),
    ],
)
def test_evaluate_shell_force_push_protected_denies(
    tool_name: str, arguments: dict[str, Any]
):
    """run / host(action=shell): force→main|master → DENY."""
    hit = evaluate_tool_call(tool_name, arguments)
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "destructive.git_force_push_protected"
    assert "destructive.git_force_push_protected" not in fuse_aligned_deny_rule_ids()
    assert "硬拒" in hit.reason


def test_evaluate_host_shell_ordinary_push_passes():
    """Ordinary push is not fuse/breaker-denied — Host GRANTABLE axis still applies."""
    assert (
        evaluate_tool_call(
            "host", {"action": "shell", "command": "git push origin feature/foo"}
        )
        is None
    )
    assert (
        evaluate_tool_call(
            "host",
            {"action": "shell", "command": "git push --force origin feature/foo"},
        )
        is None
    )


# Samples that both host_shell fuse and breaker destructive rules cover.
# Maintaining this table = fuse ⊆ DENY drift guard (方案 C).
_FUSE_SUBSET_DENY_SAMPLES: tuple[tuple[str, str], ...] = (
    ("rm -rf /", "destructive.rm_root"),
    ("sudo rm -rf /", "destructive.rm_root"),
    ("rm -rf /*", "destructive.rm_root"),
    ("mkfs.ext4 /dev/sda1", "destructive.format_device"),
    ("dd if=/dev/zero of=/dev/sda bs=1M", "destructive.format_device"),
    ("format C:", "destructive.format_device"),
    ("shutdown -h now", "destructive.shutdown"),
    ("poweroff", "destructive.shutdown"),
    ("Stop-Computer", "destructive.shutdown"),
    ("Restart-Computer", "destructive.shutdown"),
)


@pytest.mark.parametrize("command,rule_id", _FUSE_SUBSET_DENY_SAMPLES)
def test_host_shell_fuse_covered_destructive_denies(command: str, rule_id: str):
    """方案 C: fuse-covered shapes on host(action=shell) → DENY (no approve-then-fuse-fail)."""
    assert shell_fuse_blocks(command), f"sample must be fuse-covered: {command!r}"
    assert rule_id in fuse_aligned_deny_rule_ids()
    hit = evaluate_tool_call("host", {"action": "shell", "command": command})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == rule_id
    assert "并非完整拦截" in hit.reason
    assert "硬拒" in hit.reason or "已硬拒" in hit.reason


_SILENT_INSTALL_SAMPLES: tuple[str, ...] = (
    r"msiexec /i Setup.msi /quiet",
    r".\Setup.exe /S",
    r"Start-Process foo.exe -ArgumentList '/qn'",
    r"Installer.exe /VERYSILENT",
)


@pytest.mark.parametrize("command", _SILENT_INSTALL_SAMPLES)
def test_host_shell_silent_install_denies(command: str):
    """桶4: silent arbitrary installer heuristics on host(action=shell) → DENY."""
    from agentcore.tools.builtin.host import shell_silent_install_blocks

    assert shell_silent_install_blocks(command), command
    hit = evaluate_tool_call("host", {"action": "shell", "command": command})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "host.silent_install"
    assert "并非完整拦截" in hit.reason
    assert "install_package" in hit.reason


@pytest.mark.parametrize("command,rule_id", _FUSE_SUBSET_DENY_SAMPLES)
def test_run_fuse_covered_shapes_still_force_approval(command: str, rule_id: str):
    """run has no host fuse — same shapes stay FORCE_APPROVAL."""
    hit = evaluate_tool_call("run", {"command": command})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.FORCE_APPROVAL
    assert hit.rule_id == rule_id


def test_fuse_aligned_deny_rule_ids_exclude_git():
    ids = fuse_aligned_deny_rule_ids()
    assert ids == {
        "destructive.rm_root",
        "destructive.format_device",
        "destructive.shutdown",
    }
    assert "destructive.git_force_push_protected" not in ids
    # Drift guard: every sample rule_id is declared in the shared set.
    assert {rid for _, rid in _FUSE_SUBSET_DENY_SAMPLES} <= ids


def test_git_forbidden_list_shared_with_git_ops():
    assert git_forbidden_subcommands() == _FORBIDDEN_PATTERNS
    assert "push" not in git_forbidden_subcommands()
    assert {"reset", "clean"} <= git_forbidden_subcommands()
    assert git_forbidden_subcommands().isdisjoint({"stash", "merge", "rebase"})


def test_evaluate_git_forbidden_denies():
    hit = evaluate_tool_call("git", {"subcommand": "reset"})
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "git.forbidden_subcommand"
    clean = evaluate_tool_call("git", {"subcommand": "clean"})
    assert clean is not None
    assert clean.rule_id == "git.forbidden_subcommand"


def test_evaluate_git_g2_collab_passes_breaker():
    """G2 verbs are allowlisted — breaker must not DENY (approval / execute guards)."""
    for args in (
        {"subcommand": "merge", "ref": "feature/x"},
        {"subcommand": "rebase", "ref": "feature/x"},
        {"subcommand": "cherry-pick", "ref": "abc"},
        {"subcommand": "stash", "action": "push"},
        {"subcommand": "tag", "action": "create", "name": "v1"},
        {"subcommand": "remote", "action": "add", "name": "o", "url": "https://x"},
    ):
        assert evaluate_tool_call("git", args) is None


@pytest.mark.parametrize(
    "args,rule_id",
    [
        ({"subcommand": "stash", "action": "drop"}, "git.forbidden_stash_destructive"),
        ({"subcommand": "stash", "action": "clear"}, "git.forbidden_stash_destructive"),
        ({"subcommand": "tag", "action": "delete"}, "git.forbidden_tag_delete"),
        ({"subcommand": "remote", "action": "remove"}, "git.forbidden_remote_remove"),
    ],
)
def test_evaluate_git_g2_destructive_actions_deny(args: dict[str, Any], rule_id: str):
    hit = evaluate_tool_call("git", args)
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == rule_id


def test_evaluate_git_ordinary_push_passes():
    """Ordinary push is allowlisted — breaker must not DENY (approval path)."""
    assert evaluate_tool_call("git", {"subcommand": "push"}) is None
    assert evaluate_tool_call("git", {"subcommand": "push", "remote": "origin"}) is None


@pytest.mark.parametrize(
    "args",
    [
        {"subcommand": "push", "force": True},
        {"subcommand": "push", "force_with_lease": True},
        {"subcommand": "push", "branch": "main"},
        {"subcommand": "push", "branch": "master"},
        {"subcommand": "push", "refspec": "feature:main"},
        {"subcommand": "push", "remote": "--force"},
    ],
)
def test_evaluate_git_push_force_or_protected_denies(args: dict[str, Any]):
    hit = evaluate_tool_call("git", args)
    assert hit is not None
    assert hit.verdict is BreakerVerdict.DENY
    assert hit.rule_id == "git.push_force_or_protected"


# ── Gate + full_trust: force still prompts ───────────────────────────────────


def _drain(sink: EventSink):
    events = []
    while not sink._queue.empty():  # noqa: SLF001
        events.append(sink._queue.get_nowait())
    return events


async def _resolve_when_ready(
    registry: InteractionRegistry,
    approval_id: str,
    decision: ApprovalDecision,
    conversation_id: str,
) -> None:
    for _ in range(2000):
        if registry.resolve(approval_id, decision, conversation_id=conversation_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"approval {approval_id!r} never became pending")


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c",
    )


async def test_force_authorize_ignores_turn_grant_and_delegation():
    """Circuit-breaker force=True must not honor kickoff / turn grants."""
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-cb",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
        delegation_grantable_tools=frozenset({"run"}),
    )
    gate.grant_delegation("exec-1")
    gate._granted.add("run")  # noqa: SLF001 — simulate 本轮放行

    task = asyncio.create_task(
        gate.authorize(
            tool_name="run",
            tool_call_id="tc-force-1",
            arguments={"command": "rm -rf /", "circuit_breaker_hint": "hint"},
            execution_id="exec-1",
            force=True,
        )
    )
    await _resolve_when_ready(
        registry, "tc-force-1", ApprovalDecision.APPROVE, "conv-cb"
    )
    assert await task is ApprovalDecision.APPROVE
    types = [e.type for e in _drain(sink)]
    assert EventType.APPROVAL_REQUIRED in types


async def test_force_authorize_refuses_approve_always_grant():
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-cb2",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
    )
    task = asyncio.create_task(
        gate.authorize(
            tool_name="run",
            tool_call_id="tc-force-2",
            arguments={"command": "rm -rf /"},
            force=True,
        )
    )
    await _resolve_when_ready(
        registry, "tc-force-2", ApprovalDecision.APPROVE_ALWAYS, "conv-cb2"
    )
    decision = await task
    assert decision is ApprovalDecision.APPROVE  # downgraded
    assert "run" not in gate._granted  # noqa: SLF001


async def test_full_trust_auto_pass_bypassed_for_destructive_via_tool_exec():
    """Even when sandbox_approval would auto-pass under FULL_AUTO, destructive
    shapes still suspend on the gate."""
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-ft",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
        delegation_grantable_tools=frozenset({"run"}),
    )

    class _Local:
        location = "local"

    class _ExecTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="run",
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.EXECUTION,
                approval=ToolApproval.GRANTABLE,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(tool_call_id="", success=True, output="ran")

    registry_tools = ToolRegistry()
    registry_tools.register(_ExecTool())
    ctx = ToolContext.create(
        execution_id="exec-ft",
        run_id="run-ft",
        agent_id="a",
        backend=_Local(),  # type: ignore[arg-type]
        user_id="u",
        conversation_id="conv-ft",
    )

    assert (
        execution_tool_auto_passes(
            _Local(), "run", permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED)
        )
        is True
    )

    tc = ToolCall(
        id="tc-ft-1",
        function=ToolCallFunction(
            name="run", arguments='{"command": "rm -rf /"}'
        ),
    )

    async def _approve() -> None:
        await _resolve_when_ready(
            registry, "tc-ft-1", ApprovalDecision.APPROVE, "conv-ft"
        )

    approve_task = asyncio.create_task(_approve())
    messages, terminal, attempts = await tool_exec_mod.execute_tools(
        [tc],
        registry_tools,
        ctx,
        sink,
        approval_gate=gate,
        run_id="run-ft",
    )
    await approve_task
    assert terminal is None
    assert attempts[0].success is True
    assert messages[0].content == "ran"
    required = [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required) == 1
    gate_args = required[0].payload["arguments"]
    assert "circuit_breaker_hint" in gate_args
    assert gate_args["rule_id"] == "destructive.rm_root"
    assert gate_args["force_one_shot"] is True


async def test_sensitive_credential_read_forces_approval():
    """Ask-class credential read → FORCE_APPROVAL; keys preview on card, not model."""
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-ask",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
    )
    secret_body = "PREVIEW_ONLY_KEY=super-secret-value\nOTHER=1\n"
    executed_args: dict[str, Any] = {}

    class _Backend:
        location = "local"
        root = Path(".")

        async def read(self, path: str) -> str:
            assert path == ".env"
            return secret_body

    class _ReadTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_read",
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.NEVER,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            executed_args.update(arguments)
            return ToolResult(tool_call_id="", success=True, output="SECRET=1")

    tools = ToolRegistry()
    tools.register(_ReadTool())
    ctx = ToolContext.create(
        execution_id="exec-ask",
        run_id="run-ask",
        agent_id="a",
        backend=_Backend(),  # type: ignore[arg-type]
        user_id="u",
        conversation_id="conv-ask",
    )
    tc = ToolCall(
        id="tc-read-ask",
        function=ToolCallFunction(name="file_read", arguments='{"path": ".env"}'),
    )

    async def _approve() -> None:
        await _resolve_when_ready(
            registry, "tc-read-ask", ApprovalDecision.APPROVE, "conv-ask"
        )

    approve_task = asyncio.create_task(_approve())
    messages, _, attempts = await tool_exec_mod.execute_tools(
        [tc], tools, ctx, sink, approval_gate=gate, run_id="run-ask"
    )
    await approve_task
    assert attempts[0].success is True
    assert "SECRET=1" in messages[0].content
    required = [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required) == 1
    gate_args = required[0].payload["arguments"]
    hint = gate_args["circuit_breaker_hint"]
    assert gate_args["rule_id"] == "sensitive.path_read_ask"
    assert "force_one_shot" not in gate_args
    # Key assertion: preview lands only on approval args.circuit_breaker_hint;
    # file body must not be fed to the model via hint / gate args / execute args.
    assert "键名预览" in hint
    assert "PREVIEW_ONLY_KEY" in hint
    assert "OTHER" in hint
    assert "super-secret-value" not in hint
    assert "super-secret-value" not in str(gate_args)
    assert secret_body not in str(gate_args)
    assert "super-secret-value" not in (messages[0].content or "")
    assert executed_args.get("path") == ".env"
    assert "circuit_breaker_hint" not in executed_args
    assert "rule_id" not in executed_args
    assert "force_one_shot" not in executed_args
    assert "super-secret-value" not in str(executed_args)


async def test_sensitive_path_read_ask_approve_always_grants_same_tool():
    """path_read_ask: first card required; APPROVE_ALWAYS → same-tool re-read skips."""
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-grant",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
    )
    read_paths: list[str] = []

    class _Backend:
        location = "local"
        root = Path(".")

        async def read(self, path: str) -> str:
            return "KEY=1\n"

    class _ReadTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_read",
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.NEVER,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            read_paths.append(str(arguments.get("path") or ""))
            return ToolResult(tool_call_id="", success=True, output="ok")

    tools = ToolRegistry()
    tools.register(_ReadTool())
    ctx = ToolContext.create(
        execution_id="exec-grant",
        run_id="run-grant",
        agent_id="a",
        backend=_Backend(),  # type: ignore[arg-type]
        user_id="u",
        conversation_id="conv-grant",
    )

    tc1 = ToolCall(
        id="tc-grant-1",
        function=ToolCallFunction(name="file_read", arguments='{"path": ".env"}'),
    )

    async def _approve_always() -> None:
        await _resolve_when_ready(
            registry, "tc-grant-1", ApprovalDecision.APPROVE_ALWAYS, "conv-grant"
        )

    approve_task = asyncio.create_task(_approve_always())
    messages1, _, attempts1 = await tool_exec_mod.execute_tools(
        [tc1], tools, ctx, sink, approval_gate=gate, run_id="run-grant"
    )
    await approve_task
    assert attempts1[0].success is True
    assert messages1[0].content == "ok"
    assert "file_read" in gate._granted  # noqa: SLF001
    required1 = [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required1) == 1
    assert required1[0].payload["arguments"]["rule_id"] == "sensitive.path_read_ask"
    assert "force_one_shot" not in required1[0].payload["arguments"]

    tc2 = ToolCall(
        id="tc-grant-2",
        function=ToolCallFunction(
            name="file_read", arguments='{"path": ".env.local"}'
        ),
    )
    messages2, _, attempts2 = await tool_exec_mod.execute_tools(
        [tc2], tools, ctx, sink, approval_gate=gate, run_id="run-grant"
    )
    assert attempts2[0].success is True
    assert messages2[0].content == "ok"
    required2 = [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert required2 == []
    assert read_paths == [".env", ".env.local"]


async def test_sensitive_credential_preview_soft_fail_still_asks():
    """Preview read failure must not block FORCE_APPROVAL (reason-only hint)."""
    sink = EventSink()
    registry = InteractionRegistry()
    gate = ApprovalGate(
        sink=sink,
        conversation_id="conv-soft",
        registry=registry,
        timeout_seconds=5.0,
        permission_axes=recipe_to_axes(AutonomyPolicy.MANAGED),
    )

    class _Backend:
        location = "local"
        root = Path(".")

        async def read(self, path: str) -> str:
            raise FileNotFoundError(path)

    class _ReadTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_read",
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.NEVER,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(tool_call_id="", success=True, output="ok")

    tools = ToolRegistry()
    tools.register(_ReadTool())
    ctx = ToolContext.create(
        execution_id="exec-soft",
        run_id="run-soft",
        agent_id="a",
        backend=_Backend(),  # type: ignore[arg-type]
        user_id="u",
        conversation_id="conv-soft",
    )
    tc = ToolCall(
        id="tc-read-soft",
        function=ToolCallFunction(name="file_read", arguments='{"path": ".env"}'),
    )

    async def _approve() -> None:
        await _resolve_when_ready(
            registry, "tc-read-soft", ApprovalDecision.APPROVE, "conv-soft"
        )

    approve_task = asyncio.create_task(_approve())
    messages, _, attempts = await tool_exec_mod.execute_tools(
        [tc], tools, ctx, sink, approval_gate=gate, run_id="run-soft"
    )
    await approve_task
    assert attempts[0].success is True
    assert messages[0].content == "ok"
    required = [e for e in _drain(sink) if e.type is EventType.APPROVAL_REQUIRED]
    assert len(required) == 1
    soft_args = required[0].payload["arguments"]
    hint = soft_args["circuit_breaker_hint"]
    assert soft_args["rule_id"] == "sensitive.path_read_ask"
    assert "force_one_shot" not in soft_args
    assert "并非完整拦截" in hint
    assert "键名预览" not in hint


async def test_sensitive_key_read_denied_as_policy_failure():
    sink = EventSink()

    class _ReadTool:
        @property
        def schema(self) -> ToolSchema:
            return ToolSchema(
                name="file_read",
                description="t",
                parameters={"type": "object", "properties": {}},
                category=ToolCategory.FILESYSTEM,
                approval=ToolApproval.NEVER,
            )

        async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
            raise AssertionError("must not execute key-material read")

    tools = ToolRegistry()
    tools.register(_ReadTool())
    ctx = _ctx()
    tc = ToolCall(
        id="tc-read-1",
        function=ToolCallFunction(name="file_read", arguments='{"path": "id_rsa"}'),
    )
    messages, _, attempts = await tool_exec_mod.execute_tools(
        [tc], tools, ctx, sink, approval_gate=None, run_id="run-r"
    )
    assert attempts[0].success is False
    assert attempts[0].policy_failure is True
    assert "敏感" in messages[0].content or "私钥" in messages[0].content or "密钥" in messages[0].content
