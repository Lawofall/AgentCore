"""GVisor (runsc) based sandbox for secure code execution.

Execution model (安全权限与治理.md §五, as-built):

- **copy-in / copy-out 产物写回** (default writable runs): when the request
  carries a workspace ``cwd``, the workspace is COPIED into a per-run staging
  dir, seeded into a tmpfs ``/workspace``, then new/changed files are copied
  back under caps (``ExecutionResult.written_files``). Timeout / cancel skip
  copy-out.
- **install 专用例外** (``registry_egress=True``): rw-bind the persistent
  workspace (``request.cwd`` / DATA_DIR workspaces) at ``/workspace`` — skip
  staging copy-out and the whole-tree base64 wrap. ``node_modules`` / ``.venv``
  land on disk directly; short-lived sandbox only runs the install command (+ netns /
  ``/pkg-cache``). Why the exception: install trees are too large for
  staging↔base64 round-trip, and the product needs them on the durable workspace.
  Non-install writable execution keeps the staging model.
- **灰度护栏**: a process-global slot limiter caps concurrent executions
  (``GVISOR_MAX_CONCURRENT_EXECUTIONS``), with a bounded grace wait before an
  explainable busy failure; memory/timeout ceilings come from settings.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
import uuid
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
from agentcore.tools.sandbox.sandboxd.protocol import CodeNetwork, Shape
from agentcore.tools.sandbox.staging import (
    TreeState,
    collect_changes,
    prepare_bind_tree_for_sandbox,
    stage_workspace,
    write_back,
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

# Bundles must live on the DATA_DIR volume (settings.gvisor_runtime_root);
# container /tmp overlay makes runsc mkdir fail with EINVAL inside gVisor.
_ARTIFACT_MARKER = "__AGENTCORE_ARTIFACTS__"


def _runsc_shape(
    *, network_mode: str, registry_egress: bool
) -> tuple[Shape, CodeNetwork]:
    """Map execute request knobs onto sandboxd shape A (``code``) vs B (``net``)."""
    if registry_egress:
        return "net", "none"
    return "code", "host" if network_mode == "restricted" else "none"


def _resolve_runtime_root(explicit: str | None) -> str:
    """Prefer constructor override; else settings (default under data_dir)."""
    if explicit:
        return explicit
    return settings.gvisor_runtime_root


def _strip_artifact_payload(stdout: str) -> tuple[str, dict[str, str]]:
    """Remove the sandbox artifact trailer from stdout (tmpfs → host bridge)."""
    idx = stdout.rfind(_ARTIFACT_MARKER)
    if idx == -1:
        return stdout, {}
    prefix = stdout[:idx]
    tail = stdout[idx + len(_ARTIFACT_MARKER) :].lstrip("\n")
    line = tail.split("\n", 1)[0].strip()
    if not line:
        return prefix, {}
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("sandbox.artifact_payload_invalid")
        return prefix, {}
    if not isinstance(payload, dict):
        return prefix, {}
    files = {k: v for k, v in payload.items() if isinstance(k, str) and isinstance(v, str)}
    return prefix, files


def _materialize_artifacts(staging_dir: Path, payload: dict[str, str]) -> None:
    """Write sandbox tmpfs artifacts onto the host staging tree (mkdir OK on host)."""
    for rel, encoded in payload.items():
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            continue
        dest = staging_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(encoded.encode("ascii")))


class GVisorSandbox:
    """SandboxProvider implementation using gVisor runsc."""

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
        # Set by ``health_check`` on failure so boot probe / exec-env can log a
        # stable reason (and a classified code — not the unclassified fallback).
        self._last_health_failure: tuple[str, str | None] | None = None

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            isolation="gvisor",
            supports_network=True,  # restricted mode can enable; default still none
            max_memory_mb=settings.gvisor_memory_limit_mb,
            max_timeout_seconds=settings.gvisor_timeout_max_seconds,
        )

    @property
    def last_health_failure(self) -> tuple[str, str | None] | None:
        """``(reason, detail)`` from the latest failed ``health_check``, else ``None``."""
        return self._last_health_failure

    @property
    def last_health_failure_code(self) -> str | None:
        """Exec-env reason code for the latest failed health check, else ``None``."""
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
        """Compact reason + detail behind the latest failed health check."""
        failure = self._last_health_failure
        if failure is None:
            return ""
        reason, detail = failure
        return f"{reason} {detail}".strip() if detail else reason

    async def health_check(self) -> bool:
        """Probe shape A (``code``) via sandboxd. Never asks sandboxd about shape B."""
        self._last_health_failure = None
        if not _IS_LINUX:
            self._last_health_failure = ("not_linux", f"platform={sys.platform}")
            return False

        try:
            ok, detail = await get_sandboxd_client().health("code")
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
            logger.debug(
                "sandbox.health_check_failed",
                detail=detail[:200] or None,
            )
            return False
        return True

    # -- 会话面 (D9): long-lived browser sessions, added ALONGSIDE execute() -------
    # A separate surface for the L3 team browser — one-shot execute() is unchanged.
    def supports_browser_sessions(self) -> bool:
        """True where a real gVisor browser sandbox can run (Linux)."""
        from agentcore.tools.sandbox.browser.gvisor_session import browser_sessions_supported

        return browser_sessions_supported()

    async def open_browser_session(self, request):  # type: ignore[no-untyped-def]
        """Launch a long-lived browser sandbox (see ``browser.gvisor_session``)."""
        from agentcore.tools.sandbox.browser.gvisor_session import open_gvisor_browser_session

        return await open_gvisor_browser_session(
            request, runsc_path=self._runsc, runtime_root=self._runtime_root
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute code inside a gVisor sandbox (slot-limited, staged workspace)."""
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
        # 灰度护栏: bounded wait for a global execution slot, then fail fast with an
        # explainable busy result (never queue past the engine's tool deadline).
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
        container_id = f"agentcore-{uuid.uuid4().hex[:12]}"
        bundle_dir = tempfile.mkdtemp(prefix="agentcore_gvisor_", dir=self._runtime_root)
        timeout_seconds = self._effective_timeout(request)
        egress_session = None
        exit_code = -1
        stdout_str = ""
        stderr_str = ""

        try:
            # Install-only: netns + allowlist proxy (non-rootless sandbox network).
            # Merges proxy env into the request so npm/pnpm/yarn dial the chokepoint.
            if request.registry_egress:
                from agentcore.tools.sandbox.egress import (
                    install_proxy_env,
                    open_package_egress,
                )

                egress_session = await open_package_egress(
                    cache_bucket=request.cache_bucket
                )
                merged_env = dict(request.env or {})
                merged_env.update(install_proxy_env(egress_session.proxy_url))
                request = ExecutionRequest(
                    code=request.code,
                    language=request.language,
                    timeout_seconds=request.timeout_seconds,
                    memory_limit_mb=request.memory_limit_mb,
                    stdin=request.stdin,
                    cwd=request.cwd,
                    on_output=request.on_output,
                    env=merged_env,
                    network_mode=request.network_mode,
                    registry_egress=True,
                    cache_bucket=request.cache_bucket,
                    cpu_limit=request.cpu_limit,
                    pids_limit=request.pids_limit,
                    idle_timeout_seconds=request.idle_timeout_seconds,
                )

            scratch_dir = Path(bundle_dir) / "scratch"
            scratch_dir.mkdir()
            rootfs = Path(bundle_dir) / "rootfs"
            rootfs.mkdir()

            ext = _FILE_EXTENSIONS[request.language]
            script_name = f"main{ext}"
            (scratch_dir / script_name).write_text(request.code, encoding="utf-8")
            if request.stdin:
                (scratch_dir / "stdin.txt").write_text(request.stdin, encoding="utf-8")
            prepare_bind_tree_for_sandbox(scratch_dir)

            # Workspace mount policy:
            # - install (registry_egress): rw-bind persistent workspace (no staging /
            #   base64 wrap / write_back). Never prepare_bind_tree on the canonical tree.
            # - other writable: staging copy → tmpfs + wrap → write_back.
            # - no workspace: scratch as read-only /workspace.
            workspace_root = request.cwd or self._workspace_root
            staging_dir: Path | None = None
            staged_state: TreeState | None = None
            install_workspace_rw = bool(request.registry_egress and workspace_root)
            if install_workspace_rw and workspace_root is not None:
                workspace = str(Path(workspace_root).resolve())
            elif workspace_root:
                staging_dir = Path(bundle_dir) / "workspace"
                staged_state = await asyncio.to_thread(
                    stage_workspace,
                    Path(workspace_root),
                    staging_dir,
                    max_bytes=settings.gvisor_stage_max_bytes,
                )
                workspace = str(staging_dir.resolve())
            else:
                workspace = str(scratch_dir)
            config = self._build_oci_config(
                request,
                script_name=script_name,
                workspace=workspace,
                scratch_dir=str(scratch_dir.resolve()),
                workspace_writable=staging_dir is not None,
                install_workspace_rw=install_workspace_rw,
                memory_limit_mb=settings.gvisor_memory_limit_mb,
                egress_netns_path=(
                    egress_session.netns_path if egress_session is not None else None
                ),
                cache_host_dir=(
                    str(egress_session.cache_host_dir)
                    if egress_session is not None
                    else None
                ),
            )
            (Path(bundle_dir) / "config.json").write_text(
                json.dumps(config),
                encoding="utf-8",
            )

            shape, code_network = _runsc_shape(
                network_mode=request.network_mode,
                registry_egress=request.registry_egress,
            )
            idle = request.idle_timeout_seconds
            idle_timeout = float(idle) if idle is not None and idle > 0 else None

            try:
                client = get_sandboxd_client()
                exit_code, stdout_str, stderr_str = await client.run_wait(
                    shape=shape,
                    bundle_dir=bundle_dir,
                    container_id=container_id,
                    network_mode=code_network,
                    netns_path=(
                        egress_session.netns_path
                        if egress_session is not None
                        else None
                    ),
                    timeout_seconds=float(timeout_seconds),
                    idle_timeout_seconds=idle_timeout,
                    stdin=request.stdin,
                    on_output=request.on_output,
                )
            except TimeoutError:
                from agentcore.tools.sandbox.exec_env import disaster_timeout_stderr

                raise SandboxTimeoutError(
                    disaster_timeout_stderr(int(timeout_seconds))
                ) from None
            except SandboxdError as exc:
                if exc.code == "sandboxd_timeout":
                    from agentcore.tools.sandbox.exec_env import (
                        disaster_timeout_stderr,
                    )

                    raise SandboxTimeoutError(
                        disaster_timeout_stderr(int(timeout_seconds))
                    ) from exc
                raise SandboxError(f"代码执行环境启动失败：{exc}") from exc
            except OSError as e:
                raise SandboxError(f"代码执行环境启动失败：{e}") from e
            finally:
                await asyncio.shield(self._stop_container(container_id))

            if staging_dir is not None:
                clean_stdout, artifact_payload = _strip_artifact_payload(stdout_str)
                if artifact_payload:
                    _materialize_artifacts(staging_dir, artifact_payload)
                stdout_str = clean_stdout

            # Copy-out leg: only a run that COMPLETED (any exit code) persists its
            # artifacts — a partial success (chart saved, later step failed) still
            # delivers files; a timeout-killed run never lands half-written ones.
            written, skipped = await self._write_back_if_staged(
                staging_dir, staged_state, workspace_root
            )

            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                success=exit_code == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=exit_code or 0,
                duration_ms=duration_ms,
                written_files=written,
                write_back_skipped=skipped,
            )

        except SandboxTimeoutError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            from agentcore.tools.sandbox.exec_env import disaster_timeout_stderr

            detail = str(exc).strip() or disaster_timeout_stderr(int(timeout_seconds))
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=(
                    f"{detail}；执行被中断，中断前的文件改动未写回工作区。"
                ),
                exit_code=-1,
                duration_ms=duration_ms,
            )
        finally:
            if egress_session is not None:
                with contextlib.suppress(Exception):
                    await egress_session.close()
            shutil.rmtree(bundle_dir, ignore_errors=True)

    async def _write_back_if_staged(
        self,
        staging_dir: Path | None,
        staged_state: TreeState | None,
        workspace_root: str | None,
    ) -> tuple[list[str], int]:
        """Copy new/changed staged files back into the real workspace (capped)."""
        if staging_dir is None or staged_state is None or not workspace_root:
            return [], 0

        def _run() -> tuple[list[str], int]:
            changes = collect_changes(staging_dir, staged_state)
            if not changes:
                return [], 0
            report = write_back(
                staging_dir,
                Path(workspace_root),
                changes,
                max_bytes=settings.gvisor_write_back_max_bytes,
                max_files=settings.gvisor_write_back_max_files,
            )
            return report.written, len(report.skipped)

        written, skipped = await asyncio.to_thread(_run)
        if written or skipped:
            logger.info(
                "sandbox.write_back",
                written=len(written),
                skipped=skipped,
                files=written[:20],
            )
        return written, skipped

    def _build_run_cmd(
        self,
        *,
        bundle_dir: str,
        container_id: str,
        network_mode: str,
        registry_egress: bool = False,
    ) -> list[str]:
        """Allowlisted ``runsc`` argv via sandboxd (shape A ``code`` / B ``net``)."""
        shape, code_network = _runsc_shape(
            network_mode=network_mode, registry_egress=registry_egress
        )
        return build_runsc_cmd(
            runsc_path=self._runsc,
            runtime_root=self._runtime_root,
            bundle_dir=bundle_dir,
            container_id=container_id,
            shape=shape,
            network_mode=code_network,
        )

    def _build_command(self, request: ExecutionRequest, script_path: str) -> list[str]:
        if request.stdin and request.language == "bash":
            return ["bash", "-c", f"{script_path} < /scratch/stdin.txt"]
        return _LANGUAGE_COMMANDS[request.language] + [script_path]

    def _build_env(self, request: ExecutionRequest) -> list[str]:
        env: dict[str, str] = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Headless plotting: the sandbox has no display; without an explicit
            # backend matplotlib may probe for GUI toolkits and fail confusingly.
            "MPLBACKEND": "Agg",
            # Keep sandbox-created artifacts world-readable so the non-root API
            # user can copy them back after runsc exits (umask 022 → 644 files).
            "UMASK": "0022",
        }
        if request.env:
            env.update(request.env)
        return [f"{key}={value}" for key, value in env.items()]

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

    def _wrap_staged_workspace_command(self, inner_cmd: list[str]) -> list[str]:
        """Seed tmpfs /workspace, run, then emit artifacts on stdout for host copy-out."""
        inner = " ".join(shlex.quote(part) for part in inner_cmd)
        script = (
            "cp -a /workspace-seed/. /workspace/ 2>/dev/null || true; "
            f"{inner}; "
            "ec=$?; "
            "python3 - <<'PY'\n"
            "import base64, json, sys\n"
            "from pathlib import Path\n"
            "root = Path('/workspace')\n"
            "payload = {\n"
            "    p.relative_to(root).as_posix(): "
            "base64.b64encode(p.read_bytes()).decode('ascii')\n"
            "    for p in root.rglob('*') if p.is_file()\n"
            "}\n"
            "marker = "
            f"{_ARTIFACT_MARKER!r}\n"
            "sys.stdout.write(marker + json.dumps(payload, separators=(',', ':')))\n"
            "sys.stdout.write('\\n')\n"
            "PY\n"
            "exit $ec"
        )
        return ["bash", "-c", script]

    def _build_oci_config(
        self,
        request: ExecutionRequest,
        *,
        script_name: str,
        workspace: str,
        scratch_dir: str,
        workspace_writable: bool = False,
        install_workspace_rw: bool = False,
        memory_limit_mb: int | None = None,
        egress_netns_path: str | None = None,
        cache_host_dir: str | None = None,
    ) -> dict:
        script_path = f"/scratch/{script_name}"
        namespaces = [
            {"type": "pid"},
            {"type": "ipc"},
            {"type": "uts"},
            {"type": "mount"},
        ]
        # Install registry_egress: network ns BY PATH so sandbox netstack clones
        # the packaging veth (browser PoC finding). Other restricted stays as
        # empty network ns + rootless ``--network=host``.
        if egress_netns_path:
            namespaces.append({"type": "network", "path": egress_netns_path})
        elif request.network_mode == "restricted":
            namespaces.append({"type": "network"})

        mounts = [
            {
                "destination": "/tmp",
                "type": "tmpfs",
                "source": "tmpfs",
                "options": ["nosuid", "nodev", "size=64m"],
            },
        ]
        process_args = self._build_command(request, script_path)
        if install_workspace_rw:
            # Install-only: durable workspace rw-bind. Staging/tmpfs + whole-tree
            # base64 wrap cannot carry node_modules / .venv; sandbox is short-lived for
            # the install command only. Non-install writable stays below.
            mounts.append(
                {
                    "destination": "/workspace",
                    "type": "bind",
                    "source": workspace,
                    "options": ["rw", "rbind", "nosuid", "nodev"],
                }
            )
        elif workspace_writable:
            # runsc cannot mkdir on bind mounts (EINVAL) from inside Docker; use
            # tmpfs for live writes and copy-in/out via twin binds on staging.
            stage_mb = max(64, settings.gvisor_stage_max_bytes // (1024 * 1024))
            mounts.extend(
                [
                    {
                        "destination": "/workspace-seed",
                        "type": "bind",
                        "source": workspace,
                        "options": ["ro", "bind", "nosuid", "nodev"],
                    },
                    {
                        "destination": "/workspace",
                        "type": "tmpfs",
                        "source": "tmpfs",
                        "options": [
                            "rw",
                            "nosuid",
                            "nodev",
                            "mode=1777",
                            f"size={stage_mb}m",
                        ],
                    },
                ]
            )
            process_args = self._wrap_staged_workspace_command(process_args)
        else:
            mounts.append(
                {
                    "destination": "/workspace",
                    "type": "bind",
                    "source": workspace,
                    "options": ["ro", "rbind"],
                }
            )
        mounts.append(
            {
                "destination": "/scratch",
                "type": "bind",
                "source": scratch_dir,
                "options": ["ro", "bind", "nosuid", "nodev"],
            }
        )
        if cache_host_dir:
            from agentcore.tools.sandbox.egress.runtime import PACKAGE_CACHE_MOUNT

            mounts.append(
                {
                    "destination": PACKAGE_CACHE_MOUNT,
                    "type": "bind",
                    "source": cache_host_dir,
                    "options": ["rw", "bind", "nosuid", "nodev"],
                }
            )
        mounts.extend(self._host_bind_mounts())

        return {
            "ociVersion": "1.0.2",
            "process": {
                "terminal": False,
                "user": {"uid": 65534, "gid": 65534},
                "args": process_args,
                "env": self._build_env(request),
                "cwd": "/workspace",
            },
            "root": {"path": "rootfs", "readonly": True},
            "mounts": mounts,
            "linux": {
                "resources": {
                    # Cloud runs take the configured guardrail ceiling; the request's
                    # own field only applies when no explicit limit is passed (bare
                    # sandbox use in tests).
                    "memory": {
                        "limit": (memory_limit_mb or request.memory_limit_mb) * 1024 * 1024
                    },
                    "cpu": {
                        "quota": int(request.cpu_limit * 100000),
                        "period": 100000,
                    },
                    "pids": {"limit": request.pids_limit},
                },
                "namespaces": namespaces,
            },
        }

    async def _stop_container(self, container_id: str) -> None:
        client = get_sandboxd_client()
        with contextlib.suppress(Exception):
            await client.kill(container_id)
        with contextlib.suppress(Exception):
            await client.delete(container_id, force=True)
