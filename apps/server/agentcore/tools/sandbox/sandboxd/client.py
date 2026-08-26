"""API-side client for the sandboxd Unix socket. Never execs ``runsc`` / ``ip``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Callable
from typing import Any

from agentcore.tools.sandbox.sandboxd.errors import (
    SandboxdError,
    SandboxdRpcError,
    SandboxdUnavailableError,
)
from agentcore.tools.sandbox.sandboxd.protocol import (
    DEFAULT_SOCKET_PATH,
    METHOD_DELETE,
    METHOD_EXEC,
    METHOD_HEALTH,
    METHOD_KILL,
    METHOD_NETNS_SETUP,
    METHOD_NETNS_TEARDOWN,
    METHOD_PING,
    METHOD_RUN,
    NetFamily,
    NetnsInfo,
    Shape,
)

_CONNECT_TIMEOUT = 2.0
_RPC_TIMEOUT = 30.0

_injected: SandboxdClient | None = None
_req_id = 0


def set_sandboxd_client_for_tests(client: SandboxdClient | None) -> None:
    global _injected
    _injected = client


def reset_sandboxd_client_for_tests() -> None:
    set_sandboxd_client_for_tests(None)


def get_sandboxd_client() -> SandboxdClient:
    if _injected is not None:
        return _injected
    from agentcore.config import settings

    path = getattr(settings, "sandboxd_socket", None) or DEFAULT_SOCKET_PATH
    return UnixSandboxdClient(path)


def next_request_id() -> int:
    global _req_id
    _req_id += 1
    return _req_id


class SandboxdStdio:
    """Raw splice to ``runsc`` stdin/stdout after a ``mode=stdio`` run."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        container_id: str,
        client: UnixSandboxdClient,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.container_id = container_id
        self._client = client
        self._closed = False

    async def write(self, data: bytes) -> None:
        if self._closed:
            raise SandboxdUnavailableError("stdio closed")
        self._writer.write(data)
        await self._writer.drain()

    async def readline(self) -> bytes:
        if self._closed:
            return b""
        return await self._reader.readline()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    async def wait_exit(self, timeout: float) -> bool:
        """Best-effort: stdio EOF means the supervisor is gone."""
        try:
            await asyncio.wait_for(self._reader.read(), timeout=timeout)
        except TimeoutError:
            return False
        except Exception:
            return True
        return True

    async def kill(self) -> None:
        await self._client.kill(self.container_id)


class SandboxdClient:
    """Structural interface; production impl is :class:`UnixSandboxdClient`."""

    async def ping(self) -> None:
        raise NotImplementedError

    async def health(self, shape: Shape) -> tuple[bool, str]:
        raise NotImplementedError

    async def netns_setup(
        self, family: NetFamily, slot: int, subnet_base: str
    ) -> NetnsInfo:
        raise NotImplementedError

    async def netns_teardown(self, family: NetFamily, slot: int) -> None:
        raise NotImplementedError

    async def start_detach(
        self,
        *,
        bundle_dir: str,
        container_id: str,
        netns_path: str,
    ) -> None:
        raise NotImplementedError

    async def exec_wait(
        self,
        *,
        container_id: str,
        argv: list[str],
        cwd: str = "/workspace",
        env: list[str] | None = None,
        timeout_seconds: float = 60.0,
        idle_timeout_seconds: float | None = None,
        stdin: str | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> tuple[int, str, str]:
        raise NotImplementedError

    async def exec_stdio(
        self,
        *,
        container_id: str,
        argv: list[str],
        cwd: str = "/workspace",
        env: list[str] | None = None,
    ) -> SandboxdStdio:
        raise NotImplementedError

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        raise NotImplementedError

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        raise NotImplementedError


class UnixSandboxdClient(SandboxdClient):
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if sys.platform == "win32" or not hasattr(asyncio, "open_unix_connection"):
            raise SandboxdUnavailableError("sandboxd Unix socket 仅 Linux")
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=_CONNECT_TIMEOUT,
            )
        except (TimeoutError, OSError, FileNotFoundError) as exc:
            raise SandboxdUnavailableError(
                f"无法连接 sandboxd（{self.socket_path}）"
            ) from exc

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await self._connect()
        try:
            payload = json.dumps(
                {"id": next_request_id(), "method": method, "params": params},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            writer.write(payload.encode("utf-8") + b"\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=_RPC_TIMEOUT)
            if not raw:
                raise SandboxdUnavailableError("sandboxd 关闭了控制连接")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SandboxdError("sandboxd 返回非 JSON") from exc
            if not msg.get("ok", False):
                raise SandboxdRpcError(
                    str(msg.get("error") or "sandboxd rpc failed"),
                    code=str(msg.get("code") or "sandboxd_rpc"),
                )
            result = msg.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def ping(self) -> None:
        await self._rpc(METHOD_PING, {})

    async def health(self, shape: Shape) -> tuple[bool, str]:
        result = await self._rpc(METHOD_HEALTH, {"shape": shape})
        ok = bool(result.get("ok", False))
        return ok, str(result.get("detail") or "")

    async def netns_setup(
        self, family: NetFamily, slot: int, subnet_base: str
    ) -> NetnsInfo:
        result = await self._rpc(
            METHOD_NETNS_SETUP,
            {"family": family, "slot": slot, "subnet_base": subnet_base},
        )
        return NetnsInfo(
            family=family,
            slot=int(result["slot"]),
            name=str(result["name"]),
            path=str(result["path"]),
            host_ip=str(result["host_ip"]),
            sbx_ip=str(result["sbx_ip"]),
        )

    async def netns_teardown(self, family: NetFamily, slot: int) -> None:
        await self._rpc(
            METHOD_NETNS_TEARDOWN, {"family": family, "slot": slot}
        )

    async def start_detach(
        self,
        *,
        bundle_dir: str,
        container_id: str,
        netns_path: str,
    ) -> None:
        await self._rpc(
            METHOD_RUN,
            {
                "shape": "net",
                "mode": "detach",
                "bundle_dir": bundle_dir,
                "container_id": container_id,
                "netns_path": netns_path,
            },
        )

    async def exec_wait(
        self,
        *,
        container_id: str,
        argv: list[str],
        cwd: str = "/workspace",
        env: list[str] | None = None,
        timeout_seconds: float = 60.0,
        idle_timeout_seconds: float | None = None,
        stdin: str | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> tuple[int, str, str]:
        reader, writer = await self._connect()
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        try:
            payload = json.dumps(
                {
                    "id": next_request_id(),
                    "method": METHOD_EXEC,
                    "params": {
                        "container_id": container_id,
                        "argv": argv,
                        "cwd": cwd,
                        "env": env,
                        "timeout_seconds": timeout_seconds,
                        "idle_timeout_seconds": idle_timeout_seconds,
                        "stdin": stdin,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            writer.write(payload.encode("utf-8") + b"\n")
            await writer.drain()
            first = await asyncio.wait_for(reader.readline(), timeout=_RPC_TIMEOUT)
            if not first:
                raise SandboxdUnavailableError("sandboxd 关闭了 exec 连接")
            header = json.loads(first)
            if not header.get("ok", False):
                raise SandboxdRpcError(
                    str(header.get("error") or "exec failed"),
                    code=str(header.get("code") or "sandboxd_rpc"),
                )
            return await _read_wait_stream(
                reader,
                timeout_seconds=timeout_seconds,
                on_output=on_output,
                stdout_buf=stdout_buf,
                stderr_buf=stderr_buf,
            )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def exec_stdio(
        self,
        *,
        container_id: str,
        argv: list[str],
        cwd: str = "/workspace",
        env: list[str] | None = None,
    ) -> SandboxdStdio:
        reader, writer = await self._connect()
        payload = json.dumps(
            {
                "id": next_request_id(),
                "method": METHOD_EXEC,
                "params": {
                    "container_id": container_id,
                    "mode": "stdio",
                    "argv": argv,
                    "cwd": cwd,
                    "env": env,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        writer.write(payload.encode("utf-8") + b"\n")
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), timeout=_RPC_TIMEOUT)
        if not first:
            writer.close()
            raise SandboxdUnavailableError("sandboxd 关闭了 exec stdio 连接")
        header = json.loads(first)
        if not header.get("ok", False):
            writer.close()
            raise SandboxdRpcError(
                str(header.get("error") or "exec stdio failed"),
                code=str(header.get("code") or "sandboxd_rpc"),
            )
        result = header.get("result")
        result_dict = result if isinstance(result, dict) else {}
        exec_id = str(result_dict.get("exec_id") or "")
        if not exec_id:
            writer.close()
            raise SandboxdError("exec stdio missing exec_id")
        return SandboxdStdio(reader, writer, container_id=exec_id, client=self)

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        await self._rpc(METHOD_DELETE, {"container_id": container_id, "force": force})

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        await self._rpc(METHOD_KILL, {"container_id": container_id, "signal": signal})


async def _read_wait_stream(
    reader: asyncio.StreamReader,
    *,
    timeout_seconds: float,
    on_output: Callable[[str, str], None] | None,
    stdout_buf: list[str],
    stderr_buf: list[str],
) -> tuple[int, str, str]:
    deadline = asyncio.get_event_loop().time() + max(timeout_seconds, 1.0) + 5.0
    exit_code = 1
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise SandboxdError("sandboxd run 等待超时", code="sandboxd_timeout")
        raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not raw:
            break
        event = json.loads(raw)
        kind = event.get("event")
        if kind in ("stdout", "stderr"):
            chunk = str(event.get("data") or "")
            (stdout_buf if kind == "stdout" else stderr_buf).append(chunk)
            if on_output and chunk:
                on_output(kind, chunk)
        elif kind == "exit":
            exit_code = int(event.get("code") or 1)
            break
    return exit_code, "".join(stdout_buf), "".join(stderr_buf)
