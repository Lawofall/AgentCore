"""Background-process kernel — spawn / read / stop / list long-lived commands.

Local (本机 / sidecar) routes four ``WorkspaceOp`` values over ``WorkspaceChannel``
(desktop ``process_*``). Cloud (``location=server``) manages processes inside the
desk guest — short exec to background / kill, host-runtime logs, ledger keyed by
``conversation_id``.

Not a model-facing tool. Callers pass the argument keys the execute body already
reads: ``subcommand``, ``command``, ``cwd``, ``wait_for``, ``wait_timeout_seconds``,
``name``, ``process_id``, ``tail_lines``.
"""

from __future__ import annotations

import time
from typing import Any

from agentcore.config import settings
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import SandboxError
from agentcore.tools.builtin.long_running import (
    effective_wait_for,
    readiness_footer,
)
from agentcore.tools.protocol import ToolContext, ToolResult
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
    is_liveness_timeout_detail,
    is_presence_disconnected_detail,
)
from agentcore.workspace.protocol import WorkspaceError

_ALLOWED_SUBCOMMANDS = frozenset({"start", "read", "stop", "list"})

# Stable user-facing failure codes (twin of the engine's curated copy table).
# Process manage on the user's own machine is something they can often act on —
# the face must say which kind it was.
_LOCAL_WORKSPACE_REQUIRED = "local_workspace_required"
_WORKSPACE_IO_ERROR = "workspace_io_error"

# Spawn / first-chunk ceiling when ``wait_for`` is absent (channel + engine).
_FAST_TIMEOUT_SECONDS = 60.0
_DEFAULT_WAIT_TIMEOUT_SECONDS = 30.0
_MAX_WAIT_TIMEOUT_SECONDS = 300.0
_DEFAULT_TAIL_LINES = 80


def clamp_wait_timeout_seconds(raw: Any) -> float:
    """Normalize ``wait_timeout_seconds`` into ``[1, MAX]`` (default when missing)."""
    if raw is None:
        return _DEFAULT_WAIT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WAIT_TIMEOUT_SECONDS
    return max(1.0, min(value, _MAX_WAIT_TIMEOUT_SECONDS))


def process_op_timeout_seconds(arguments: dict[str, Any] | None) -> float:
    """Per-op channel / engine ceiling for one process-manage call.

    With ``wait_for``, both the channel transport deadline and the engine wall
    must outlive ``wait_timeout + slack`` so the caller does not cancel while
    the desktop is still waiting for the ready signal. Without ``wait_for``,
    start returns after spawn + first chunk (fast path).
    """
    slack = float(settings.workspace_execute_timeout_slack_seconds)
    if not arguments:
        return _FAST_TIMEOUT_SECONDS
    wait_for = effective_wait_for(
        str(arguments.get("command") or ""), arguments.get("wait_for")
    )
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
    (``runtime/engine/tool_failure_face``). Without one every process-manage failure
    collapses into the same info-free sentence, whatever actually went wrong.

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
    differently — it says nothing about whether process manage works. Counting these
    as transient failures disabled the whole face after three model typos.
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

    The channel distinguishes presence-disconnect from a single-op settle timeout from a
    plain I/O failure; ``str(e)`` alone flattens all three into one sentence.
    """
    detail = str(e) or e.__class__.__name__
    if is_presence_disconnected_detail(detail):
        return _error(detail, start, code="workspace_channel_dead")
    if is_liveness_timeout_detail(detail):
        return _error(detail, start, code="liveness_timeout")
    return _error(detail, start, code=_WORKSPACE_IO_ERROR)


def _format_process_output(
    value: dict[str, Any], *, had_wait_for: bool = False, cloud: bool = False
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
    raw_ports = value.get("http_ports") or ()
    ports = tuple(int(p) for p in raw_ports if isinstance(p, int) or str(p).isdigit())
    return body + readiness_footer(
        status=status,
        matched=matched_flag,
        had_wait_for=had_wait_for,
        exit_code=exit_code,
        cloud=cloud,
        http_ports=ports,
    )


def _process_display(
    subcommand: str, value: dict[str, Any], *, cloud: bool = False
) -> dict[str, Any]:
    display: dict[str, Any] = {"subcommand": subcommand}
    for key in ("process_id", "status", "output", "matched", "exit_code", "command", "name"):
        if key in value:
            display[key] = value[key]
    if "processes" in value:
        display["processes"] = value["processes"]
    ports = value.get("http_ports")
    if ports:
        display["http_ports"] = ports
    if cloud and value.get("status") == "running" and ports:
        display["preview_available"] = True
    return display


def _process_result(
    subcommand: str,
    value: Any,
    start: float,
    *,
    had_wait_for: bool,
    cloud: bool = False,
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
        output = _format_process_output(value, had_wait_for=had_wait_for, cloud=cloud)
    return ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        duration_ms=duration_ms,
        display=_process_display(subcommand, value, cloud=cloud),
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


async def process_manage(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """Start / read / stop / list a long-lived process (desktop channel or cloud desk)."""
    start = time.monotonic()
    subcommand = str(arguments.get("subcommand", "")).strip().lower()
    if not subcommand:
        return _arg_error("subcommand 为必填参数", start)
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return _arg_error(f"子命令 '{subcommand}' 不在允许列表中", start)

    if getattr(context.backend, "location", None) == "server":
        try:
            return await _cloud_dispatch(subcommand, arguments, context, start)
        except DeskProcessError as exc:
            return _desk_process_failure(exc, start)
        except SandboxError as exc:
            from agentcore.tools.sandbox.exec_env import (
                EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
                EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
                is_sandbox_unavailable_error,
            )

            if is_sandbox_unavailable_error(exc):
                return _error(
                    EXEC_ENV_SANDBOX_UNAVAILABLE_USER_MESSAGE,
                    start,
                    code=EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
                )
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
            "或诚实说明本回合无法托管；本机 open/bind（≠离线）。",
            start,
            code=_LOCAL_WORKSPACE_REQUIRED,
        )

    try:
        if subcommand == "start":
            return await _cmd_start(arguments, context, start)
        if subcommand == "read":
            return await _cmd_read(arguments, context, start)
        if subcommand == "stop":
            return await _cmd_stop(arguments, context, start)
        return await _cmd_list(context, start)
    except WorkspaceError as e:
        return _workspace_error(e, start)


async def _cloud_dispatch(
    subcommand: str,
    arguments: dict[str, Any],
    context: ToolContext,
    start: float,
) -> ToolResult:
    bucket = (context.user_id or "").strip() or None
    conv = context.conversation_id or ""
    if subcommand == "start":
        return await _cloud_start(arguments, context, start, bucket)
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
        return _process_result(
            "read", value, start, had_wait_for=bool(wait_for), cloud=True
        )
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
        return _process_result("stop", value, start, had_wait_for=False, cloud=True)
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
    arguments: dict[str, Any],
    context: ToolContext,
    start: float,
    cache_bucket: str | None,
) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    if not command:
        return _arg_error("start 需要 command 参数", start)
    wait_for = effective_wait_for(command, arguments.get("wait_for"))
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
    return _process_result("start", value, start, had_wait_for=bool(wait_for), cloud=True)


async def _cmd_start(
    arguments: dict[str, Any], context: ToolContext, start: float
) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    if not command:
        return _arg_error("start 需要 command 参数", start)

    wait_for = effective_wait_for(command, arguments.get("wait_for"))

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
        timeout=process_op_timeout_seconds(arguments),
    )
    return _process_result("start", value, start, had_wait_for=bool(wait_for))


async def _cmd_read(
    arguments: dict[str, Any], context: ToolContext, start: float
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
        timeout=process_op_timeout_seconds(arguments),
    )
    return _process_result("read", value, start, had_wait_for=bool(wait_for))


async def _cmd_stop(
    arguments: dict[str, Any], context: ToolContext, start: float
) -> ToolResult:
    process_id = str(arguments.get("process_id") or "").strip()
    if not process_id:
        return _arg_error("stop 需要 process_id 参数", start)

    assert context.workspace_channel is not None
    value = await context.workspace_channel.request(
        WorkspaceOp.PROCESS_STOP,
        {"process_id": process_id},
    )
    return _process_result("stop", value, start, had_wait_for=False)


async def _cmd_list(context: ToolContext, start: float) -> ToolResult:
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
