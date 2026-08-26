"""Readiness for packaging registry egress (honest preflight)."""

from __future__ import annotations

import sys

from agentcore.config import settings

# Align metadata.code with browser egress hard-fail when isolation cannot be created.
EGRESS_UNAVAILABLE_CODE = "egress_unavailable"


def registry_egress_available() -> bool:
    """True when the host can start a desk-resident packaging chokepoint.

    Linux + gVisor config + cloud sandbox health is not a known failure.
    Unprobed (``None``) is fail-open like ``code_execute`` assembly.
    """
    if sys.platform != "linux" or not settings.gvisor_enabled:
        return False
    from agentcore.tools.sandbox.cloud_health import cloud_sandbox_health

    return cloud_sandbox_health() is not False
