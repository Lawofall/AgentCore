"""Errors for the sandboxd Unix control socket."""

from __future__ import annotations


class SandboxdError(Exception):
    """Base error for sandboxd RPC / transport failures."""

    def __init__(self, message: str, *, code: str = "sandboxd_error") -> None:
        super().__init__(message)
        self.code = code


class SandboxdUnavailable(SandboxdError):
    """Socket missing, connect refused, or peer not the daemon — fail-closed."""

    def __init__(self, message: str = "sandboxd 不可用") -> None:
        super().__init__(message, code="sandboxd_unavailable")


class SandboxdRpcError(SandboxdError):
    """Daemon returned ok=false for a control method."""
