"""GVisor (runsc) cloud-desk sandbox.

Execution model (安全权限与治理.md §五):

- One long-lived shape-net guest per workspace root (non-rootless
  ``--network=sandbox`` + netns). ``execute()`` is ``sandboxd exec`` into that
  guest. Only the current workspace is rw-bound at ``/workspace``.
- OCI uid/gid ≡ API ``os.getuid`` / ``os.getgid`` (``app``). No nobody, no
  chmod of the workspace, no guest root, no replica disk, no copy-in/out.
- Outbound is the desk-resident packaging allowlist chokepoint (netns + proxy
  opened once per guest), not a per-install hole punch.
- Concurrent exec slots + memory/duration ceilings still apply.
- Cloud Chromium is ``sandboxd exec`` stdio into this same guest (not a second
  runsc jail). Playwright is ro-bound here; ``/tmp`` is Chromium-sized.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agentcore.config import settings
from agentcore.core.errors import SandboxError, SandboxTimeoutError
from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.limits import try_acquire_execution_slot
from agentcore.tools.sandbox.protocol import (
    ExecutionRequest,
    ExecutionResult,
    SandboxCapabilities,
)
from agentcore.tools.sandbox.sandboxd import (
    SandboxdError,
    SandboxdUnavailableError,
    build_runsc_cmd,
    get_sandboxd_client,
)
from agentcore.tools.sandbox.written_scan import (
    scan_written_files,
    written_scan_cutoff_ns,
)

logger = get_logger(__name__)

_IS_LINUX = sys.platform == "linux"

_LANGUAGE_COMMANDS: dict[str, list[str]] = {
    "python": ["python3", "-u"],
    "javascript": ["node"],
    "bash": ["bash"],
}

_FILE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "javascript": ".js",
    "bash": ".sh",
}

_HOST_BIND_PATHS = ("/usr", "/lib", "/lib64", "/bin", "/etc")

_desks: dict[str, _DeskSession] = {}
_desk_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


def _host_uid_gid() -> tuple[int, int]:
    uid_fn = getattr(os, "getuid", None)
    gid_fn = getattr(os, "getgid", None)
    uid = int(uid_fn()) if uid_fn is not None else 0
    gid = int(gid_fn()) if gid_fn is not None else 0
    return uid, gid


def _desk_key(workspace: str) -> str:
    return str(Path(workspace).resolve())


def _desk_ids(key: str) -> tuple[str, str]:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"agentcore-desk-{digest[:16]}", digest[:32]


def _resolve_runtime_root(explicit: str | None) -> str:
    if explicit:
        return explicit
    return settings.gvisor_runtime_root


def reset_desk_sessions_for_tests() -> None:
    """Drop in-process desk map without talking to sandboxd (unit tests)."""
    _desks.clear()
    _desk_locks.clear()
    from agentcore.tools.sandbox.desk_process import reset_desk_processes_for_tests

    reset_desk_processes_for_tests()


def _now() -> float:
    """Monotonic clock for desk idle TTL. Tests monkeypatch this."""
    return time.monotonic()


@dataclass
class _DeskSession:
    key: str
    container_id: str
    bundle_dir: Path
    scratch_dir: Path
    workspace: str
    egress: object
    last_used: float = 0.0
    inflight: int = 0

    async def close(self) -> None:
        client = get_sandboxd_client()
        with contextlib.suppress(Exception):
            await client.kill(self.container_id)
        with contextlib.suppress(Exception):
            await client.delete(self.container_id, force=True)
        closer = getattr(self.egress, "close", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
        shutil.rmtree(self.bundle_dir, ignore_errors=True)
        logger.info("sandbox.desk_closed", workspace=self.key, container_id=self.container_id)


@dataclass(frozen=True)
class DeskAttach:
    """Handle for exec-into-desk (browser driver). Does not own the guest lifetime."""

    container_id: str
    scratch_dir: Path
    host_ip: str


def _egress_host_ip(egress: object) -> str:
    host_ip = getattr(egress, "host_ip", None)
    if host_ip:
        return str(host_ip)
    proxy_url = str(getattr(egress, "proxy_url", "") or "")
    from urllib.parse import urlsplit

    host = urlsplit(proxy_url).hostname
    if host:
        return host
    raise SandboxError("云桌 egress 没有可达的 host_ip")


async def attach_workspace_desk(
    workspace: str, *, cache_bucket: str | None = None, runtime_root: str | None = None
) -> DeskAttach:
    """Attach to the workspace desk guest. Does not take a gVisor execution slot."""
    if not _IS_LINUX:
        raise SandboxError("GVisor sandbox is only available on Linux")
    workspace_resolved = str(Path(workspace).resolve())
    if not Path(workspace_resolved).is_dir():
        raise SandboxError("云桌需要已挂载的工作区盘（禁止无盘 jail）。")
    data_dir = str(Path(settings.data_dir).resolve())
    if workspace_resolved == data_dir:
        raise SandboxError("禁止把整份 DATA_DIR 绑进云桌 guest")
    sandbox = GVisorSandbox(runtime_root=runtime_root)
    desk = await sandbox._ensure_desk(workspace_resolved, cache_bucket=cache_bucket)
    return DeskAttach(
        container_id=desk.container_id,
        scratch_dir=desk.scratch_dir,
        host_ip=_egress_host_ip(desk.egress),
    )


def touch_workspace_desk(workspace: str) -> None:
    """Refresh last_used for an already-running desk (terminal / browser activity)."""
    session = _desks.get(_desk_key(workspace))
    if session is not None:
        session.last_used = _now()


def touch_desk_by_container(container_id: str) -> None:
    """Refresh last_used when a sandbox browser on this guest is active."""
    for session in _desks.values():
        if session.container_id == container_id:
            session.last_used = _now()
            return


def _desk_has_running_process(key: str) -> bool:
    from agentcore.tools.sandbox.desk_process import desk_has_running_process

    return desk_has_running_process(key)


def _desk_has_live_sandbox_browser(container_id: str) -> bool:
    from agentcore.runtime.browser.registry import default_browser_session_registry

    return default_browser_session_registry().has_live_sandbox_on_desk(container_id)


async def _close_sandbox_browsers_for_desk(container_id: str) -> None:
    from agentcore.runtime.browser.registry import default_browser_session_registry

    await default_browser_session_registry().close_sandbox_sessions_on_desk(container_id)


def _can_reap_desk(session: _DeskSession, *, now: float, ttl: float) -> bool:
    if session.inflight > 0:
        return False
    if _desk_has_running_process(session.key):
        return False
    if _desk_has_live_sandbox_browser(session.container_id):
        return False
    return (now - session.last_used) >= ttl


async def reap_idle_desks() -> int:
    """Kill idle cloud-desk guests (memory path). Disk stays; next use lazy-creates.

    Never freeze/pause. Local Bridge / sidecar never populate ``_desks``.
    """
    ttl = float(settings.gvisor_desk_idle_ttl_seconds)
    now = _now()
    async with _registry_lock:
        keys = list(_desks)
    reaped = 0
    for key in keys:
        async with _registry_lock:
            lock = _desk_locks.get(key)
        if lock is None:
            continue
        async with lock:
            session = _desks.get(key)
            if session is None or not _can_reap_desk(session, now=now, ttl=ttl):
                continue
            _desks.pop(key, None)
            from agentcore.tools.sandbox.desk_process import drop_processes_for_desk_keys

            drop_processes_for_desk_keys((key,))
            await _close_sandbox_browsers_for_desk(session.container_id)
            await session.close()
            reaped += 1
            logger.info(
                "sandbox.desk_reaped",
                workspace=session.key,
                container_id=session.container_id,
            )
    return reaped


async def _unpin_desk(workspace: str) -> None:
    key = _desk_key(workspace)
    async with _registry_lock:
        lock = _desk_locks.get(key)
    if lock is None:
        return
    async with lock:
        session = _desks.get(key)
        if session is not None:
            session.inflight = max(0, session.inflight - 1)
            session.last_used = _now()


async def close_all_desk_sessions() -> None:
    """Lifespan shutdown: tear down every lazy-started cloud-desk guest."""
    async with _registry_lock:
        sessions = list(_desks.values())
        _desks.clear()
        _desk_locks.clear()
    from agentcore.tools.sandbox.desk_process import drop_processes_for_desk_keys

    drop_processes_for_desk_keys(tuple(session.key for session in sessions))
    for session in sessions:
        with contextlib.suppress(Exception):
            await session.close()


class GVisorSandbox:
    """SandboxProvider implementation using a long-lived gVisor desk guest."""

    def __init__(
        self,
        *,
        runsc_path: str = "runsc",
        workspace_root: str | None = None,
        runtime_root: str | None = None,
    ) -> None:
        self._runsc = runsc_path
        self._workspace_root = workspace_root
        self._runtime_root = _resolve_runtime_root(runtime_root)
        os.makedirs(self._runtime_root, exist_ok=True)
        self._last_health_failure: tuple[str, str | None] | None = None

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            isolation="gvisor",
            supports_network=True,
            max_memory_mb=settings.gvisor_memory_limit_mb,
            max_timeout_seconds=settings.gvisor_timeout_max_seconds,
        )

    @property
    def last_health_failure(self) -> tuple[str, str | None] | None:
        return self._last_health_failure

    @property
    def last_health_failure_code(self) -> str | None:
        if self._last_health_failure is None:
            return None
        from agentcore.tools.sandbox.exec_env import (
            EXEC_ENV_NOT_LINUX_CODE,
            EXEC_ENV_SANDBOX_UNAVAILABLE_CODE,
        )

        reason = self._last_health_failure[0]
        if reason == "not_linux":
            return EXEC_ENV_NOT_LINUX_CODE
        return EXEC_ENV_SANDBOX_UNAVAILABLE_CODE

    @property
    def last_health_evidence(self) -> str:
        failure = self._last_health_failure
        if failure is None:
            return ""
        reason, detail = failure
        return f"{reason} {detail}".strip() if detail else reason

    async def health_check(self) -> bool:
        """Probe shape ``net`` (desk/net can start)."""
        self._last_health_failure = None
        if not _IS_LINUX:
            self._last_health_failure = ("not_linux", f"platform={sys.platform}")
            return False

        try:
            ok, detail = await get_sandboxd_client().health("net")
        except SandboxdUnavailableError as exc:
            self._last_health_failure = ("sandboxd_unavailable", str(exc)[:200] or None)
            logger.debug("sandbox.health_check_failed", error=str(exc)[:200])
            return False
        except SandboxdError as exc:
            self._last_health_failure = ("runsc_failed", str(exc)[:200] or None)
            logger.debug("sandbox.health_check_failed", error=str(exc)[:200])
            return False
        except OSError as exc:
            self._last_health_failure = ("os_error", str(exc)[:200])
            logger.debug("sandbox.health_check_failed", error=str(exc)[:200])
            return False
        if not ok:
            self._last_health_failure = ("runsc_failed", detail[:200] or None)
            logger.debug("sandbox.health_check_failed", detail=detail[:200] or None)
            return False
        return True

    def supports_browser_sessions(self) -> bool:
        from agentcore.tools.sandbox.browser.gvisor_session import browser_sessions_supported

        return browser_sessions_supported()

    async def open_browser_session(self, request):  # type: ignore[no-untyped-def]
        from agentcore.tools.sandbox.browser.gvisor_session import open_gvisor_browser_session

        return await open_gvisor_browser_session(
            request, runsc_path=self._runsc, runtime_root=self._runtime_root
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not _IS_LINUX:
            raise SandboxError("GVisor sandbox is only available on Linux")

        if request.language not in _LANGUAGE_COMMANDS:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Unsupported language: {request.language}",
                exit_code=1,
                duration_ms=0,
            )

        start = time.monotonic()
        release = await try_acquire_execution_slot()
        if release is None:
            return self._slot_busy_result(start)
        try:
            return await self._execute_in_slot(request, start)
        finally:
            release()

    def _slot_busy_result(self, start: float) -> ExecutionResult:
        capacity = max(1, int(settings.gvisor_max_concurrent_executions))
        waited = float(settings.gvisor_slot_wait_seconds)
        logger.info("sandbox.slot_busy", capacity=capacity, waited_seconds=waited)
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=(
                f"云端执行位已满（并发上限 {capacity}），等待 {waited:g} 秒仍未获得执行位。"
                "请稍后重试；持续繁忙时可拆小任务或错峰执行。"
            ),
            exit_code=-1,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def _effective_timeout(self, request: ExecutionRequest) -> int:
        return min(int(request.timeout_seconds), int(settings.gvisor_timeout_max_seconds))

    async def _execute_in_slot(
        self, request: ExecutionRequest, start: float
    ) -> ExecutionResult:
        workspace_root = request.cwd or self._workspace_root
        if not workspace_root:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="云桌执行需要工作区路径（禁止无盘 exec）。",
                exit_code=1,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        workspace = str(Path(workspace_root).resolve())
        data_dir = str(Path(settings.data_dir).resolve())
        if workspace == data_dir:
            raise SandboxError("禁止把整份 DATA_DIR 绑进云桌 guest")

        timeout_seconds = self._effective_timeout(request)
        pinned = False
        try:
            desk = await self._ensure_desk(
                workspace, cache_bucket=request.cache_bucket, pin=True
            )
            pinned = True
            script_name = f"exec-{uuid.uuid4().hex[:12]}{_FILE_EXTENSIONS[request.language]}"
            (desk.scratch_dir / script_name).write_text(request.code, encoding="utf-8")
            argv = _LANGUAGE_COMMANDS[request.language] + [f"/scratch/{script_name}"]
            env_pairs = self._exec_env_pairs(request, desk)
            idle = request.idle_timeout_seconds
            idle_timeout = float(idle) if idle is not None and idle > 0 else None
            cutoff_ns = written_scan_cutoff_ns()
            client = get_sandboxd_client()
            exit_code, stdout_str, stderr_str = await client.exec_wait(
                container_id=desk.container_id,
                argv=argv,
                cwd="/workspace",
                env=env_pairs,
                timeout_seconds=float(timeout_seconds),
                idle_timeout_seconds=idle_timeout,
                stdin=request.stdin,
                on_output=request.on_output,
            )
        except (TimeoutError, SandboxTimeoutError):
            duration_ms = int((time.monotonic() - start) * 1000)
            from agentcore.tools.sandbox.exec_env import disaster_timeout_stderr

            detail = disaster_timeout_stderr(int(timeout_seconds))
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"{detail}；执行被中断，工作区可能已有部分改动。",
                exit_code=-1,
                duration_ms=duration_ms,
            )
        except SandboxdError as exc:
            if exc.code == "sandboxd_timeout":
                duration_ms = int((time.monotonic() - start) * 1000)
                from agentcore.tools.sandbox.exec_env import disaster_timeout_stderr

                detail = disaster_timeout_stderr(int(timeout_seconds))
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"{detail}；执行被中断，工作区可能已有部分改动。",
                    exit_code=-1,
                    duration_ms=duration_ms,
                )
            raise SandboxError(f"代码执行环境启动失败：{exc}") from exc
        except OSError as e:
            raise SandboxError(f"代码执行环境启动失败：{e}") from e
        finally:
            if pinned:
                await _unpin_desk(workspace)

        written = await self._scan_written(workspace, cutoff_ns)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ExecutionResult(
            success=exit_code == 0,
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code or 0,
            duration_ms=duration_ms,
            written_files=written,
        )

    async def _scan_written(self, workspace: str, cutoff_ns: int) -> list[str]:
        try:
            scan = await asyncio.to_thread(
                scan_written_files, Path(workspace), cutoff_ns=cutoff_ns
            )
        except Exception as exc:  # noqa: BLE001 — scan must not fail the run
            logger.info("sandbox.written_scan_failed", error=str(exc)[:200])
            return []
        if scan.truncated:
            logger.info("sandbox.written_scan_truncated", found=len(scan.files))
        return scan.files

    def _desk_env_pairs(self, desk: _DeskSession) -> list[str]:
        env: dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLBACKEND": "Agg",
        }
        proxy_url = getattr(desk.egress, "proxy_url", None)
        if proxy_url:
            from agentcore.tools.sandbox.egress import install_proxy_env

            env.update(install_proxy_env(str(proxy_url)))
        return [f"{key}={value}" for key, value in env.items()]

    def _exec_env_pairs(self, request: ExecutionRequest, desk: _DeskSession) -> list[str]:
        env_pairs = self._desk_env_pairs(desk)
        if not request.env:
            return env_pairs
        merged = dict(item.split("=", 1) for item in env_pairs)
        merged.update(request.env)
        return [f"{key}={value}" for key, value in merged.items()]

    async def ensure_workspace_desk(
        self, workspace: str, *, cache_bucket: str | None = None
    ) -> None:
        """Start (or reuse) the long-lived desk guest for this workspace root."""
        await self._ensure_desk(workspace, cache_bucket=cache_bucket)

    def host_scratch_dir(self, workspace: str) -> Path | None:
        """Host path of the guest ``/scratch`` bind, or None if the desk is down."""
        session = _desks.get(_desk_key(workspace))
        return None if session is None else session.scratch_dir

    async def short_exec_script(
        self,
        workspace: str,
        *,
        guest_script: str,
        timeout_seconds: float = 15.0,
        cache_bucket: str | None = None,
    ) -> tuple[int, str, str]:
        """``bash`` a ``/scratch/…`` script and return when that script exits.

        Holds the global execution slot only for this wait. Callers that start
        long-running children must background them inside the script.
        """
        if not _IS_LINUX:
            raise SandboxError("GVisor sandbox is only available on Linux")
        if not guest_script.startswith("/scratch/") or ".." in guest_script:
            raise SandboxError("desk short exec 脚本必须落在 /scratch")
        start = time.monotonic()
        release = await try_acquire_execution_slot()
        if release is None:
            busy = self._slot_busy_result(start)
            return busy.exit_code, busy.stdout, busy.stderr
        pinned = False
        try:
            desk = await self._ensure_desk(workspace, cache_bucket=cache_bucket, pin=True)
            pinned = True
            argv = ["bash", guest_script]
            env_pairs = self._desk_env_pairs(desk)
            client = get_sandboxd_client()
            return await client.exec_wait(
                container_id=desk.container_id,
                argv=argv,
                cwd="/workspace",
                env=env_pairs,
                timeout_seconds=float(timeout_seconds),
            )
        except (TimeoutError, SandboxTimeoutError) as exc:
            raise SandboxError("云桌短执行超时") from exc
        except SandboxdError as exc:
            if exc.code == "sandboxd_timeout":
                raise SandboxError("云桌短执行超时") from exc
            raise SandboxError(f"代码执行环境启动失败：{exc}") from exc
        except OSError as exc:
            raise SandboxError(f"代码执行环境启动失败：{exc}") from exc
        finally:
            if pinned:
                await _unpin_desk(workspace)
            release()

    async def _ensure_desk(
        self, workspace: str, *, cache_bucket: str | None, pin: bool = False
    ) -> _DeskSession:
        key = _desk_key(workspace)
        async with _registry_lock:
            lock = _desk_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = _desks.get(key)
            if existing is not None:
                existing.last_used = _now()
                if pin:
                    existing.inflight += 1
                return existing
            session = await self._start_desk(key, workspace, cache_bucket=cache_bucket)
            session.last_used = _now()
            if pin:
                session.inflight += 1
            _desks[key] = session
            return session

    async def _start_desk(
        self, key: str, workspace: str, *, cache_bucket: str | None
    ) -> _DeskSession:
        from agentcore.tools.sandbox.egress import open_package_egress

        container_id, bucket_id = _desk_ids(key)
        bundle_dir = Path(
            tempfile.mkdtemp(prefix="agentcore_desk_", dir=self._runtime_root)
        )
        scratch_dir = bundle_dir / "scratch"
        scratch_dir.mkdir()
        rootfs = bundle_dir / "rootfs"
        rootfs.mkdir()
        egress = await open_package_egress(cache_bucket=cache_bucket or bucket_id)
        try:
            config = self._build_desk_oci(
                workspace=workspace,
                scratch_dir=str(scratch_dir.resolve()),
                netns_path=egress.netns_path,
                cache_host_dir=str(egress.cache_host_dir),
                proxy_url=egress.proxy_url,
            )
            (bundle_dir / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            client = get_sandboxd_client()
            await client.start_detach(
                bundle_dir=str(bundle_dir),
                container_id=container_id,
                netns_path=egress.netns_path,
            )
        except Exception:
            with contextlib.suppress(Exception):
                await egress.close()
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise
        logger.info(
            "sandbox.desk_started",
            workspace=key,
            container_id=container_id,
        )
        return _DeskSession(
            key=key,
            container_id=container_id,
            bundle_dir=bundle_dir,
            scratch_dir=scratch_dir,
            workspace=workspace,
            egress=egress,
            last_used=_now(),
        )

    def _host_bind_mounts(self) -> list[dict]:
        mounts: list[dict] = []
        for path in _HOST_BIND_PATHS:
            if os.path.isdir(path):
                mounts.append(
                    {
                        "destination": path,
                        "type": "bind",
                        "source": path,
                        "options": ["ro", "rbind", "nosuid"],
                    }
                )
        return mounts

    def _build_desk_oci(
        self,
        *,
        workspace: str,
        scratch_dir: str,
        netns_path: str,
        cache_host_dir: str,
        proxy_url: str,
        memory_limit_mb: int | None = None,
    ) -> dict:
        uid, gid = _host_uid_gid()
        from agentcore.tools.sandbox.browser.oci import (
            CHROMIUM_TMPFS_SIZE,
            playwright_browsers_mount,
        )
        from agentcore.tools.sandbox.egress import install_proxy_env
        from agentcore.tools.sandbox.egress.runtime import PACKAGE_CACHE_MOUNT

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLBACKEND": "Agg",
            **install_proxy_env(proxy_url),
        }
        mounts = [
            {
                "destination": "/tmp",
                "type": "tmpfs",
                "source": "tmpfs",
                "options": ["nosuid", "nodev", "mode=1777", f"size={CHROMIUM_TMPFS_SIZE}"],
            },
            {
                "destination": "/workspace",
                "type": "bind",
                "source": workspace,
                "options": ["rw", "rbind", "nosuid", "nodev"],
            },
            {
                "destination": "/scratch",
                "type": "bind",
                "source": scratch_dir,
                "options": ["rw", "bind", "nosuid", "nodev"],
            },
            {
                "destination": PACKAGE_CACHE_MOUNT,
                "type": "bind",
                "source": cache_host_dir,
                "options": ["rw", "bind", "nosuid", "nodev"],
            },
            *self._host_bind_mounts(),
        ]
        pw = playwright_browsers_mount(settings.browser_playwright_browsers_path)
        if pw is not None:
            mounts.append(pw)
        if memory_limit_mb is not None:
            mem_mb = int(memory_limit_mb)
        else:
            mem_mb = max(
                int(settings.gvisor_memory_limit_mb),
                int(settings.browser_sandbox_memory_limit_mb),
            )
        mem = mem_mb * 1024 * 1024
        cpu_quota = int(float(settings.browser_sandbox_cpu_limit) * 100000)
        pids_limit = int(settings.browser_sandbox_pids_limit)
        return {
            "ociVersion": "1.0.2",
            "process": {
                "terminal": False,
                "user": {"uid": uid, "gid": gid},
                "args": ["sleep", "infinity"],
                "env": [f"{k}={v}" for k, v in env.items()],
                "cwd": "/workspace",
            },
            "root": {"path": "rootfs", "readonly": True},
            "mounts": mounts,
            "linux": {
                "resources": {
                    "memory": {"limit": mem},
                    "cpu": {"quota": cpu_quota, "period": 100000},
                    "pids": {"limit": pids_limit},
                },
                "namespaces": [
                    {"type": "pid"},
                    {"type": "ipc"},
                    {"type": "uts"},
                    {"type": "mount"},
                    {"type": "network", "path": netns_path},
                ],
            },
        }

    def _build_run_cmd(
        self,
        *,
        bundle_dir: str,
        container_id: str,
        network_mode: str = "none",
        detach: bool = True,
    ) -> list[str]:
        """Desk start argv: always shape net (ignore leftover network_mode)."""
        del network_mode
        return build_runsc_cmd(
            runsc_path=self._runsc,
            runtime_root=self._runtime_root,
            bundle_dir=bundle_dir,
            container_id=container_id,
            detach=detach,
        )

    async def close_all(self) -> None:
        await close_all_desk_sessions()
