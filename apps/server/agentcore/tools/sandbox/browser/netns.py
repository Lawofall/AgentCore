"""Per-session network isolation for browser sandboxes.

The API process never execs ``ip``: netns setup/teardown and the boot probe go
through sandboxd (shape ``net``). ``browser_netns_health`` gates cloud
``browser_*`` assembly — fail-closed: only ``True`` assembles.
"""

from __future__ import annotations

import contextlib
import os
import sys

from agentcore.core.logging import get_logger
from agentcore.tools.sandbox.sandboxd.client import get_sandboxd_client
from agentcore.tools.sandbox.sandboxd.errors import SandboxdError, SandboxdUnavailableError

logger = get_logger(__name__)

NETNS_RUN_DIR = "/var/run/netns"

# None = never probed → fail-closed (do not assemble browser_*).
_browser_netns_healthy: bool | None = None


class NetnsError(RuntimeError):
    """A per-session netns / veth setup or teardown step failed."""


# Stable tool ``metadata.code`` when sandbox network isolation cannot be created.
# Permanent for the run: retrying browser_* will hit the same host capability gap.
EGRESS_UNAVAILABLE_CODE = "egress_unavailable"


def browser_netns_health() -> bool | None:
    """Cached netns capability: ``True`` / ``False``, or ``None`` if never probed."""
    return _browser_netns_healthy


def reset_browser_netns_health_for_tests() -> None:
    """Clear the process-wide cache so tests cannot leak health across cases."""
    global _browser_netns_healthy
    _browser_netns_healthy = None


def set_browser_netns_health_for_tests(healthy: bool | None) -> None:
    """Inject netns health for unit tests. ``None`` = unprobed."""
    global _browser_netns_healthy
    _browser_netns_healthy = healthy


def mark_browser_netns_unavailable() -> None:
    """Sticky: host netns proven unavailable → withhold cloud browser_* until restart."""
    global _browser_netns_healthy
    _browser_netns_healthy = False


def is_netns_capability_error(exc: BaseException) -> bool:
    """True when ``exc`` (or its cause chain) is a host netns / veth capability failure.

    Covers :class:`NetnsError` and the common wrapped form
    ``mkdir /run/netns … Permission denied`` that appears after generic Exception
    → BrowserSessionError wrapping.
    """
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, NetnsError):
            return True
        text = str(cur)
        if "NetnsError" in text or "mkdir /run/netns" in text:
            return True
        if "ip netns" in text and "Permission denied" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def chmod_netns_inode(name: str, *, run_dir: str = NETNS_RUN_DIR) -> None:
    """``ip netns add`` creates the inode as mode 0; non-root runsc must open it."""
    with contextlib.suppress(OSError):
        os.chmod(f"{run_dir}/{name}", 0o644)


async def probe_browser_netns_at_startup() -> None:
    """One-shot boot probe when gVisor browser path is config-enabled. Never raises.

    Shape B: ``sandboxd.health("net")``. ``SandboxdUnavailableError`` is fail-closed
    (``False``). Non-Linux / config-off leave the cache at ``None``.
    """
    global _browser_netns_healthy
    from agentcore.config import settings

    if sys.platform != "linux" or not settings.gvisor_enabled:
        return

    reason = "unhealthy"
    detail = ""
    try:
        ok, detail = await get_sandboxd_client().health("net")
        detail = detail[:200]
    except SandboxdUnavailableError as exc:
        ok = False
        reason = "sandboxd_unavailable"
        detail = str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 — probe must never break startup
        ok = False
        reason = type(exc).__name__
        detail = str(exc)[:200]

    _browser_netns_healthy = bool(ok)
    if ok:
        logger.debug("browser.netns_health_ok")
        return

    logger.warning(
        "browser.netns_health_failed",
        reason=reason,
        detail=detail or None,
        hint=(
            "云端 browser / package_install 将不装配，"
            "直到 sandboxd 形状 B（net）探针为 True（不回退 Local）"
        ),
    )


class SessionNetns:
    """Names / addresses for one session's isolated stack (slot-derived, no clashes)."""

    def __init__(self, *, slot: int, subnet_base: str) -> None:
        self.slot = slot
        self.subnet_base = subnet_base
        self.name = f"acbrw{slot}"
        self.host_ip = f"{subnet_base}.{slot}.1"
        self.sbx_ip = f"{subnet_base}.{slot}.2"
        self.path = f"{NETNS_RUN_DIR}/{self.name}"

    @property
    def netns_path(self) -> str:
        return self.path

    async def setup(self) -> None:
        """Ask sandboxd to create the netns + veth; never exec ``ip`` in the API."""
        try:
            info = await get_sandboxd_client().netns_setup(
                "browser", self.slot, self.subnet_base
            )
        except SandboxdError as exc:
            raise NetnsError(str(exc)) from exc
        self.name = info.name
        self.path = info.path
        self.host_ip = info.host_ip
        self.sbx_ip = info.sbx_ip
        logger.info("browser.netns_setup", netns=self.name, host_ip=self.host_ip)

    async def teardown(self) -> None:
        """Best-effort removal via sandboxd."""
        with contextlib.suppress(Exception):
            await get_sandboxd_client().netns_teardown("browser", self.slot)
