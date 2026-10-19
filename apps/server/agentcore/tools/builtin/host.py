"""Single builtin ``host`` — a short command, or GUI attach/snapshot/click/type.

Orthogonal to workspace ``run``. Transport is ``DesktopClientChannel.request_host``.
Omit ``action`` (or ``action=shell``) → required ``command``. GUI actions
``attach`` / ``snapshot`` / ``click`` / ``type`` are the Computer Use face.
Catastrophic shell shapes and silent installers are fuse-denied; package-manager
installs and ``attach`` always confirm. ``host_ping`` is transport-only and is
not registered.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.desktop.channel import HostOp, HostOpError
from agentcore.runtime.command_policy import is_package_install_command
from agentcore.tools.builtin.host_gui import (
    GUI_ACTIONS,
    execute_gui,
    host_gui_timeout_seconds,
)
from agentcore.tools.builtin.long_running import long_running_command_match
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

# action=shell: frozen timeout. Desktop kills the process at this budget.
_SHELL_TIMEOUT_DEFAULT = 60
_SHELL_CHANNEL_SLACK_SECONDS = 15.0

# Package-manager installs (Docker Desktop / VS Code) often exceed the short budget.
_PACKAGE_TIMEOUT_DEFAULT = 600
_PACKAGE_CHANNEL_SLACK_SECONDS = 30.0

# Heuristic fuse — not a complete security boundary.
_SHELL_FUSE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|-[a-zA-Z]*r[a-zA-Z]*\s+)*(/|/\*|~|/home)\b",
        r"\brm\s+-rf\s+/",
        r"\bformat\s+[a-z]:",
        r"\bFormat-Volume\b",
        r"\bClear-Disk\b",
        r"\b(shutdown|poweroff|reboot|halt)\b",
        r"\bStop-Computer\b",
        r"\bRestart-Computer\b",
        r"\bmkfs(\.\w+)?\b",
        r"\bdd\s+.*\bof\s*=\s*/dev/",
        r"\bdel\s+/[sq]\s+[a-z]:\\?\s*$",
        r"\bRemove-Item\b.*-[Rr]ecurse.*[Cc]:\\",
        r":\(\)\s*\{\s*:\|:&\s*\}\s*;",
        r"\bcipher\s+/w:",
    )
)

# Silent / unattended installer heuristics — not a complete boundary.
# Package managers (winget / brew / apt) are ordinary commands that always confirm.
_SHELL_SILENT_INSTALL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmsiexec\b.*(?:/quiet|/qn\b|/passive\b)",
        r"\bStart-Process\b[\s\S]{0,200}(?:/[Ss]\b|/silent\b|/quiet\b|/qn\b|/verysilent\b)",
        r"\.(?:exe|msi)\b[^\n]{0,120}(?:/[Ss]\b|/silent\b|/verysilent\b|/quiet\b|/qn\b)",
        r"\b/VERYSILENT\b",
        r"\b(?:curl|wget|Invoke-WebRequest)\b[\s\S]{0,160}\.(?:exe|msi)\b",
    )
)

_SHELL_SILENT_INSTALL_REASON = (
    "host 熔断：命令匹配静默安装启发式（msiexec /quiet、Setup /S、"
    "Start-Process quiet 等）。此为启发式兜底，并非完整拦截。"
    "装包请写 winget / brew / apt 的 install 命令，该命令会请人确认。"
)

_ACTION_ENUM = sorted(GUI_ACTIONS | {"shell"})

HOST_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _ACTION_ENUM,
            "description": (
                "省略或 shell = 短命令。attach 选一扇窗；snapshot 读控件树；"
                "click/type 用 snapshot 的 ref。"
            ),
        },
        "command": {
            "type": "string",
            "description": "省略 action 时必填的本机短命令。",
        },
        "ref": {
            "type": "string",
            "description": "click/type：snapshot 返回的控件 ref。",
        },
        "text": {
            "type": "string",
            "description": "type：要填入的文本。密码框硬拒。",
        },
        "snapshot_version": {
            "type": "integer",
            "description": "click/type：该 ref 的 snapshot 版本。",
        },
    },
}


def host_action_name(arguments: dict[str, Any] | None) -> str:
    """Canonical action: ``shell`` when omitted, else the GUI verb or the raw token."""
    raw = str((arguments or {}).get("action") or "").strip().lower()
    if raw in GUI_ACTIONS:
        return raw
    if raw in {"", "shell"}:
        return "shell"
    return raw


def host_call_is_shell(arguments: dict[str, Any] | None) -> bool:
    """True when this host call is the short-command face (fuse / git scan apply)."""
    return host_action_name(arguments) == "shell"


def host_call_is_gui_attach(arguments: dict[str, Any] | None) -> bool:
    return host_action_name(arguments) == "attach"


def host_call_requires_approval(arguments: dict[str, Any] | None) -> bool:
    """Shell commands and attach hit the host axis; snapshot/click/type do not.

    Attach is also always-confirm (session trust cannot eat it).
    """
    action = host_action_name(arguments)
    if action == "attach":
        return True
    if action == "shell":
        return bool(str((arguments or {}).get("command") or "").strip())
    return False


def shell_fuse_blocks(command: str) -> str | None:
    """Return a refusal reason if ``command`` matches a destructive fuse heuristic."""
    text = command.strip()
    if not text:
        return None
    for pat in _SHELL_FUSE_PATTERNS:
        if pat.search(text):
            return (
                "host 熔断：命令匹配毁灭性启发式黑名单（格式化磁盘 / "
                "rm -rf / / shutdown 等）。此为兜底、非完整安全边界；"
                "请缩小命令范围。"
            )
    return None


def shell_silent_install_blocks(command: str) -> str | None:
    """Return a refusal reason if ``command`` looks like a silent arbitrary installer."""
    text = command.strip()
    if not text:
        return None
    for pat in _SHELL_SILENT_INSTALL_PATTERNS:
        if pat.search(text):
            return _SHELL_SILENT_INSTALL_REASON
    return None


# cmd.exe %VAR% — PowerShell does not expand these.
_SHELL_CMD_ENV_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")


def shell_cmd_env_blocks(command: str) -> str | None:
    """Refuse cmd-style ``%VAR%`` env expansion (broken under Windows PowerShell)."""
    if not _SHELL_CMD_ENV_RE.search(command):
        return None
    return (
        "host 在 Windows 上走 PowerShell，不会展开 cmd 风格 %VAR%。"
        "请改用 $env:APPDATA / $env:LOCALAPPDATA / $env:USERPROFILE 等；"
        "Unix 请用 $VAR 或 ${VAR}。"
        "路径含空格时加引号，例如 "
        'Get-ChildItem -LiteralPath "$env:APPDATA\\Microsoft\\Windows"。'
    )


def host_tool_timeout_seconds(arguments: dict[str, Any] | None = None) -> float:
    """Engine wall-clock ceiling for one ``host`` call (must outlive channel + slack)."""
    action = host_action_name(arguments)
    if action in GUI_ACTIONS:
        return host_gui_timeout_seconds(action)
    command = str((arguments or {}).get("command") or "")
    if is_package_install_command(command):
        return float(_PACKAGE_TIMEOUT_DEFAULT) + _PACKAGE_CHANNEL_SLACK_SECONDS
    return float(_SHELL_TIMEOUT_DEFAULT) + _SHELL_CHANNEL_SLACK_SECONDS


def _shell_budget(command: str) -> tuple[int, float]:
    if is_package_install_command(command):
        return _PACKAGE_TIMEOUT_DEFAULT, _PACKAGE_CHANNEL_SLACK_SECONDS
    return _SHELL_TIMEOUT_DEFAULT, _SHELL_CHANNEL_SLACK_SECONDS


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _model_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _process_display(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Reuse the code-exec terminal card when the host op returned a process envelope."""
    if not any(key in payload for key in ("stdout", "stderr", "exit_code")):
        return None
    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    exit_raw = payload.get("exit_code")
    timed_out = bool(payload.get("timed_out"))
    if isinstance(exit_raw, int):
        exit_code = exit_raw
    elif timed_out:
        exit_code = -1
    else:
        exit_code = 0
    display: dict[str, Any] = {
        "stdout": stdout if isinstance(stdout, str) else "",
        "stderr": stderr if isinstance(stderr, str) else "",
        "exit_code": exit_code,
        "language": "host",
    }
    if timed_out:
        display["budget_exceeded"] = True
        display["timeout_kind"] = "idle"
    return display


def _host_display(payload: dict[str, Any]) -> dict[str, Any]:
    process = _process_display(payload)
    if process is not None:
        return process
    return {
        "kind": "host",
        "body": json.dumps(payload, ensure_ascii=False, indent=2),
    }


def _host_result(
    value: Any,
    *,
    success: bool = True,
    error: str | None = None,
) -> ToolResult:
    payload = _as_dict(value)
    return ToolResult(
        tool_call_id="",
        success=success,
        output=_model_json(payload),
        error=error,
        display=_host_display(payload),
    )


def _no_channel_error() -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=(
            "host 需要桌面回填通道：当前无在线桌面客户端，"
            "无法在用户本机跑命令。请如实说明限制，勿假装已查本机。"
        ),
    )


def _fail(error: str, *, contract_failure: bool = False) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        contract_failure=contract_failure,
    )


def _host_shell_transport_args(
    command: str, timeout_seconds: int, context: ToolContext
) -> dict[str, Any]:
    """cwd is runtime-injected; never forward a model-supplied abs path."""
    payload: dict[str, Any] = {
        "command": command,
        "timeout_seconds": timeout_seconds,
        "conversation_id": context.conversation_id or "",
    }
    backend = context.backend
    if getattr(backend, "location", None) != "local":
        return payload
    root = getattr(backend, "root", None)
    if isinstance(root, Path):
        payload["cwd"] = str(root)
    elif isinstance(root, str) and root.strip():
        payload["cwd"] = root.strip()
    channel = getattr(backend, "_channel", None)
    rid = getattr(channel, "root_id", None) if channel is not None else None
    if isinstance(rid, str) and rid:
        payload["root_id"] = rid
    return payload


async def _execute_shell(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    if not command:
        return _fail("host 需要非空 command。", contract_failure=True)
    fuse = shell_fuse_blocks(command)
    if fuse:
        return _fail(fuse)
    silent = shell_silent_install_blocks(command)
    if silent:
        return _fail(silent)
    cmd_env = shell_cmd_env_blocks(command)
    if cmd_env:
        return _fail(cmd_env)
    matched_long = long_running_command_match(command)
    if matched_long is not None:
        return _fail(
            f"禁止用 host 启动长驻进程（检测到：{matched_long}）。"
            "host 有超时上限、不托管后台进程。"
            "请改用 run：同一命令设 background=true。"
            "省略 wait_for 则起来就返回。"
            "用 action=read|list 确认进程仍在跑。"
        )
    timeout_seconds, slack = _shell_budget(command)
    channel = context.desktop_channel
    if channel is None:
        return _no_channel_error()
    logger.info(
        "desktop.host_op_request",
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        op=HostOp.SHELL.value,
        timeout_seconds=timeout_seconds,
    )
    try:
        value = await channel.request_host(
            HostOp.SHELL,
            _host_shell_transport_args(command, timeout_seconds, context),
            timeout=float(timeout_seconds) + slack,
        )
    except HostOpError as e:
        return _fail(str(e))
    return _host_result(value)


class HostTool:
    """本机 Host 面：短命令，或附着窗口后的控件树 ref 派。"""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        host_class=True,
        catalog_summary="这台电脑。",
        blurb="在这台电脑上跑短命令或操作窗口",
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="host",
            description="这台电脑上的短命令；或附着一扇窗后读控件并按 ref 点按/键入。",
            parameters=HOST_TOOL_PARAMETERS,
            face=ToolFace.HOST_BROWSER,
            approval=ToolApproval.NEVER,
            timeout_seconds=None,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = host_action_name(arguments)
        if action == "shell":
            return await _execute_shell(arguments, context)
        if action in GUI_ACTIONS:
            return await execute_gui(action, arguments, context)
        return _fail(
            "host 的 action 只能是 attach / snapshot / click / type；省略则是短命令。",
            contract_failure=True,
        )
