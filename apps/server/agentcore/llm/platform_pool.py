"""In-memory snapshot of the platform credential pool (sync pick path).

``platform_llm_credentials`` is sync and is the unique selection point. Pool
members live in Postgres; this module holds a decrypted snapshot so the hot
path does not open a session. Admin CRUD and the boot/refresh loop reload
via :mod:`agentcore.llm.platform_credential_service`. Runtime cooling /
blocked flags live in :mod:`agentcore.llm.platform_pool_state` (Redis when
``RATE_LIMIT_BACKEND=redis``).

Empty / all-disabled snapshot → callers fall back to the env single key.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSurfaceLimits:
    """Declared upstream tool-surface caps. ``None`` on a field = unlimited."""

    max_tools: int | None = None
    max_properties_total: int | None = None
    max_properties_per_tool: int | None = None

    def is_unrestricted(self) -> bool:
        return (
            self.max_tools is None
            and self.max_properties_total is None
            and self.max_properties_per_tool is None
        )


@dataclass(frozen=True)
class PlatformPoolMember:
    """One decrypted pool member. ``id`` is ``platform_credential_id``."""

    id: str
    label: str
    api_key: str
    base_url: str
    subscription_day: int
    enabled: bool
    tool_surface_limits: ToolSurfaceLimits = field(default_factory=ToolSurfaceLimits)


_snapshot: tuple[PlatformPoolMember, ...] = ()


def iter_platform_pool_members() -> tuple[PlatformPoolMember, ...]:
    """All members in the last loaded snapshot (including disabled)."""
    return _snapshot


def pick_enabled_platform_pool_member() -> PlatformPoolMember | None:
    """First admin-enabled member (oldest ``created_at``), ignoring runtime state.

    Availability checks use this (a cooling member still means the pool exists).
    Call-time selection goes through :func:`pick_schedulable_platform_pool_member`.
    """
    for member in _snapshot:
        if member.enabled:
            return member
    return None


def replace_platform_pool_snapshot(
    members: tuple[PlatformPoolMember, ...] | list[PlatformPoolMember],
) -> None:
    """Replace the process snapshot (tests + reload)."""
    global _snapshot
    _snapshot = tuple(members)
