"""Desktop client channel — route desktop-only ops to the bound Electron app."""

from agentcore.desktop.channel import (
    DesktopClientChannel,
    ExternalMountError,
    HostOp,
    HostOpError,
)

__all__ = [
    "DesktopClientChannel",
    "ExternalMountError",
    "HostOp",
    "HostOpError",
]
