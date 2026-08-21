"""uid-0 sandboxd: Unix-socket RPC that execs allowlisted runsc / ip only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd
from agentcore.tools.sandbox.sandboxd.netns_ops import (
    NETNS_RUN_DIR,
    PROBE_NETNS_NAME,
    NetnsOpsError,
    probe_setup,
    probe_teardown,
    spec_for,
)
from agentcore.tools.sandbox.sandboxd.netns_ops import setup as netns_setup_ops
from agentcore.tools.sandbox.sandboxd.netns_ops import teardown as netns_teardown_ops
from agentcore.tools.sandbox.sandboxd.protocol import (
    CONTAINER_ID_PREFIX,
    DEFAULT_SOCKET_PATH,
    METHOD_DELETE,
    METHOD_HEALTH,
    METHOD_KILL,
    METHOD_NETNS_SETUP,
    METHOD_NETNS_TEARDOWN,
    METHOD_PING,
    METHOD_RUN,
    SOCKET_ENV,
    CodeNetwork,
    NetFamily,
    Shape,
)

logger = get_logger(__name__)

_SO_PEERCRED = 17  # Linux SOL_SOCKET / SO_PEERCRED
_HOST_BIND_PATHS = ("/usr", "/lib", "/lib64", "/bin", "/etc")
_SUBNET_RE = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}$")
_CONTAINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALLOWED_SIGNALS = frozenset({"SIGKILL", "SIGTERM", "SIGINT", "SIGHUP"})
_STREAM_CHUNK = 2048


class RpcDeniedError(Exception):
    def __init__(self, message: str, *, code: str = "sandboxd_denied") -> None:
        super().__init__(message)
        self.code = code


def _self_uid() -> int:
    getter = getattr(os, "getuid", None)
    return int(getter()) if getter is not None else 0


def lookup_user_uid(name: str) -> int | None:
    try:
        import pwd

        getpwnam = getattr(pwd, "getpwnam", None)
        if getpwnam is None:
            return None
        return int(getpwnam(name).pw_uid)
    except (ImportError, KeyError, AttributeError):
        return None


def peer_uid(sock: socket.socket | None) -> int | None:
    """SO_PEERCRED uid, or None when credentials cannot be read."""
    if sock is None or sys.platform != "linux":
        return _self_uid() if sys.platform != "linux" else None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("iii"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("iii", raw)
    return int(uid)


def peer_allowed(
    sock: socket.socket | None,
    *,
    self_uid: int,
    app_uid: int | None,
) -> bool:
    uid = peer_uid(sock)
    if uid is None:
        return False
    if uid == self_uid:
        return True
    return app_uid is not None and uid == app_uid


def _json_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _ok(req_id: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": req_id, "ok": True, "result": result or {}}


def _err(req_id: Any, error: str, *, code: str = "sandboxd_rpc") -> dict[str, Any]:
    return {"id": req_id, "ok": False, "error": error, "code": code}


def _host_bind_mounts() -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
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


def _health_oci_config(*, netns_path: str | None = None) -> dict[str, Any]:
    namespaces: list[dict[str, Any]] = [
        {"type": "pid"},
        {"type": "ipc"},
        {"type": "uts"},
        {"type": "mount"},
    ]
    if netns_path:
        namespaces.append({"type": "network", "path": netns_path})
    return {
        "ociVersion": "1.0.2",
        "process": {
            "terminal": False,
            "user": {"uid": 65534, "gid": 65534},
            "args": ["/bin/true"],
            "env": ["PATH=/usr/bin:/bin"],
            "cwd": "/tmp",
        },
        "root": {"path": "rootfs", "readonly": True},
        "mounts": [
            {
                "destination": "/tmp",
                "type": "tmpfs",
                "source": "tmpfs",
                "options": ["nosuid", "nodev", "size=8m"],
            },
            *_host_bind_mounts(),
        ],
        "linux": {
            "resources": {
                "memory": {"limit": 64 * 1024 * 1024},
                "pids": {"limit": 32},
            },
            "namespaces": namespaces,
        },
    }


class SandboxdServer:
    def __init__(
        self,
        *,
        socket_path: str,
        runsc_path: str,
        runtime_root: str,
        netns_run_dir: str = NETNS_RUN_DIR,
        app_user: str = "app",
    ) -> None:
        self.socket_path = socket_path
        self._runsc = runsc_path
        self._runtime_root = runtime_root
        self._netns_run_dir = netns_run_dir
        self._app_user = app_user
        self._app_uid = lookup_user_uid(app_user)
        self._self_uid = _self_uid()
        self._server: asyncio.Server | None = None
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._proc_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls) -> SandboxdServer:
        from agentcore.config import settings

        socket_path = os.environ.get(SOCKET_ENV) or settings.sandboxd_socket
        return cls(
            socket_path=socket_path or DEFAULT_SOCKET_PATH,
            runsc_path=settings.gvisor_runsc_path,
            runtime_root=settings.gvisor_runtime_root,
        )

    async def start(self) -> None:
        os.makedirs(self._runtime_root, exist_ok=True)
        parent = os.path.dirname(self.socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with contextlib.suppress(FileNotFoundError, OSError):
            os.unlink(self.socket_path)
        start_unix = getattr(asyncio, "start_unix_server", None)
        if start_unix is None:
            raise OSError("sandboxd requires Unix sockets (Linux)")
        self._server = await start_unix(self._on_client, path=self.socket_path)
        self._relax_socket_perms()
        logger.info("sandboxd.started", socket=self.socket_path)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError, OSError):
            os.unlink(self.socket_path)
        logger.info("sandboxd.stopped")

    def _relax_socket_perms(self) -> None:
        with contextlib.suppress(OSError):
            os.chmod(self.socket_path, 0o660)
        try:
            import grp

            getgrnam = getattr(grp, "getgrnam", None)
            if getgrnam is None:
                raise AttributeError("grp.getgrnam")
            gid = getgrnam(self._app_user).gr_gid
            chown = getattr(os, "chown", None)
            if chown is not None:
                chown(self.socket_path, self._self_uid, gid)
        except (ImportError, KeyError, OSError, AttributeError):
            with contextlib.suppress(OSError):
                os.chmod(self.socket_path, 0o666)

    def _require_container_id(self, raw: Any) -> str:
        if not isinstance(raw, str) or not raw.startswith(CONTAINER_ID_PREFIX):
            raise RpcDeniedError("container_id must start with agentcore-")
        if not _CONTAINER_ID_RE.fullmatch(raw):
            raise RpcDeniedError("container_id has invalid characters")
        return raw

    def _require_under_root(self, raw: Any, root: str, *, label: str) -> Path:
        if not isinstance(raw, str) or not raw:
            raise RpcDeniedError(f"{label} is required")
        resolved = Path(raw).resolve()
        root_res = Path(root).resolve()
        if not resolved.is_relative_to(root_res):
            raise RpcDeniedError(f"{label} must be under runtime root")
        return resolved

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if not peer_allowed(sock, self_uid=self._self_uid, app_uid=self._app_uid):
            logger.warning("sandboxd.peer_denied", uid=peer_uid(sock))
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
        keep_open = False
        try:
            raw = await reader.readline()
            if not raw:
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                writer.write(_json_bytes(_err(None, "invalid json", code="invalid_json")))
                await writer.drain()
                return
            if not isinstance(msg, dict):
                writer.write(_json_bytes(_err(None, "invalid request")))
                await writer.drain()
                return
            req_id = msg.get("id")
            method = msg.get("method")
            raw_params = msg.get("params")
            params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
            try:
                keep_open = await self._dispatch(method, params, req_id, reader, writer)
            except RpcDeniedError as exc:
                logger.warning("sandboxd.rpc_denied", method=str(method), error=str(exc))
                writer.write(_json_bytes(_err(req_id, str(exc), code=exc.code)))
                await writer.drain()
            except NetnsOpsError as exc:
                writer.write(_json_bytes(_err(req_id, str(exc), code="NetnsError")))
                await writer.drain()
            except Exception as exc:  # noqa: BLE001 — RPC boundary
                logger.warning("sandboxd.rpc_error", method=str(method), error=str(exc)[:200])
                writer.write(_json_bytes(_err(req_id, str(exc)[:200])))
                await writer.drain()
        finally:
            if not keep_open:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _dispatch(
        self,
        method: Any,
        params: dict[str, Any],
        req_id: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        if method == METHOD_PING:
            writer.write(_json_bytes(_ok(req_id)))
            await writer.drain()
            return False
        if method == METHOD_HEALTH:
            shape = params.get("shape")
            if shape not in ("code", "net"):
                raise RpcDeniedError("health shape must be code or net")
            ok, detail = await self._health(shape)
            writer.write(_json_bytes(_ok(req_id, {"ok": ok, "detail": detail})))
            await writer.drain()
            return False
        if method == METHOD_NETNS_SETUP:
            info = await self._netns_setup(params)
            writer.write(_json_bytes(_ok(req_id, info)))
            await writer.drain()
            return False
        if method == METHOD_NETNS_TEARDOWN:
            await self._netns_teardown(params)
            writer.write(_json_bytes(_ok(req_id)))
            await writer.drain()
            return False
        if method == METHOD_DELETE:
            await self._delete(params)
            writer.write(_json_bytes(_ok(req_id)))
            await writer.drain()
            return False
        if method == METHOD_KILL:
            await self._kill(params)
            writer.write(_json_bytes(_ok(req_id)))
            await writer.drain()
            return False
        if method == METHOD_RUN:
            return await self._run(params, req_id, reader, writer)
        raise RpcDeniedError(f"unknown method: {method}", code="unknown_method")

    def _parse_slot(self, raw: Any) -> int:
        if isinstance(raw, bool) or not isinstance(raw, int) or not (0 <= raw <= 255):
            raise RpcDeniedError("slot must be an int 0–255")
        return raw

    def _parse_family(self, raw: Any) -> NetFamily:
        if raw not in ("browser", "package"):
            raise RpcDeniedError("family must be browser or package")
        return raw

    def _parse_subnet(self, raw: Any) -> str:
        if not isinstance(raw, str) or not _SUBNET_RE.fullmatch(raw):
            raise RpcDeniedError("subnet_base must be two dotted octets")
        return raw

    async def _netns_setup(self, params: dict[str, Any]) -> dict[str, Any]:
        family = self._parse_family(params.get("family"))
        slot = self._parse_slot(params.get("slot"))
        subnet = self._parse_subnet(params.get("subnet_base"))
        spec = spec_for(family, slot, subnet, run_dir=self._netns_run_dir)
        await netns_setup_ops(spec)
        logger.info("sandboxd.netns_setup", family=family, slot=slot, name=spec.name)
        return {
            "slot": spec.slot,
            "name": spec.name,
            "path": spec.path,
            "host_ip": spec.host_ip,
            "sbx_ip": spec.sbx_ip,
        }

    async def _netns_teardown(self, params: dict[str, Any]) -> None:
        family = self._parse_family(params.get("family"))
        slot = self._parse_slot(params.get("slot"))
        spec = spec_for(family, slot, "0.0", run_dir=self._netns_run_dir)
        await netns_teardown_ops(spec)
        logger.info("sandboxd.netns_teardown", family=family, slot=slot, name=spec.name)

    async def _health(self, shape: Shape) -> tuple[bool, str]:
        container_id = f"agentcore-health-{uuid.uuid4().hex[:12]}"
        bundle_dir = tempfile.mkdtemp(prefix="agentcore_health_", dir=self._runtime_root)
        netns_path: str | None = None
        try:
            if shape == "net":
                netns_path = await probe_setup(PROBE_NETNS_NAME, run_dir=self._netns_run_dir)
            rootfs = Path(bundle_dir) / "rootfs"
            rootfs.mkdir()
            config = _health_oci_config(netns_path=netns_path)
            (Path(bundle_dir) / "config.json").write_text(json.dumps(config), encoding="utf-8")
            cmd = build_runsc_cmd(
                runsc_path=self._runsc,
                runtime_root=self._runtime_root,
                bundle_dir=bundle_dir,
                container_id=container_id,
                shape=shape,
                network_mode="none",
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:200]
                logger.warning(
                    "sandboxd.health_failed",
                    shape=shape,
                    detail=detail or None,
                )
                return False, detail
            return True, ""
        except (FileNotFoundError, OSError, NetnsOpsError) as exc:
            detail = str(exc)[:200]
            logger.warning("sandboxd.health_failed", shape=shape, detail=detail)
            return False, detail
        finally:
            await self._runsc_aux("delete", "--force", container_id)
            if shape == "net":
                with contextlib.suppress(Exception):
                    await probe_teardown(PROBE_NETNS_NAME)
            shutil.rmtree(bundle_dir, ignore_errors=True)

    async def _run(
        self,
        params: dict[str, Any],
        req_id: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        shape = params.get("shape")
        mode = params.get("mode")
        if shape not in ("code", "net"):
            raise RpcDeniedError("shape must be code or net")
        if mode not in ("wait", "stdio"):
            raise RpcDeniedError("mode must be wait or stdio")
        if mode == "stdio" and shape != "net":
            raise RpcDeniedError("stdio mode requires shape=net")
        container_id = self._require_container_id(params.get("container_id"))
        bundle_dir = str(
            self._require_under_root(
                params.get("bundle_dir"), self._runtime_root, label="bundle_dir"
            )
        )
        network_mode: CodeNetwork = "host" if params.get("network_mode") == "host" else "none"
        netns_path_raw = params.get("netns_path")
        if shape == "net":
            if not isinstance(netns_path_raw, str) or not netns_path_raw:
                raise RpcDeniedError("netns_path is required for shape=net")
            self._require_under_root(netns_path_raw, self._netns_run_dir, label="netns_path")
        cmd = build_runsc_cmd(
            runsc_path=self._runsc,
            runtime_root=self._runtime_root,
            bundle_dir=bundle_dir,
            container_id=container_id,
            shape=shape,
            network_mode=network_mode,
        )
        logger.info(
            "sandboxd.run",
            shape=shape,
            mode=mode,
            container_id=container_id,
        )
        if mode == "stdio":
            await self._run_stdio(cmd, container_id, req_id, reader, writer)
            return True
        timeout = params.get("timeout_seconds")
        timeout_seconds = float(timeout) if isinstance(timeout, (int, float)) else 60.0
        idle = params.get("idle_timeout_seconds")
        idle_timeout = float(idle) if isinstance(idle, (int, float)) and idle > 0 else None
        stdin = params.get("stdin")
        stdin_s = stdin if isinstance(stdin, str) else None
        await self._run_wait(
            cmd,
            container_id,
            req_id,
            writer,
            timeout_seconds=max(timeout_seconds, 0.1),
            idle_timeout_seconds=idle_timeout,
            stdin=stdin_s,
        )
        return False

    async def _track(self, container_id: str, proc: asyncio.subprocess.Process) -> None:
        async with self._proc_lock:
            self._procs[container_id] = proc

    async def _untrack(self, container_id: str) -> None:
        async with self._proc_lock:
            self._procs.pop(container_id, None)

    async def _run_wait(
        self,
        cmd: list[str],
        container_id: str,
        req_id: Any,
        writer: asyncio.StreamWriter,
        *,
        timeout_seconds: float,
        idle_timeout_seconds: float | None,
        stdin: str | None,
    ) -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._track(container_id, proc)
        write_lock = asyncio.Lock()

        async def emit(obj: dict[str, Any]) -> None:
            async with write_lock:
                writer.write(_json_bytes(obj))
                await writer.drain()

        await emit(_ok(req_id, {"mode": "wait"}))
        last_output_at = [time.monotonic()]
        try:
            if stdin is not None and proc.stdin is not None:
                proc.stdin.write(stdin.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            async def pump(stream: asyncio.StreamReader | None, event: str) -> None:
                if stream is None:
                    return
                while True:
                    chunk = await stream.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    last_output_at[0] = time.monotonic()
                    await emit({"event": event, "data": chunk.decode("utf-8", errors="replace")})

            pumps = asyncio.gather(
                pump(proc.stdout, "stdout"),
                pump(proc.stderr, "stderr"),
            )
            pump_task = asyncio.ensure_future(pumps)
            deadline = time.monotonic() + timeout_seconds
            timed_out = False
            try:
                while True:
                    if pump_task.done():
                        await pump_task
                        break
                    now = time.monotonic()
                    if now >= deadline:
                        timed_out = True
                        break
                    if (
                        idle_timeout_seconds is not None
                        and (now - last_output_at[0]) >= idle_timeout_seconds
                    ):
                        timed_out = True
                        break
                    await asyncio.sleep(0.05)
            finally:
                if not pump_task.done():
                    pump_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pump_task
            if timed_out:
                await self._kill_proc(proc)
            await proc.wait()
            exit_code = proc.returncode if proc.returncode is not None else 1
            await emit({"event": "exit", "code": exit_code})
        finally:
            await self._untrack(container_id)
            await self._runsc_aux("delete", "--force", container_id)

    async def _run_stdio(
        self,
        cmd: list[str],
        container_id: str,
        req_id: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._track(container_id, proc)
        writer.write(_json_bytes(_ok(req_id, {"mode": "stdio"})))
        await writer.drain()

        async def sock_to_stdin() -> None:
            assert proc.stdin is not None
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            finally:
                with contextlib.suppress(Exception):
                    proc.stdin.close()

        async def stdout_to_sock() -> None:
            assert proc.stdout is not None
            while True:
                data = await proc.stdout.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()

        async def drain_stderr() -> None:
            if proc.stderr is None:
                return
            with contextlib.suppress(Exception):
                await proc.stderr.read()

        try:
            await asyncio.gather(sock_to_stdin(), stdout_to_sock(), drain_stderr())
            await proc.wait()
        finally:
            await self._untrack(container_id)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _delete(self, params: dict[str, Any]) -> None:
        container_id = self._require_container_id(params.get("container_id"))
        await self._kill_tracked(container_id)
        force = params.get("force", True)
        extra = ("--force",) if force else ()
        await self._runsc_aux("delete", *extra, container_id)

    async def _kill(self, params: dict[str, Any]) -> None:
        container_id = self._require_container_id(params.get("container_id"))
        sig = params.get("signal") or "SIGKILL"
        if not isinstance(sig, str) or sig not in _ALLOWED_SIGNALS:
            raise RpcDeniedError("signal not allowed")
        await self._kill_tracked(container_id)
        await self._runsc_aux("kill", container_id, sig)

    async def _kill_tracked(self, container_id: str) -> None:
        async with self._proc_lock:
            proc = self._procs.get(container_id)
        if proc is not None:
            await self._kill_proc(proc)

    async def _kill_proc(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2.0)

    async def _runsc_aux(self, *args: str) -> None:
        with contextlib.suppress(Exception):
            proc = await asyncio.create_subprocess_exec(
                self._runsc,
                f"--root={self._runtime_root}",
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30.0)
