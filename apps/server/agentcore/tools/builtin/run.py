"""Unified command face — one model tool.

Industry shape (Cursor / Claude Code): the model types a human command.
Classification is ours: long-running / verify / short. Not three tools.
"""

from __future__ import annotations

from typing import Any, Literal

from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.long_running import long_running_command_match
from agentcore.tools.builtin.package_install import (
    command_payload_argvs,
    is_install_shaped_argv,
    is_safe_relpath,
    network_unavailable_code,
    network_unavailable_message,
    permission_allows_restricted_network,
    registry_pin_env,
    reject_registry_override_in_command,
    reject_workspace_cd,
    validate_install_argv,
)
from agentcore.tools.builtin.run_process import (
    process_manage,
    process_op_timeout_seconds,
)
from agentcore.tools.builtin.run_short import execute_short
from agentcore.tools.builtin.run_verify import (
    _VERIFY_DISASTER_SECONDS,
    _is_allowed_verify_argv,
    _shell_command_runner,
    execute_verify,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.sandbox.exec_env import _ENGINE_TIMEOUT_SLACK_SECONDS

_PROCESS_ACTIONS = frozenset({"read", "stop", "list"})


def run_description(location: Literal["server", "local"] | None = None) -> str:
    if location == "local":
        where = "在用户本机工作区跑命令。"
    elif location == "server":
        where = "在云桌执行环境跑命令。"
    else:
        where = "在当前工作区跑命令。"
    return (
        where
        + "验证与短命令直接跑；dev/watch 设 background=true；"
        + "已有进程 action=read|stop|list。"
        + "HOW→consult(run)。"
    )


def run_op_timeout_seconds(arguments: dict[str, Any] | None) -> float:
    """Engine wall: process manage / start follow terminal; verify uses disaster cap."""
    args = arguments or {}
    action = str(args.get("action") or "").strip().lower()
    if action in _PROCESS_ACTIONS or _wants_background(args):
        return process_op_timeout_seconds(
            {
                "command": args.get("command"),
                "wait_for": args.get("wait_for"),
                "wait_timeout_seconds": args.get("wait_timeout_seconds"),
            }
        )
    command = str(args.get("command") or "")
    if _is_verify_command(command):
        return float(_VERIFY_DISASTER_SECONDS + _ENGINE_TIMEOUT_SLACK_SECONDS)
    return 90.0


def _wants_background(arguments: dict[str, Any]) -> bool:
    if arguments.get("background") is True:
        return True
    command = str(arguments.get("command") or "")
    return bool(command and long_running_command_match(command))


def _is_verify_command(command: str) -> bool:
    """True when any payload looks like install / test / typecheck / build.

    Companion segments (echo, rebuild, …) ride the same human command into the
    verify kernel. Classification picks timeout policy, not a second model tool.
    """
    payloads = command_payload_argvs(command)
    if not payloads:
        return False
    return any(
        is_install_shaped_argv(argv) or _is_allowed_verify_argv(argv)
        for argv in payloads
    )


class RunTool:
    """Single model-facing command tool."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        execution_class=True,
        needs_location=True,
        file_products=FileProductsContract.SELF_REPORT,
        produces_formats=(".xlsx", ".pptx"),
    )

    def __init__(self, *, location: Literal["server", "local"] | None = None) -> None:
        self._location = location

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="run",
            description=run_description(self._location),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "要跑的命令，按人在终端里的写法。"
                            "如 `pnpm --filter @whiteboard/core test`、`pnpm typecheck`、"
                            "`python -c \"print(1)\"`、`pnpm dev`。"
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作区相对目录，可选。",
                    },
                    "background": {
                        "type": "boolean",
                        "default": False,
                        "description": "true=挂起不等结束（dev server / watch）。",
                    },
                    "wait_for": {
                        "type": "string",
                        "description": (
                            "可选。匹配此正则再返回；长驻省略则用默认就绪信号。"
                        ),
                    },
                    "wait_timeout_seconds": {
                        "type": "number",
                        "description": "可选。就绪等待上限（秒）；前台命令忽略。",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["read", "stop", "list"],
                        "description": "管理已有后台进程。新开命令不要填。",
                    },
                    "process_id": {
                        "type": "string",
                        "description": "read / stop 的进程 id（background 启动时返回）。",
                    },
                    "name": {
                        "type": "string",
                        "description": "background 可选显示名。",
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "一句话中文说明为何跑这条命令；会展示给用户作为审批说明，"
                            "执行时忽略"
                        ),
                    },
                },
            },
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.GRANTABLE,
            timeout_seconds=None,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = str(arguments.get("action") or "").strip().lower()
        command = str(arguments.get("command") or "").strip()

        if action in _PROCESS_ACTIONS:
            return await self._dispatch_process(action, arguments, context)
        if _wants_background(arguments):
            if not command:
                return _arg_error("background 启动需要 command")
            cd_err = reject_workspace_cd(command)
            if cd_err:
                return _arg_error(cd_err)
            return await self._dispatch_start(arguments, context)
        if not command:
            return _arg_error("请提供 command，或用 action=list|read|stop 管理后台进程")
        cwd = str(arguments.get("cwd") or "").strip() or None
        if cwd is not None and not is_safe_relpath(cwd):
            return _arg_error(
                f"cwd 必须是工作区相对安全路径（禁止绝对路径 / ..）：{cwd}"
            )
        cd_err = reject_workspace_cd(command)
        if cd_err:
            return _arg_error(cd_err)
        if _is_verify_command(command):
            return await self._dispatch_verify(arguments, context)
        return await self._dispatch_short(arguments, context)

    async def _dispatch_process(
        self,
        action: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        mapped = {
            "subcommand": action,
            "process_id": arguments.get("process_id"),
            "wait_for": arguments.get("wait_for"),
            "wait_timeout_seconds": arguments.get("wait_timeout_seconds"),
            "tail_lines": arguments.get("tail_lines"),
        }
        return await process_manage(
            {k: v for k, v in mapped.items() if v is not None},
            context,
        )

    async def _dispatch_start(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        mapped = {
            "subcommand": "start",
            "command": arguments.get("command"),
            "cwd": arguments.get("cwd"),
            "wait_for": arguments.get("wait_for"),
            "wait_timeout_seconds": arguments.get("wait_timeout_seconds"),
            "name": arguments.get("name"),
            "purpose": arguments.get("purpose"),
        }
        return await process_manage(
            {k: v for k, v in mapped.items() if v is not None},
            context,
        )

    async def _dispatch_verify(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        command = str(arguments.get("command") or "").strip()
        cwd = str(arguments.get("cwd") or "").strip() or None
        # Always pass the human command through so `--filter` / package scripts
        # are not rewritten. The verify kernel still parses test/typecheck-shaped output.
        payload: dict[str, Any] = {
            "check": "command",
            "command": command,
            "purpose": arguments.get("purpose"),
        }
        if cwd:
            payload["working_directory"] = cwd
        return await execute_verify(
            {k: v for k, v in payload.items() if v is not None},
            context,
        )

    async def _dispatch_short(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        command = str(arguments.get("command") or "").strip()
        cwd = str(arguments.get("cwd") or "").strip() or None
        payloads = command_payload_argvs(command)
        install_payloads = [p for p in payloads if is_install_shaped_argv(p)]
        for payload_argv in install_payloads:
            install_err = validate_install_argv(payload_argv)
            if install_err:
                return _arg_error(install_err)
        if install_payloads:
            reg_err = reject_registry_override_in_command(command)
            if reg_err:
                return _arg_error(reg_err)
            if not permission_allows_restricted_network(context.permission_axes):
                msg = network_unavailable_message()
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output=msg,
                    error=msg,
                    duration_ms=0,
                    contract_failure=True,
                    metadata={"code": network_unavailable_code()},
                )
        short_args: dict[str, Any] = {
            "code": _shell_command_runner(command, chdir=cwd),
            "language": "python",
            "purpose": arguments.get("purpose"),
        }
        if install_payloads:
            short_args["env"] = registry_pin_env()
        return await execute_short(
            {k: v for k, v in short_args.items() if v is not None},
            context,
            location=self._location,
        )


def _arg_error(error: str) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=0,
        contract_failure=True,
        metadata={"code": "run_contract"},
    )
