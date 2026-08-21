"""Allowlisted ``ip netns`` / veth ops — the only ``ip`` argv sandboxd will exec."""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass

from agentcore.tools.sandbox.sandboxd.protocol import NetFamily

NETNS_RUN_DIR = "/var/run/netns"
PROBE_NETNS_NAME = "acsbxdprobe"


class NetnsOpsError(RuntimeError):
    """An ``ip`` netns / veth step failed."""


@dataclass(frozen=True, slots=True)
class NetnsSpec:
    family: NetFamily
    slot: int
    name: str
    path: str
    host_ip: str
    sbx_ip: str
    veth_host: str
    veth_sbx: str
    cidr: str = "24"


def spec_for(
    family: NetFamily,
    slot: int,
    subnet_base: str,
    *,
    run_dir: str = NETNS_RUN_DIR,
) -> NetnsSpec:
    if family == "browser":
        prefix, host, sbx = "acbrw", "acbrwh", "acbrws"
    elif family == "package":
        prefix, host, sbx = "acpkg", "acpkgh", "acpkgs"
    else:
        raise NetnsOpsError(f"unknown netns family: {family}")
    name = f"{prefix}{slot}"
    return NetnsSpec(
        family=family,
        slot=slot,
        name=name,
        path=f"{run_dir}/{name}",
        host_ip=f"{subnet_base}.{slot}.1",
        sbx_ip=f"{subnet_base}.{slot}.2",
        veth_host=f"{host}{slot}",
        veth_sbx=f"{sbx}{slot}",
    )


def chmod_netns_inode(name: str, *, run_dir: str = NETNS_RUN_DIR) -> None:
    """``ip netns add`` creates the inode as mode 0; open it after."""
    with contextlib.suppress(OSError):
        os.chmod(f"{run_dir}/{name}", 0o644)


async def _ip(*args: str, check: bool = True) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ip",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise NetnsOpsError(f"ip {' '.join(args)} failed ({proc.returncode}): {text.strip()}")
    return proc.returncode or 0, text


async def setup(spec: NetnsSpec) -> None:
    await teardown(spec)
    await _ip("netns", "add", spec.name)
    chmod_netns_inode(spec.name, run_dir=os.path.dirname(spec.path) or NETNS_RUN_DIR)
    await _ip("link", "add", spec.veth_host, "type", "veth", "peer", "name", spec.veth_sbx)
    await _ip("link", "set", spec.veth_sbx, "netns", spec.name)
    await _ip("addr", "add", f"{spec.host_ip}/{spec.cidr}", "dev", spec.veth_host)
    await _ip("link", "set", spec.veth_host, "up")
    await _ip("-n", spec.name, "addr", "add", f"{spec.sbx_ip}/{spec.cidr}", "dev", spec.veth_sbx)
    await _ip("-n", spec.name, "link", "set", spec.veth_sbx, "up")
    await _ip("-n", spec.name, "link", "set", "lo", "up")
    await _ip("-n", spec.name, "route", "add", "default", "via", spec.host_ip)


async def teardown(spec: NetnsSpec) -> None:
    await _ip("netns", "del", spec.name, check=False)
    await _ip("link", "del", spec.veth_host, check=False)


async def probe_setup(name: str = PROBE_NETNS_NAME, *, run_dir: str = NETNS_RUN_DIR) -> str:
    """Minimal probe netns (add + lo up). Not a Chromium session."""
    await probe_teardown(name)
    await _ip("netns", "add", name)
    chmod_netns_inode(name, run_dir=run_dir)
    await _ip("-n", name, "link", "set", "lo", "up")
    return f"{run_dir}/{name}"


async def probe_teardown(name: str = PROBE_NETNS_NAME) -> None:
    await _ip("netns", "del", name, check=False)
