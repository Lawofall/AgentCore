"""Background-process tool — spawn / read / stop / list long-lived commands.

CEO + worker. Assembly follows the execution class (desk health on cloud).
``location=local`` (本机 / sidecar) still routes four ``WorkspaceOp`` values over
``WorkspaceChannel`` (desktop ``process_*``). ``location=server`` manages
processes inside the cloud-desk guest — short exec to background / kill,
host-runtime logs, ledger keyed by ``conversation_id``.

CEO holds it for pure start/stop/list; write/repair/install still goes through
``delegate``. Schema stays ``ToolApproval.NEVER`` so read / stop / list skip the
gate; ``start`` is gated via ``tool_call_requires_approval``.
See docs/03-AI核心/工具与能力系统.md (terminal 行) and 安全权限与治理 §五.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import SandboxError
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.long_running import (
    long_running_command_match,
    readiness_footer,
    wait_for_required_message,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    FileProductsContract,
    ToolRegistration,
    ToolSurface,
)
from agentcore.tools.sandbox.desk_process import (
    CLOUD_DESK_REQUIRED,
    PROCESS_NOT_REGISTERED,
    DeskProcessError,
    list_desk_processes,
    read_desk_process,
    start_desk_process,
    stop_desk_process,
)
from agentcore.workspace.channel import WorkspaceOp
from agentcore.workspace.limits import (
    is_channel_dead_detail,
    is_liveness_timeout_detail,
)
from agentcore.workspace.protocol import WorkspaceError

_ALLOWED_SUBCOMMANDS = frozenset({"start", "read", "stop", "list"})
_APPROVAL_SUBCOMMANDS = frozenset({"start"})

# Stable user-facing failure codes (twin of the engine's curated copy table).
# ``terminal`` runs on the user's own machine, so a failure here is something they can
# often act on — the face must say which kind it was.
_LOCAL_WORKSPACE_REQUIRED = "local_workspace_required"
_WORKSPACE_IO_ERROR = "workspace_io_error"

# Spawn / first-chunk ceiling when ``wait_for`` is absent (channel + engine).
_FAST_TIMEOUT_SECONDS = 60.0
_DEFAULT_WAIT_TIMEOUT_SECONDS = 30.0
_MAX_WAIT_TIMEOUT_SECONDS = 300.0
_DEFAULT_TAIL_LINES = 80

TERMINAL_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subcommand": {
            "type": "string",
            "enum": ["start", "read", "stop", "list"],
            "description": (
                "start：启动长时进程并返回 process_id + 首段输出；"
                "read：读尾部输出 / 按 regex 等待新输出；"
                "stop：终止进程；"
                "list：列出本对话进程（可能含用户交互终端「用户终端 #N」，可读不可停）。"
            ),
        },
        "command": {
            "type": "string",
            "description": "start 时要启动的命令（shell 字符串，如 `pnpm dev`）。",
        },
        "cwd": {
            "type": "string",
            "description": "start 的工作目录（工作区相对路径，可选；默认工作区根）。",
        },
        "wait_for": {
            "type": "string",
            "description": (
                "start / read：等待输出匹配此正则后再返回"
                "（如 Local:|ready in|Listening）。"
                "启动 npm run dev / vite / next dev / uvicorn --reload 等长驻进程时【必填】。"
                "宣称「已就绪」须 wait_for 命中，勿仅凭首段输出。"
            ),
        },
        "wait_timeout_seconds": {
            "type": "number",
            "description": (
                f"wait_for 最长等待秒数（默认 {_DEFAULT_WAIT_TIMEOUT_SECONDS:.0f}，"
                f"上限 {_MAX_WAIT_TIMEOUT_SECONDS:.0f}）。"
            ),
            "default": _DEFAULT_WAIT_TIMEOUT_SECONDS,
        },
        "name": {
            "type": "string",
            "description": "start 可选：进程显示名（终端 tab 用）。",
        },
        "process_id": {
            "type": "string",
            "description": "read / stop 的进程 id（start 返回值）。",
        },
        "tail_lines": {
            "type": "integer",
            "description": "read 可选：返回末尾最多 N 行（默认由桌面侧决定）。",
        },
        "purpose": {
            "type": "string",
            "description": (
                "一句话中文说明为何启动该进程；会展示给用户作为审批说明，执行时忽略"
            ),
        },
    },
    "required": ["subcommand"],
}


def terminal_description(location: Literal["server", "local"] | None = None) -> str:
    """Location-aware schema copy. Catalog (location=None) does not say 仅本地."""
    if location == "local":
        where = (
            "在用户本机启动/管理长时后台进程（dev server、watch、长脚本等）。"
            "进程由桌面主进程托管，跨回合存活。"
        )
    elif location == "server":
        where = (
            "在云桌执行环境里启动/管理长时后台进程（dev server、watch、长脚本等）。"
            "进程按本对话记账（同文件夹不共用一条开发服务器）；"
            "日志在宿主机运行时目录，不是工作区交付物。"
            "服务重启后进程登记可能丢失，勿把已消失的 process_id 当成还活着。"
        )
    else:
        where = (
            "启动/管理长时后台进程（dev server、watch、长脚本等）。"
            "本机由桌面托管；云端在同一张云桌 guest 内按对话记账。"
        )
    return (
        where
        + "永不退出的长驻进程用本工具，禁止改走 code_execute / host(action=shell)。"
        "短命令：装包/build/test → test_run（worker）；极短 CLI → code_execute（worker）。"
        "HOW→consult(terminal)。"
    )


def terminal_approval_subcommands() -> frozenset[str]:
    """Subcommands that require user approval (``start`` only)."""
    return _APPROVAL_SUBCOMMANDS


def clamp_wait_timeout_seconds(raw: Any) -> float:
    """Normalize ``wait_timeout_seconds`` into ``[1, MAX]`` (default when missing)."""
    if raw is None:
        return _DEFAULT_WAIT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WAIT_TIMEOUT_SECONDS
    return max(1.0, min(value, _MAX_WAIT_TIMEOUT_SECONDS))


def terminal_op_timeout_seconds(arguments: dict[str, Any] | None) -> float:
    """Per-op channel / engine ceiling for one ``terminal`` call.

    With ``wait_for``, both the channel transport deadline and
    ``resolve_tool_timeout`` must outlive ``wait_timeout + slack`` so the tool
    layer does not cancel while the desktop is still waiting for the ready signal.
    Without ``wait_for``, start returns after spawn + first chunk (fast path).
    """
    slack = float(settings.workspace_execute_timeout_slack_seconds)
    if not arguments:
        return _FAST_TIMEOUT_SECONDS
    wait_for = str(arguments.get("wait_for") or "").strip()
    if not wait_for:
        return _FAST_TIMEOUT_SECONDS
    return clamp_wait_timeout_seconds(arguments.get("wait_timeout_seconds")) + slack


def _error(
    error: str,
    start: float,
    *,
    code: str | None = None,
    contract_failure: bool = False,
) -> ToolResult:
    """Failed ``ToolResult`` with elapsed timing and a stable user-facing code.

    ``code`` rides ``metadata["code"]`` — the twin of the engine's curated user copy
    (``runtime/engine/tool_failure_face``). Without one every terminal failure collapses
    into the same info-free sentence, whatever actually went wrong.

    ``contract_failure`` marks a deterministic argument rejection so the run-scoped tool
    circuit breaker skips it (see :class:`~agentcore.tools.protocol.ToolResult`).
    """
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata={"code": code} if code else {},
        contract_failure=contract_failure,
    )


def _arg_error(error: str, start: float) -> ToolResult:
    """Reject a malformed call: coded ``VALIDATION_ERROR``, off the breaker tally.

    A subcommand typo or a missing ``process_id`` is fixed by writing the next call
    differently — it says nothing about whether ``terminal`` works. Counting these as
    transient failures disabled the whole tool after three model typos.
    """
    return _error(error, start, code=ErrorCode.VALIDATION_ERROR, contract_failure=True)


def _desk_process_failure(exc: DeskProcessError, start: float) -> ToolResult:
    """Map a cloud-desk ledger failure; named codes stay visible to the face scanner."""
    if exc.code == CLOUD_DESK_REQUIRED:
        return _error(
            str(exc),
            start,
            code=CLOUD_DESK_REQUIRED,
            contract_failure=exc.contract_failure,
        )
    if exc.code == PROCESS_NOT_REGISTERED:
        return _error(
            str(exc),
            start,
            code=PROCESS_NOT_REGISTERED,
            contract_failure=exc.contract_failure,
        )
    return _error(
        str(exc),
        start,
        code=exc.code,
        contract_failure=exc.contract_failure,
    )


def _workspace_error(e: WorkspaceError, start: float) -> ToolResult:
    """Map a desktop-side op failure, keeping the kind the desktop already told us.

    The channel distinguishes sticky channel-dead from a single-op settle timeout from a
    plain I/O failure; ``str(e)`` alone flattens all three into one sentence.
    """
    detail = str(e) or e.__class__.__name__
    if is_channel_dead_detail(detail):
        return _error(detail, start, code="workspace_channel_dead")
    if is_liveness_timeout_detail(detail):
        return _error(detail, start, code="liveness_timeout")
    return _error(detail, start, code=_WORKSPACE_IO_ERROR)


def _format_process_output(
    value: dict[str, Any], *, had_wait_for: bool = False
) -> str:
    process_id = value.get("process_id", "")
    status = str(value.get("status", ""))
    output = str(value.get("output") or "")
    matched = value.get("matched")
    exit_code = value.get("exit_code")
    lines = [f"process_id: {process_id}", f"status: {status}"]
    if matched is not None:
        lines.append(f"matched: {matched}")
    if exit_code is not None:
        lines.append(f"exit_code: {exit_code}")
    if output:
        lines.append(f"output:\n{output}")
    else:
        lines.append("output:（无）")
    body = "\n".join(lines)
    matched_flag: bool | None
    if matched is True:
        matched_flag = True
    elif matched is False:
        matched_flag = False
    else:
        matched_flag = None
    return body + readiness_footer(
        status=status,
        matched=matched_flag,
        had_wait_for=had_wait_for,
        exit_code=exit_code,
    )


def _format_list_output(processes: list[Any]) -> str:
    if not processes:
        return "（本对话无后台进程）"
    lines: list[str] = []
    for item in processes:
        if not isinstance(item, dict):
            continue
        pid = item.get("process_id", "")
        status = item.get("status", "")
        command = item.get("command", "")
        name = item.get("name")
        started = item.get("started_at", "")
        exit_code = item.get("exit_code")
        label = f"{name} " if name else ""
        line = f"- {label}id={pid} status={status} command={command}"
        if started:
            line += f" started_at={started}"
        if exit_code is not None:
            line += f" exit_code={exit_code}"
        lines.append(line)
    return "\n".join(lines) if lines else "（本对话无后台进程）"


def _process_display(subcommand: str, value: dict[str, Any]) -> dict[str, Any]:
    display: dict[str, Any] = {"subcommand": subcommand}
    for key in ("process_id", "status", "output", "matched", "exit_code", "command", "name"):
        if key in value:
            display[key] = value[key]
    if "processes" in value:
        display["processes"] = value["processes"]
    return display


class TerminalTool:
    """Spawn and manage long-lived processes (desktop channel or cloud-desk guest)."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        execution_class=True,
        needs_location=True,
        # 长驻进程（dev server / build）留下的东西枚举不出，也不是交付物。
        file_products=FileProductsContract.NO_PRODUCT,
    )

    def __init__(self, *, location: Literal["server", "local"] | None = None) -> None:
        self._location = location

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="terminal",
            description=terminal_description(self._location),
            parameters=TERMINAL_TOOL_PARAMETERS,
            category=ToolCategory.EXECUTION,
            approval=ToolApproval.NEVER,
            # Dynamic ceiling via resolve_tool_timeout(arguments=…); schema leaves
            # None so the category default is not a hard 90s cap under wait_for.
            timeout_seconds=None,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        subcommand = str(arguments.get("subcommand", "")).strip().lower()
        if not subcommand:
            return _arg_error("subcommand 为必填参数", start)
        if subcommand not in _ALLOWED_SUBCOMMANDS:
            return _arg_error(f"子命令 '{subcommand}' 不在允许列表中", start)

        if getattr(context.backend, "location", None) == "server":
            try:
                return await self._cloud_dispatch(subcommand, arguments, context, start)
            except DeskProcessError as exc:
                return _desk_process_failure(exc, start)
            except SandboxError as exc:
                msg = exc.message or str(exc)
                launcher = "代码执行环境启动失败" in msg or "云桌短执行超时" in msg
                return _error(
                    msg,
                    start,
                    code="launcher_unavailable" if launcher else _WORKSPACE_IO_ERROR,
                    contract_failure=launcher,
                )

        channel = context.workspace_channel
        if channel is None:
            return _error(
                "当前没有本机桌面进程通道，无法在用户电脑上托管后台进程。"
                "需本机终端时：**推荐**引导 Composer「导入到云」"
                "或诚实说明本回合无法托管；本机传统 open/bind 合法非默认（≠离线）。",
                start,
                code=_LOCAL_WORKSPACE_REQUIRED,
            )

        try:
            if subcommand == "start":
                return await self._cmd_start(arguments, context, start)
            if subcommand == "read":
                return await self._cmd_read(arguments, context, start)
            if subcommand == "stop":
                return await self._cmd_stop(arguments, context, start)
            return await self._cmd_list(context, start)
        except WorkspaceError as e:
            return _workspace_error(e, start)

    async def _cloud_dispatch(
        self,
        subcommand: str,
        arguments: dict[str, Any],
        context: ToolContext,
        start: float,
    ) -> ToolResult:
        bucket = (context.user_id or "").strip() or None
        conv = context.conversation_id or ""
        if subcommand == "start":
            return await self._cloud_start(arguments, context, start, bucket)
        if subcommand == "read":
            process_id = str(arguments.get("process_id") or "").strip()
            if not process_id:
                return _arg_error("read 需要 process_id 参数", start)
            wait_for = str(arguments.get("wait_for") or "").strip()
            tail_lines = _DEFAULT_TAIL_LINES
            if arguments.get("tail_lines") is not None:
                try:
                    tail_lines = int(arguments["tail_lines"])
                except (TypeError, ValueError):
                    return _arg_error("tail_lines 必须是整数", start)
            value = await read_desk_process(
                context.backend,
                conversation_id=conv,
                process_id=process_id,
                wait_for=wait_for,
                wait_timeout_seconds=clamp_wait_timeout_seconds(
                    arguments.get("wait_timeout_seconds")
                )
                if wait_for
                else 30.0,
                tail_lines=tail_lines,
                cache_bucket=bucket,
            )
            return self._process_result("read", value, start, had_wait_for=bool(wait_for))
        if subcommand == "stop":
            process_id = str(arguments.get("process_id") or "").strip()
            if not process_id:
                return _arg_error("stop 需要 process_id 参数", start)
            value = await stop_desk_process(
                context.backend,
                conversation_id=conv,
                process_id=process_id,
                cache_bucket=bucket,
            )
            return self._process_result("stop", value, start, had_wait_for=False)
        value = await list_desk_processes(conversation_id=conv)
        processes = value.get("processes") or []
        if not isinstance(processes, list):
            processes = []
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=_format_list_output(processes),
            duration_ms=duration_ms,
            display=_process_display("list", {"processes": processes}),
        )

    async def _cloud_start(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
        start: float,
        cache_bucket: str | None,
    ) -> ToolResult:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return _arg_error("start 需要 command 参数", start)
        wait_for = str(arguments.get("wait_for") or "").strip()
        if not wait_for:
            detected = long_running_command_match(command)
            if detected is not None:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=wait_for_required_message(detected),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    metadata={"code": "wait_for_required", "matched": detected},
                    contract_failure=True,
                )
        value = await start_desk_process(
            context.backend,
            conversation_id=context.conversation_id or "",
            command=command,
            cwd=str(arguments.get("cwd") or "").strip(),
            name=str(arguments.get("name") or "").strip(),
            wait_for=wait_for,
            wait_timeout_seconds=clamp_wait_timeout_seconds(
                arguments.get("wait_timeout_seconds")
            ),
            cache_bucket=cache_bucket,
        )
        return self._process_result("start", value, start, had_wait_for=bool(wait_for))

    async def _cmd_start(
        self, arguments: dict[str, Any], context: ToolContext, start: float
    ) -> ToolResult:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return _arg_error("start 需要 command 参数", start)

        wait_for = str(arguments.get("wait_for") or "").strip()
        # 就绪验收闸：长驻 CLI 无 wait_for → 拒启动，逼模型带 ready 信号。
        if not wait_for:
            detected = long_running_command_match(command)
            if detected is not None:
                return ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=wait_for_required_message(detected),
                    duration_ms=int((time.monotonic() - start) * 1000),
                    metadata={"code": "wait_for_required", "matched": detected},
                    contract_failure=True,
                )

        args: dict[str, Any] = {"command": command}
        cwd = str(arguments.get("cwd") or "").strip()
        if cwd:
            args["cwd"] = cwd
        if wait_for:
            args["wait_for"] = wait_for
            args["wait_timeout_seconds"] = clamp_wait_timeout_seconds(
                arguments.get("wait_timeout_seconds")
            )
        name = str(arguments.get("name") or "").strip()
        if name:
            args["name"] = name

        assert context.workspace_channel is not None
        value = await context.workspace_channel.request(
            WorkspaceOp.PROCESS_START,
            args,
            timeout=terminal_op_timeout_seconds(arguments),
        )
        return self._process_result("start", value, start, had_wait_for=bool(wait_for))

    async def _cmd_read(
        self, arguments: dict[str, Any], context: ToolContext, start: float
    ) -> ToolResult:
        process_id = str(arguments.get("process_id") or "").strip()
        if not process_id:
            return _arg_error("read 需要 process_id 参数", start)

        args: dict[str, Any] = {"process_id": process_id}
        wait_for = str(arguments.get("wait_for") or "").strip()
        if wait_for:
            args["wait_for"] = wait_for
            args["wait_timeout_seconds"] = clamp_wait_timeout_seconds(
                arguments.get("wait_timeout_seconds")
            )
        if arguments.get("tail_lines") is not None:
            try:
                args["tail_lines"] = int(arguments["tail_lines"])
            except (TypeError, ValueError):
                return _arg_error("tail_lines 必须是整数", start)

        assert context.workspace_channel is not None
        value = await context.workspace_channel.request(
            WorkspaceOp.PROCESS_READ,
            args,
            timeout=terminal_op_timeout_seconds(arguments),
        )
        return self._process_result("read", value, start, had_wait_for=bool(wait_for))

    async def _cmd_stop(
        self, arguments: dict[str, Any], context: ToolContext, start: float
    ) -> ToolResult:
        process_id = str(arguments.get("process_id") or "").strip()
        if not process_id:
            return _arg_error("stop 需要 process_id 参数", start)

        assert context.workspace_channel is not None
        value = await context.workspace_channel.request(
            WorkspaceOp.PROCESS_STOP,
            {"process_id": process_id},
        )
        return self._process_result("stop", value, start, had_wait_for=False)

    async def _cmd_list(self, context: ToolContext, start: float) -> ToolResult:
        assert context.workspace_channel is not None
        value = await context.workspace_channel.request(WorkspaceOp.PROCESS_LIST, {})
        if not isinstance(value, dict):
            return _error("桌面返回了无效的 process_list 结果", start, code=_WORKSPACE_IO_ERROR)
        processes = value.get("processes") or []
        if not isinstance(processes, list):
            processes = []
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=_format_list_output(processes),
            duration_ms=duration_ms,
            display=_process_display("list", {"processes": processes}),
        )

    def _process_result(
        self,
        subcommand: str,
        value: Any,
        start: float,
        *,
        had_wait_for: bool,
    ) -> ToolResult:
        if not isinstance(value, dict) or not value.get("process_id"):
            return _error(f"桌面返回了无效的 {subcommand} 结果", start, code=_WORKSPACE_IO_ERROR)
        duration_ms = int((time.monotonic() - start) * 1000)
        # stop：目标就是退出，不加「就绪判定」脚注。
        if subcommand == "stop":
            process_id = value.get("process_id", "")
            status = value.get("status", "")
            exit_code = value.get("exit_code")
            lines = [f"process_id: {process_id}", f"status: {status}"]
            if exit_code is not None:
                lines.append(f"exit_code: {exit_code}")
            output = "\n".join(lines)
        else:
            output = _format_process_output(value, had_wait_for=had_wait_for)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=duration_ms,
            display=_process_display(subcommand, value),
        )
