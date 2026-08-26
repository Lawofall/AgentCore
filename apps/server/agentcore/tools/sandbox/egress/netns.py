"""Per-install-run network isolation — packaging egress (package family only).

The API process never execs ``ip``: setup/teardown go through sandboxd.
"""

from __future__ import annotations

import contextlib

from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.sandboxd.client import get_sandboxd_client
from agentcore.tools.sandbox.sandboxd.errors import SandboxdError

logger = get_logger(__name__)

NETNS_RUN_DIR = "/var/run/netns"


class PackageNetnsError(RuntimeError):
    """A packaging netns / veth setup or teardown step failed."""


class PackageNetns:
    """Names / addresses for one install-run isolated stack (slot-derived)."""

    def __init__(self, *, slot: int, subnet_base: str) -> None:
        self.slot = slot
        self.subnet_base = subnet_base
        self.name = f"acpkg{slot}"
        self.host_ip = f"{subnet_base}.{slot}.1"
        self.sbx_ip = f"{subnet_base}.{slot}.2"
        self.path = f"{NETNS_RUN_DIR}/{self.name}"

    @property
    def netns_path(self) -> str:
        return self.path

    async def setup(self) -> None:
        try:
            info = await get_sandboxd_client().netns_setup(
                "package", self.slot, self.subnet_base
            )
        except SandboxdError as exc:
            raise PackageNetnsError(str(exc)) from exc
        self.name = info.name
        self.path = info.path
        self.host_ip = info.host_ip
        self.sbx_ip = info.sbx_ip
        logger.info("package.netns_setup", netns=self.name, host_ip=self.host_ip)

    async def teardown(self) -> None:
        with contextlib.suppress(Exception):
            await get_sandboxd_client().netns_teardown("package", self.slot)
