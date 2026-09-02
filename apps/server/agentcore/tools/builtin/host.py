"""Single builtin ``host`` — observe / assist the user's machine via desktop backfill.

Orthogonal to Workspace / Browser. Transport is ``DesktopClientChannel.request_host``
(ClientTool SSE); HostOp enum values are unchanged. Model surface is one tool with
an ``action`` policy table (schema ``NEVER``, runtime elevation — same posture as
``git`` / ``run``). ``host_ping`` is transport-only and is not registered.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.desktop.channel import HostOp, HostOpError
from agentcore.tools.builtin.long_running import long_running_command_match
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

# Frozen action set (docs/03-AI核心/工具与能力系统.md §四B).
_ACTION_STATUS = "status"
_ACTION_OS_LOG = "os_log"
_ACTION_SHELL = "shell"
_ACTION_OPEN_SETTINGS = "open_settings"
_ACTION_SET_AUDIO = "set_audio"
_ACTION_RESTART_SERVICE = "restart_service"
_ACTION_INSTALL_PACKAGE = "install_package"

_ALLOWED_ACTIONS = frozenset(
    {
        _ACTION_STATUS,
        _ACTION_OS_LOG,
        _ACTION_SHELL,
        _ACTION_OPEN_SETTINGS,
        _ACTION_SET_AUDIO,
        _ACTION_RESTART_SERVICE,
        _ACTION_INSTALL_PACKAGE,
    }
)
_NEVER_APPROVE_ACTIONS = frozenset({_ACTION_STATUS, _ACTION_OS_LOG})
_APPROVAL_ACTIONS = _ALLOWED_ACTIONS - _NEVER_APPROVE_ACTIONS

# L1 host_os_log_summary hard caps (desktop clamps again; keep in lockstep).
_OS_LOG_MINUTES_DEFAULT = 60
_OS_LOG_MINUTES_MAX = 1440
_OS_LOG_ENTRIES_DEFAULT = 40
_OS_LOG_ENTRIES_MAX = 80
_OS_LOG_BYTES_DEFAULT = 24_000
_OS_LOG_BYTES_MAX = 48_000
_OS_LOG_LEVELS = frozenset({"error", "warning", "info", "any"})
_OS_LOG_SOURCE_MAX = 120

# L2 panel whitelist — closed set (安全权限与治理 / Host 定案 P1).
_OPEN_SETTINGS_PANELS = frozenset({"sound", "display", "network", "apps", "about"})

# L3 service-name whitelist — closed set (Host 定案 P2；禁任意 sc).
# Canonical SCM name only; do not expand without architecture sign-off.
_SERVICE_RESTART_ALLOWLIST = frozenset({"audiosrv"})

# L3 package managers — closed set (桶4 · 点名包；否决任意 exe 静默装).
_PACKAGE_MANAGERS = frozenset({"winget", "brew", "apt"})
_PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-/@]{0,199}$")

# action=shell: optional timeout clamp (seconds). Desktop kills the process at this budget.
_SHELL_TIMEOUT_DEFAULT = 60
_SHELL_TIMEOUT_MAX = 120
_SHELL_CHANNEL_SLACK_SECONDS = 15.0

# action=install_package: Docker Desktop / VS Code installs often exceed shell 120s.
_PACKAGE_TIMEOUT_DEFAULT = 600
_PACKAGE_TIMEOUT_MAX = 900
_PACKAGE_CHANNEL_SLACK_SECONDS = 30.0

# status facets → (HostOp, today's per-op engine ceiling). Ping / os_log excluded.
_STATUS_FACET_ORDER: tuple[str, ...] = (
    "info",
    "audio_devices",
    "storage",
    "power",
    "network_summary",
    "apps",
)
_STATUS_FACETS: dict[str, tuple[HostOp, float]] = {
    "info": (HostOp.INFO, 20.0),
    "audio_devices": (HostOp.AUDIO_DEVICES, 30.0),
    "storage": (HostOp.STORAGE, 30.0),
    "power": (HostOp.POWER, 20.0),
    "network_summary": (HostOp.NETWORK_SUMMARY, 20.0),
    "apps": (HostOp.APPS, 45.0),
}
_ACTION_TIMEOUTS: dict[str, float] = {
    _ACTION_OS_LOG: 45.0,
    _ACTION_OPEN_SETTINGS: 30.0,
    _ACTION_SET_AUDIO: 45.0,
    _ACTION_RESTART_SERVICE: 60.0,
}

# Heuristic fuse — not a complete security boundary (Host 定案 P3).
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

# Silent / unattended installer heuristics — not a complete boundary (桶4).
# Keep in rough lockstep with desktop ``shellSilentInstallBlocks``.
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
    "host(action=shell) 熔断：命令匹配静默安装启发式（msiexec /quiet、Setup /S、"
    "Start-Process quiet 等）。此为启发式兜底，并非完整拦截；"
    "请改用 host(action=install_package)（manager∈winget/brew/apt + package id）。"
)

HOST_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": sorted(_ALLOWED_ACTIONS),
            "description": (
                "status / os_log / shell / open_settings / set_audio / "
                "restart_service / install_package。"
            ),
        },
        "facets": {
            "type": "array",
            "items": {"type": "string", "enum": list(_STATUS_FACET_ORDER)},
            "description": (
                "status 可选投影（info/audio_devices/storage/power/network_summary/apps）；"
                "默认全要。不含 ping、不含 os_log。"
            ),
        },
        "source": {
            "type": "string",
            "description": (
                "os_log 可选：来源/应用/Provider 子串过滤（如 Application、docker）；"
                f"最长 {_OS_LOG_SOURCE_MAX}。"
            ),
        },
        "level": {
            "type": "string",
            "enum": sorted(_OS_LOG_LEVELS),
            "description": "os_log：最低关注级别 error / warning（默认，含 error）/ info / any。",
        },
        "minutes": {
            "type": "integer",
            "description": (
                f"os_log 回看分钟（默认 {_OS_LOG_MINUTES_DEFAULT}，上限 {_OS_LOG_MINUTES_MAX}）。"
            ),
            "minimum": 1,
            "maximum": _OS_LOG_MINUTES_MAX,
        },
        "max_entries": {
            "type": "integer",
            "description": (
                f"os_log 最多返回条数（默认 {_OS_LOG_ENTRIES_DEFAULT}，"
                f"硬上限 {_OS_LOG_ENTRIES_MAX}）。"
            ),
            "minimum": 1,
            "maximum": _OS_LOG_ENTRIES_MAX,
        },
        "max_bytes": {
            "type": "integer",
            "description": (
                f"os_log 摘要载荷字节硬上限（默认 {_OS_LOG_BYTES_DEFAULT}，"
                f"硬上限 {_OS_LOG_BYTES_MAX}）。"
            ),
            "minimum": 1024,
            "maximum": _OS_LOG_BYTES_MAX,
        },
        "command": {
            "type": "string",
            "description": (
                "shell 本机短时命令（非空）；cwd 由运行时设为已授权根（默认工作区根）。"
                "Windows 写 PowerShell（$env:APPDATA、'; if；禁 %VAR%/||/&&）；"
                "Unix 写 POSIX（$SHELL -lc）。"
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": (
                f"shell 默认 {_SHELL_TIMEOUT_DEFAULT}、上限 {_SHELL_TIMEOUT_MAX}；"
                f"install_package 默认 {_PACKAGE_TIMEOUT_DEFAULT}、上限 {_PACKAGE_TIMEOUT_MAX}。"
            ),
        },
        "panel": {
            "type": "string",
            "enum": sorted(_OPEN_SETTINGS_PANELS),
            "description": "open_settings：sound|display|network|apps|about。",
        },
        "device_id": {
            "type": "string",
            "description": (
                "set_audio：设备 id（与 status 音频设备返回的 id 一致）。"
                "须先 status 观测设备。"
            ),
        },
        "device_name": {
            "type": "string",
            "description": "set_audio：设备友好名（与 status 音频设备返回的 name 一致）。",
        },
        "service": {
            "type": "string",
            "description": "restart_service：服务名（SCM name）；当前仅允许 Audiosrv。",
            "enum": ["Audiosrv"],
        },
        "manager": {
            "type": "string",
            "enum": sorted(_PACKAGE_MANAGERS),
            "description": "install_package：winget（Win）/ brew（macOS·Linux）/ apt（Linux）。",
        },
        "package_id": {
            "type": "string",
            "description": (
                "install_package：包管理器点名 id，例如 Microsoft.VisualStudioCode、"
                "Docker.DockerDesktop、visual-studio-code、docker.io。"
            ),
        },
        "cask": {
            "type": "boolean",
            "description": "install_package 仅 brew：true 时用 brew install --cask（GUI 应用）。",
        },
    },
    "required": ["action"],
}


def host_action_name(arguments: dict[str, Any] | None) -> str:
    """Normalized ``action`` (empty when missing)."""
    return str((arguments or {}).get("action") or "").strip().lower()


def host_call_is_shell(arguments: dict[str, Any] | None) -> bool:
    return host_action_name(arguments) == _ACTION_SHELL


def host_call_requires_approval(arguments: dict[str, Any] | None) -> bool:
    """Runtime elevation: host-axis actions + install_package (status/os_log skip)."""
    return host_action_name(arguments) in _APPROVAL_ACTIONS


def shell_fuse_blocks(command: str) -> str | None:
    """Return a refusal reason if ``command`` matches a destructive fuse heuristic."""
    text = command.strip()
    if not text:
        return None
    for pat in _SHELL_FUSE_PATTERNS:
        if pat.search(text):
            return (
                "host(action=shell) 熔断：命令匹配毁灭性启发式黑名单（格式化磁盘 / "
                "rm -rf / / shutdown 等）。此为兜底、非完整安全边界；"
                "请改用更安全的结构化 host action 或缩小命令范围。"
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


def clamp_package_timeout(raw: Any) -> int:
    """Parse optional timeout_seconds for package install; default 600, clamp to [60, 900]."""
    if raw is None or raw == "":
        return _PACKAGE_TIMEOUT_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _PACKAGE_TIMEOUT_DEFAULT
    return max(60, min(_PACKAGE_TIMEOUT_MAX, value))


def validate_package_install_args(
    *,
    manager: str,
    package_id: str,
    cask: bool = False,
) -> str | None:
    """Return an error string if manager / package id are invalid; else None."""
    mgr = manager.strip().lower()
    if mgr not in _PACKAGE_MANAGERS:
        return (
            f"host(action=install_package) 不支持 manager={manager!r}；"
            f"仅允许：{', '.join(sorted(_PACKAGE_MANAGERS))}。"
        )
    pkg = package_id.strip()
    if not pkg or not _PACKAGE_ID_RE.fullmatch(pkg):
        return (
            "host(action=install_package) 需要合法 package_id（字母数字开头，"
            "可含 ._+-/@，最长 200；禁空格与 shell 元字符）。"
        )
    if cask and mgr != "brew":
        return "host(action=install_package) 的 cask=true 仅适用于 manager=brew。"
    return None


# cmd.exe %VAR% — PowerShell does not expand these (prod thrash: %APPDATA% → NOT_FOUND).
_SHELL_CMD_ENV_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")


def shell_cmd_env_blocks(command: str) -> str | None:
    """Refuse cmd-style ``%VAR%`` env expansion (broken under Windows PowerShell)."""
    if not _SHELL_CMD_ENV_RE.search(command):
        return None
    return (
        "host(action=shell) 在 Windows 上走 PowerShell，不会展开 cmd 风格 %VAR%。"
        "请改用 $env:APPDATA / $env:LOCALAPPDATA / $env:USERPROFILE 等；"
        "Unix 请用 $VAR 或 ${VAR}。"
        "路径含空格时加引号，例如 "
        "Get-ChildItem -LiteralPath \"$env:APPDATA\\Microsoft\\Windows\"。"
    )


def clamp_shell_timeout(raw: Any) -> int:
    """Parse optional timeout_seconds; default 60, clamp to [1, 120]."""
    if raw is None or raw == "":
        return _SHELL_TIMEOUT_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _SHELL_TIMEOUT_DEFAULT
    return max(1, min(_SHELL_TIMEOUT_MAX, value))


def _clamp_os_log_int(raw: Any, *, default: int, lo: int, hi: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def normalize_os_log_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Clamp / default os_log args (server-side; desktop reclamps)."""
    source = str(arguments.get("source") or "").strip()[:_OS_LOG_SOURCE_MAX]
    raw_level = str(arguments.get("level") or "warning").strip().lower()
    level = raw_level if raw_level in _OS_LOG_LEVELS else "warning"
    return {
        "source": source,
        "level": level,
        "minutes": _clamp_os_log_int(
            arguments.get("minutes"),
            default=_OS_LOG_MINUTES_DEFAULT,
            lo=1,
            hi=_OS_LOG_MINUTES_MAX,
        ),
        "max_entries": _clamp_os_log_int(
            arguments.get("max_entries"),
            default=_OS_LOG_ENTRIES_DEFAULT,
            lo=1,
            hi=_OS_LOG_ENTRIES_MAX,
        ),
        "max_bytes": _clamp_os_log_int(
            arguments.get("max_bytes"),
            default=_OS_LOG_BYTES_DEFAULT,
            lo=1024,
            hi=_OS_LOG_BYTES_MAX,
        ),
    }


def normalize_status_facets(raw: Any) -> tuple[list[str], str | None]:
    """Ordered unique status facets; default all. Error string when invalid."""
    if raw is None or raw == "" or raw == []:
        return list(_STATUS_FACET_ORDER), None
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return [], (
            "facets 须为字符串数组"
            f"（{'/'.join(_STATUS_FACET_ORDER)}）。"
        )
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = str(item).strip().lower()
        if name not in _STATUS_FACETS:
            return [], (
                f"未知 facets={item!r}；仅允许：{', '.join(_STATUS_FACET_ORDER)}。"
            )
        if name not in seen:
            seen.add(name)
            out.append(name)
    return (out or list(_STATUS_FACET_ORDER)), None


def host_tool_timeout_seconds(arguments: dict[str, Any] | None = None) -> float:
    """Engine wall-clock ceiling for one ``host`` call (must outlive channel + slack)."""
    args = arguments or {}
    action = host_action_name(args)
    if action == _ACTION_SHELL:
        return float(clamp_shell_timeout(args.get("timeout_seconds"))) + (
            _SHELL_CHANNEL_SLACK_SECONDS
        )
    if action == _ACTION_INSTALL_PACKAGE:
        return float(clamp_package_timeout(args.get("timeout_seconds"))) + (
            _PACKAGE_CHANNEL_SLACK_SECONDS
        )
    if action == _ACTION_STATUS:
        facets, err = normalize_status_facets(args.get("facets"))
        if err or not facets:
            return _STATUS_FACETS["apps"][1]
        return max(_STATUS_FACETS[name][1] for name in facets)
    return _ACTION_TIMEOUTS.get(action, _STATUS_FACETS["apps"][1])


def _untrusted(payload: dict[str, Any]) -> str:
    """Frame Host probe results as untrusted OS-reported facts (禁催密码)."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"<不可信内容>\n{body}\n</不可信内容>"


def _no_channel_error() -> ToolResult:
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=(
            "host 需要桌面回填通道：当前无在线桌面客户端，"
            "无法观测或操作用户本机。请如实说明限制，勿假装已查本机。"
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


async def _host_call(
    context: ToolContext,
    *,
    op: HostOp,
    args: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> ToolResult:
    channel = context.desktop_channel
    if channel is None:
        return _no_channel_error()
    logger.info(
        "desktop.host_op_request",
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        op=op.value,
    )
    try:
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        value = await channel.request_host(op, args or {}, **kwargs)
    except HostOpError as e:
        return ToolResult(
            tool_call_id="",
            success=False,
            output="",
            error=str(e),
        )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_untrusted(value),
    )


async def _execute_status(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    facets, err = normalize_status_facets(arguments.get("facets"))
    if err:
        return _fail(err, contract_failure=True)
    channel = context.desktop_channel
    if channel is None:
        return _no_channel_error()

    async def _one(facet: str) -> tuple[str, dict[str, Any]]:
        op, facet_timeout = _STATUS_FACETS[facet]
        logger.info(
            "desktop.host_op_request",
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            op=op.value,
        )
        try:
            value = await channel.request_host(op, {}, timeout=facet_timeout)
        except HostOpError as e:
            return facet, {"error": str(e)}
        if isinstance(value, dict):
            return facet, value
        return facet, {"value": value}

    pairs = await asyncio.gather(*(_one(facet) for facet in facets))
    payload = {facet: value for facet, value in pairs}
    failed = [
        f"{facet}: {value['error']}"
        for facet, value in pairs
        if isinstance(value, dict) and value.get("error")
    ]
    if failed and len(failed) == len(pairs):
        return ToolResult(
            tool_call_id="",
            success=False,
            output=_untrusted(payload),
            error="; ".join(failed),
        )
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_untrusted(payload),
    )


async def _execute_os_log(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    args = normalize_os_log_args(arguments)
    payload = {k: v for k, v in args.items() if not (k == "source" and v == "")}
    return await _host_call(
        context,
        op=HostOp.OS_LOG_SUMMARY,
        args=payload,
        timeout=_ACTION_TIMEOUTS[_ACTION_OS_LOG],
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
        return _fail("host(action=shell) 需要非空 command。", contract_failure=True)
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
            f"禁止用 host(action=shell) 启动长驻进程（检测到：{matched_long}）。"
            "host(action=shell) 有超时上限、不托管后台进程。"
            "请改用 run：同一命令设 background=true。"
            "省略 wait_for 时用默认就绪信号；命中前不得宣称已启动。"
            "用 action=read|list 确认进程仍在跑。"
        )
    timeout_seconds = clamp_shell_timeout(arguments.get("timeout_seconds"))
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
            timeout=float(timeout_seconds) + _SHELL_CHANNEL_SLACK_SECONDS,
        )
    except HostOpError as e:
        return _fail(str(e))
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_untrusted(value),
    )


async def _execute_open_settings(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    panel = str(arguments.get("panel") or "").strip().lower()
    if panel not in _OPEN_SETTINGS_PANELS:
        return _fail(
            f"host(action=open_settings) 不支持 panel={panel!r}；"
            f"仅允许：{', '.join(sorted(_OPEN_SETTINGS_PANELS))}。",
            contract_failure=True,
        )
    return await _host_call(
        context,
        op=HostOp.OPEN_SETTINGS,
        args={"panel": panel},
        timeout=_ACTION_TIMEOUTS[_ACTION_OPEN_SETTINGS],
    )


async def _execute_set_audio(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    device_id = str(arguments.get("device_id") or "").strip()
    device_name = str(arguments.get("device_name") or "").strip()
    if not device_id and not device_name:
        return _fail(
            "host(action=set_audio) 需要 device_id 和/或 device_name；"
            "请先 host(action=status) 观测音频设备后再指定。",
            contract_failure=True,
        )
    args: dict[str, Any] = {}
    if device_id:
        args["device_id"] = device_id
    if device_name:
        args["device_name"] = device_name
    return await _host_call(
        context,
        op=HostOp.AUDIO_SET_DEFAULT,
        args=args,
        timeout=_ACTION_TIMEOUTS[_ACTION_SET_AUDIO],
    )


async def _execute_restart_service(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    service = str(arguments.get("service") or "").strip()
    if service.lower() not in _SERVICE_RESTART_ALLOWLIST:
        return _fail(
            f"host(action=restart_service) 拒绝服务名 {service!r}；"
            "仅允许极短白名单：Audiosrv。",
            contract_failure=True,
        )
    return await _host_call(
        context,
        op=HostOp.SERVICE_RESTART,
        args={"service": "Audiosrv"},
        timeout=_ACTION_TIMEOUTS[_ACTION_RESTART_SERVICE],
    )


async def _execute_install_package(
    arguments: dict[str, Any], context: ToolContext
) -> ToolResult:
    manager = str(arguments.get("manager") or "").strip()
    package_id = str(arguments.get("package_id") or "").strip()
    cask = bool(arguments.get("cask"))
    invalid = validate_package_install_args(
        manager=manager, package_id=package_id, cask=cask
    )
    if invalid:
        return _fail(invalid, contract_failure=True)
    timeout_seconds = clamp_package_timeout(arguments.get("timeout_seconds"))
    channel = context.desktop_channel
    if channel is None:
        return _no_channel_error()
    args: dict[str, Any] = {
        "manager": manager.strip().lower(),
        "package_id": package_id.strip(),
        "timeout_seconds": timeout_seconds,
    }
    if cask:
        args["cask"] = True
    logger.info(
        "desktop.host_op_request",
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        op=HostOp.PACKAGE_INSTALL.value,
        manager=args["manager"],
        package_id=args["package_id"],
        timeout_seconds=timeout_seconds,
    )
    try:
        value = await channel.request_host(
            HostOp.PACKAGE_INSTALL,
            args,
            timeout=float(timeout_seconds) + _PACKAGE_CHANNEL_SLACK_SECONDS,
        )
    except HostOpError as e:
        return _fail(str(e))
    return ToolResult(
        tool_call_id="",
        success=True,
        output=_untrusted(value),
    )


class HostTool:
    """本机 Host 面：单一 ``host`` + ``action`` 政策表。"""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
        host_class=True,
    )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="host",
            description=(
                "本机 Host（仅桌面回填通道；与 folder/bind 正交）。"
                "schema 免批；status/os_log 运行时免批；其余走 host 轴；"
                "install_package 恒确认（session/kickoff/turn grant 不覆盖；"
                "不吃 kickoff/command=auto）。"
                "HOW→consult(host)。"
            ),
            parameters=HOST_TOOL_PARAMETERS,
            category=ToolCategory.INTERACTION,
            approval=ToolApproval.NEVER,
            timeout_seconds=None,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = host_action_name(arguments)
        if not action:
            return _fail("action 为必填参数", contract_failure=True)
        if action not in _ALLOWED_ACTIONS:
            return _fail(
                f"action '{action}' 不在允许列表中："
                f"{', '.join(sorted(_ALLOWED_ACTIONS))}。",
                contract_failure=True,
            )

        if action == _ACTION_STATUS:
            return await _execute_status(arguments, context)
        if action == _ACTION_OS_LOG:
            return await _execute_os_log(arguments, context)
        if action == _ACTION_SHELL:
            return await _execute_shell(arguments, context)
        if action == _ACTION_OPEN_SETTINGS:
            return await _execute_open_settings(arguments, context)
        if action == _ACTION_SET_AUDIO:
            return await _execute_set_audio(arguments, context)
        if action == _ACTION_RESTART_SERVICE:
            return await _execute_restart_service(arguments, context)
        return await _execute_install_package(arguments, context)
