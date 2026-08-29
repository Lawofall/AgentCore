"""Test-only sandboxd client: exec allowlisted runsc argv (never used in production)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agentcore.tools.sandbox.sandboxd.argv import build_runsc_cmd, build_runsc_exec_cmd
from agentcore.tools.sandbox.sandboxd.client import SandboxdClient
from agentcore.tools.sandbox.sandboxd.errors import SandboxdError
from agentcore.tools.sandbox.sandboxd.protocol import (
    NetFamily,
    NetnsInfo,
    Shape,
)


class LoopbackRunscClient(SandboxdClient):
    """Drive a fake ``runsc`` binary the way production sandboxd would.

    Used by desk-guest tests that install a stub binary. Production API must
    keep :class:`~agentcore.tools.sandbox.sandboxd.client.UnixSandboxdClient`.
    """

    def __init__(self, *, runsc_path: str, runtime_root: str) -> None:
        self._runsc = runsc_path
        self._runtime_root = runtime_root
        self._bundles: dict[str, str] = {}

    async def ping(self) -> None:
        return None

    async def health(self, shape: Shape) -> tuple[bool, str]:
        return True, ""

    async def netns_setup(
        self, family: NetFamily, slot: int, subnet_base: str
    ) -> NetnsInfo:
        if family != "package":
            raise SandboxdError("family must be package")
        name = f"acpkg{slot}"
        return NetnsInfo(
            family=family,
            slot=slot,
            name=name,
            path=f"/var/run/netns/{name}",
            host_ip=f"{subnet_base}.{slot}.1",
            sbx_ip=f"{subnet_base}.{slot}.2",
        )

    async def netns_teardown(self, family: NetFamily, slot: int) -> None:
        return None

    async def start_detach(
        self,
        *,
        bundle_dir: str,
        container_id: str,
        netns_path: str,
        **_kwargs: object,
    ) -> None:
        self._bundles[container_id] = bundle_dir
        cmd = build_runsc_cmd(
            runsc_path=self._runsc,
            runtime_root=self._runtime_root,
            bundle_dir=bundle_dir,
            container_id=container_id,
            detach=True,
        )
        code, _out, err = await self._invoke(cmd, timeout_seconds=30.0)
        if code != 0:
            raise SandboxdError(err or "loopback start-detach failed")

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
        bundle = self._bundles.get(container_id)
        cmd = build_runsc_exec_cmd(
            runsc_path=self._runsc,
            runtime_root=self._runtime_root,
            container_id=container_id,
            argv=argv,
            cwd=cwd,
            env=env,
        )
        if bundle:
            # Stub binary still locates the workspace bind via --bundle=.
            cmd = [cmd[0], f"--bundle={bundle}", *cmd[1:]]
        return await self._invoke(
            cmd,
            timeout_seconds=timeout_seconds,
            stdin=stdin,
            on_output=on_output,
        )

    async def exec_stdio(self, **kwargs: Any) -> Any:
        raise SandboxdError("loopback client has no exec_stdio mode")

    async def _invoke(
        self,
        cmd: list[str],
        *,
        timeout_seconds: float,
        stdin: str | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin else None),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise SandboxdError("loopback runsc timeout", code="sandboxd_timeout") from exc
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if on_output:
            if stdout:
                on_output("stdout", stdout)
            if stderr:
                on_output("stderr", stderr)
        return proc.returncode or 0, stdout, stderr

    async def delete(self, container_id: str, *, force: bool = True) -> None:
        self._bundles.pop(container_id, None)
        proc = await asyncio.create_subprocess_exec(
            self._runsc,
            "delete",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def kill(self, container_id: str, signal: str = "SIGKILL") -> None:
        proc = await asyncio.create_subprocess_exec(
            self._runsc,
            "kill",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
