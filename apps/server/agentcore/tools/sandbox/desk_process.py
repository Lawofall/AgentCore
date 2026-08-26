"""Long-running processes inside a cloud-desk guest.

``code_execute`` / ``test_run`` hold a gVisor exec slot until the command exits.
A dev server never exits, so ``terminal`` must not take that path: a short
allowlisted ``bash /scratch/…`` script backgrounds the payload and returns.
Logs and pid files live on the host runtime bind (``/scratch/proc/…``), not
the workspace tree — they are not deliverables.

Ledger is in-process and keyed by ``conversation_id`` (same Folder shares the
disk, not one vite). After API restart the ledger is gone: unknown ids fail
honestly instead of being reconstructed from leftover files.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_LOG_READ = 1_000_000
_POLL_SECONDS = 0.15
_DEFAULT_TAIL_LINES = 80
_LAUNCH_TIMEOUT_SECONDS = 15.0

CLOUD_DESK_REQUIRED = "cloud_desk_required"
PROCESS_NOT_REGISTERED = "process_not_registered"
_WORKSPACE_IO = "workspace_io_error"


class DeskProcessError(Exception):
    """Typed failure from the cloud-desk process face."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        contract_failure: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.contract_failure = contract_failure


@dataclass
class _Record:
    process_id: str
    conversation_id: str
    desk_key: str
    command: str
    name: str | None
    cwd: str
    status: str
    started_at: str
    exit_code: int | None
    host_dir: Path
    guest_dir: str


_records: dict[str, _Record] = {}
_lock = asyncio.Lock()


def reset_desk_processes_for_tests() -> None:
    """Drop the in-process ledger (unit tests). Does not talk to sandboxd."""
    _records.clear()


def drop_processes_for_desk_keys(keys: list[str] | tuple[str, ...]) -> int:
    """Forget ledger rows for desks being torn down. Guest kill reaps pids."""
    wanted = {str(key) for key in keys}
    dropped = [pid for pid, rec in list(_records.items()) if rec.desk_key in wanted]
    for pid in dropped:
        _records.pop(pid, None)
    if dropped:
        logger.info(
            "sandbox.desk_processes_dropped",
            count=len(dropped),
            desks=len(wanted),
        )
    return len(dropped)


def desk_has_running_process(desk_key: str) -> bool:
    """True when this workspace desk has a ledger row still marked running."""
    key = str(desk_key)
    return any(rec.desk_key == key and rec.status == "running" for rec in _records.values())


def _safe_id(raw: str) -> str:
    cleaned = _SAFE_ID.sub("_", (raw or "").strip())[:80]
    return cleaned or "unknown"


def _desk_key(workspace: str) -> str:
    return str(Path(workspace).resolve())


def _guest_dir(conversation_id: str, process_id: str) -> str:
    return f"/scratch/proc/{_safe_id(conversation_id)}/{_safe_id(process_id)}"


def _tail_log(path: Path, tail_lines: int) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > _MAX_LOG_READ:
            handle.seek(size - _MAX_LOG_READ)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    limit = tail_lines if tail_lines > 0 else _DEFAULT_TAIL_LINES
    if len(lines) > limit:
        return "\n".join(lines[-limit:])
    return "\n".join(lines)


def _compile_wait_for(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise DeskProcessError(
            f"wait_for 不是合法正则：{exc}",
            code="VALIDATION_ERROR",
            contract_failure=True,
        ) from exc


def _snapshot(rec: _Record, *, output: str = "", matched: bool | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "process_id": rec.process_id,
        "status": rec.status,
        "command": rec.command,
        "started_at": rec.started_at,
    }
    if rec.name:
        value["name"] = rec.name
    if rec.cwd:
        value["cwd"] = rec.cwd
    if rec.exit_code is not None:
        value["exit_code"] = rec.exit_code
    if output or matched is not None:
        value["output"] = output
    if matched is not None:
        value["matched"] = matched
    return value


def _gvisor(backend: object) -> Any:
    from agentcore.tools.sandbox.gvisor import GVisorSandbox

    sandbox = getattr(backend, "_sandbox", None)
    if isinstance(sandbox, GVisorSandbox):
        return sandbox
    return None


def _workspace_root(backend: object) -> Path | None:
    root = getattr(backend, "root", None)
    return root if isinstance(root, Path) else None


def _resolve_guest_cwd(backend: object, cwd: str) -> str:
    root = _workspace_root(backend)
    if root is None:
        raise DeskProcessError(
            "云端长驻进程需要已挂载的工作区盘。",
            code=CLOUD_DESK_REQUIRED,
        )
    rel = (cwd or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return "/workspace"
    from agentcore.workspace._paths import resolve_safe_path

    resolved = resolve_safe_path(root, rel, root_label=getattr(backend, "root_label", None))
    if resolved is None or not resolved.is_dir():
        raise DeskProcessError(
            f"cwd 不存在或不在工作区内：{cwd}",
            code=_WORKSPACE_IO,
        )
    posix = resolved.resolve().relative_to(root.resolve()).as_posix()
    return "/workspace" if posix == "." else f"/workspace/{posix}"


def _launch_script(*, guest_dir: str, guest_cwd: str) -> str:
    log_q = shlex.quote(f"{guest_dir}/log")
    pid_q = shlex.quote(f"{guest_dir}/pid")
    cmd_q = shlex.quote(f"{guest_dir}/command.sh")
    cwd_q = shlex.quote(guest_cwd)
    return (
        "set -eu\n"
        f"LOG={log_q}\n"
        f"PIDF={pid_q}\n"
        f"CMD={cmd_q}\n"
        ': > "$LOG"\n'
        f"cd {cwd_q}\n"
        'setsid nohup bash "$CMD" >>"$LOG" 2>&1 < /dev/null &\n'
        'echo $! > "$PIDF"\n'
    )


def _stop_script(*, guest_dir: str) -> str:
    pid_q = shlex.quote(f"{guest_dir}/pid")
    return (
        "set +e\n"
        f"PIDF={pid_q}\n"
        '[ -f "$PIDF" ] || exit 0\n'
        'pid=$(cat "$PIDF")\n'
        '[ -n "$pid" ] || exit 0\n'
        'kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null\n'
        'kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null\n'
        "exit 0\n"
    )


def _alive_script(*, guest_dir: str) -> str:
    pid_q = shlex.quote(f"{guest_dir}/pid")
    return (
        "set +e\n"
        f"PIDF={pid_q}\n"
        '[ -f "$PIDF" ] || { echo dead; exit 0; }\n'
        'pid=$(cat "$PIDF")\n'
        '[ -n "$pid" ] || { echo dead; exit 0; }\n'
        'kill -0 "$pid" 2>/dev/null && echo alive || echo dead\n'
        "exit 0\n"
    )


async def _short_bash(
    sandbox: Any,
    workspace: str,
    *,
    host_dir: Path,
    guest_dir: str,
    filename: str,
    body: str,
    cache_bucket: str | None,
) -> tuple[int, str, str]:
    host_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / filename).write_text(body, encoding="utf-8", newline="\n")
    return await sandbox.short_exec_script(
        workspace,
        guest_script=f"{guest_dir}/{filename}",
        timeout_seconds=_LAUNCH_TIMEOUT_SECONDS,
        cache_bucket=cache_bucket,
    )


async def _poll_log(
    path: Path,
    pattern: re.Pattern[str],
    *,
    timeout_seconds: float,
) -> tuple[bool, str]:
    deadline = time.monotonic() + max(timeout_seconds, 1.0)
    tail_lines = _DEFAULT_TAIL_LINES
    while True:
        output = _tail_log(path, tail_lines)
        if pattern.search(output):
            return True, output
        if time.monotonic() >= deadline:
            return False, output
        await asyncio.sleep(_POLL_SECONDS)


async def _mark_dead_if_needed(
    sandbox: Any,
    rec: _Record,
    workspace: str,
    cache_bucket: str | None,
) -> None:
    if rec.status != "running":
        return
    code, stdout, _err = await _short_bash(
        sandbox,
        workspace,
        host_dir=rec.host_dir,
        guest_dir=rec.guest_dir,
        filename="alive.sh",
        body=_alive_script(guest_dir=rec.guest_dir),
        cache_bucket=cache_bucket,
    )
    if code != 0:
        return
    tokens = (stdout or "").split()
    if "dead" in tokens and "alive" not in tokens:
        rec.status = "exited"
        rec.exit_code = rec.exit_code if rec.exit_code is not None else -1


async def start_desk_process(
    backend: object,
    *,
    conversation_id: str,
    command: str,
    cwd: str = "",
    name: str = "",
    wait_for: str = "",
    wait_timeout_seconds: float = 30.0,
    cache_bucket: str | None = None,
) -> dict[str, Any]:
    """Background ``command`` in the desk guest; return as soon as launch.sh exits."""
    conv = (conversation_id or "").strip()
    if not conv:
        raise DeskProcessError(
            "云端长驻进程按对话记账，缺少 conversation_id。",
            code="VALIDATION_ERROR",
            contract_failure=True,
        )
    sandbox = _gvisor(backend)
    if sandbox is None:
        raise DeskProcessError(
            "云端长驻进程需要云桌 guest，当前执行环境无法托管后台进程。",
            code=CLOUD_DESK_REQUIRED,
        )
    root = _workspace_root(backend)
    if root is None:
        raise DeskProcessError(
            "云端长驻进程需要已挂载的工作区盘。",
            code=CLOUD_DESK_REQUIRED,
        )
    workspace = str(root.resolve())
    guest_cwd = _resolve_guest_cwd(backend, cwd)
    process_id = f"tp-{uuid.uuid4().hex[:12]}"
    guest_dir = _guest_dir(conv, process_id)
    await sandbox.ensure_workspace_desk(workspace, cache_bucket=cache_bucket)
    from agentcore.tools.sandbox.gvisor import touch_workspace_desk

    touch_workspace_desk(workspace)
    scratch = sandbox.host_scratch_dir(workspace)
    if scratch is None:
        raise DeskProcessError(
            "云桌 guest 未能提供运行时目录。",
            code=CLOUD_DESK_REQUIRED,
        )
    host_dir = scratch / "proc" / _safe_id(conv) / _safe_id(process_id)
    host_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / "command.sh").write_text(
        command if command.endswith("\n") else command + "\n",
        encoding="utf-8",
        newline="\n",
    )
    code, _out, err = await _short_bash(
        sandbox,
        workspace,
        host_dir=host_dir,
        guest_dir=guest_dir,
        filename="launch.sh",
        body=_launch_script(guest_dir=guest_dir, guest_cwd=guest_cwd),
        cache_bucket=cache_bucket,
    )
    if code != 0:
        raise DeskProcessError(
            (err or _out or "启动脚本未能在云桌 guest 里执行。").strip()
            or "启动脚本未能在云桌 guest 里执行。",
            code=_WORKSPACE_IO,
        )
    pid_path = host_dir / "pid"
    if not pid_path.is_file() or not pid_path.read_text(encoding="utf-8").strip():
        raise DeskProcessError(
            "启动脚本已返回但没有记下进程号，未登记为存活。",
            code=_WORKSPACE_IO,
        )
    rec = _Record(
        process_id=process_id,
        conversation_id=conv,
        desk_key=_desk_key(workspace),
        command=command,
        name=(name or "").strip() or None,
        cwd=(cwd or "").strip(),
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        exit_code=None,
        host_dir=host_dir,
        guest_dir=guest_dir,
    )
    async with _lock:
        _records[process_id] = rec
    logger.info(
        "sandbox.desk_process_started",
        process_id=process_id,
        conversation_id=conv,
        workspace=rec.desk_key,
    )
    log_path = host_dir / "log"
    matched: bool | None = None
    if wait_for:
        matcher = _compile_wait_for(wait_for)
        matched, output = await _poll_log(
            log_path, matcher, timeout_seconds=wait_timeout_seconds
        )
    else:
        output = _tail_log(log_path, _DEFAULT_TAIL_LINES)
    mark = getattr(backend, "_mark_mutated", None)
    if callable(mark):
        mark()
    return _snapshot(rec, output=output, matched=matched)


async def read_desk_process(
    backend: object,
    *,
    conversation_id: str,
    process_id: str,
    wait_for: str = "",
    wait_timeout_seconds: float = 30.0,
    tail_lines: int = _DEFAULT_TAIL_LINES,
    cache_bucket: str | None = None,
) -> dict[str, Any]:
    rec = await _require_record(conversation_id, process_id)
    sandbox = _gvisor(backend)
    root = _workspace_root(backend)
    if sandbox is None or root is None:
        raise DeskProcessError(
            "云端长驻进程需要云桌 guest，当前执行环境无法读取进程。",
            code=CLOUD_DESK_REQUIRED,
        )
    workspace = str(root.resolve())
    from agentcore.tools.sandbox.gvisor import touch_workspace_desk

    touch_workspace_desk(workspace)
    await _mark_dead_if_needed(sandbox, rec, workspace, cache_bucket)
    log_path = rec.host_dir / "log"
    matched: bool | None = None
    if wait_for:
        matcher = _compile_wait_for(wait_for)
        matched, output = await _poll_log(
            log_path, matcher, timeout_seconds=wait_timeout_seconds
        )
    else:
        output = _tail_log(log_path, tail_lines)
    return _snapshot(rec, output=output, matched=matched)


async def stop_desk_process(
    backend: object,
    *,
    conversation_id: str,
    process_id: str,
    cache_bucket: str | None = None,
) -> dict[str, Any]:
    rec = await _require_record(conversation_id, process_id)
    sandbox = _gvisor(backend)
    root = _workspace_root(backend)
    if sandbox is None or root is None:
        raise DeskProcessError(
            "云端长驻进程需要云桌 guest，当前执行环境无法停止进程。",
            code=CLOUD_DESK_REQUIRED,
        )
    workspace = str(root.resolve())
    from agentcore.tools.sandbox.gvisor import touch_workspace_desk

    touch_workspace_desk(workspace)
    await _short_bash(
        sandbox,
        workspace,
        host_dir=rec.host_dir,
        guest_dir=rec.guest_dir,
        filename="stop.sh",
        body=_stop_script(guest_dir=rec.guest_dir),
        cache_bucket=cache_bucket,
    )
    rec.status = "exited"
    rec.exit_code = rec.exit_code if rec.exit_code is not None else -1
    logger.info(
        "sandbox.desk_process_stopped",
        process_id=rec.process_id,
        conversation_id=rec.conversation_id,
    )
    return _snapshot(rec)


async def list_desk_processes(*, conversation_id: str) -> dict[str, Any]:
    conv = (conversation_id or "").strip()
    async with _lock:
        rows = [rec for rec in _records.values() if rec.conversation_id == conv]
    processes = [_snapshot(rec) for rec in rows]
    return {"processes": processes}


async def _require_record(conversation_id: str, process_id: str) -> _Record:
    conv = (conversation_id or "").strip()
    pid = (process_id or "").strip()
    rec = _records.get(pid)
    if rec is None or rec.conversation_id != conv:
        raise DeskProcessError(
            "进程不存在或登记已丢失（服务重启后不会假装它还在）。",
            code=PROCESS_NOT_REGISTERED,
        )
    return rec
