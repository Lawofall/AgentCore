"""Lease package — durable RUNNING ownership for crash recover.

Sweeper symbols are lazy-exported: eager import pulls ``conversation.store`` and
cycles back through ``db.repositories`` when this package is loaded for the repo
re-export alone.
"""

from typing import Any

from agentcore.db.models.runs import TurnLeaseRow
from agentcore.runtime.leases.repo import TurnLeaseRepository
from agentcore.runtime.leases.service import (
    LOCAL_TURN_LEASE_OWNER_PREFIX,
    acquire_turn_lease,
    heartbeat_turn_lease,
    is_local_turn_lease,
    lease_heartbeat_loop,
    lease_owner_id,
    list_fresh_conversation_ids_for_user,
    local_turn_lease_owner_id,
    orphan_turn_lease,
    release_turn_lease,
)

_SWEEPER_EXPORTS = frozenset(
    {
        "run_turn_lease_sweep",
        "salvage_no_dag_turn",
        "turn_lease_sweep_loop",
    }
)

__all__ = [
    "TurnLeaseRow",
    "TurnLeaseRepository",
    "LOCAL_TURN_LEASE_OWNER_PREFIX",
    "acquire_turn_lease",
    "heartbeat_turn_lease",
    "is_local_turn_lease",
    "lease_heartbeat_loop",
    "lease_owner_id",
    "list_fresh_conversation_ids_for_user",
    "local_turn_lease_owner_id",
    "orphan_turn_lease",
    "release_turn_lease",
    "run_turn_lease_sweep",
    "salvage_no_dag_turn",
    "turn_lease_sweep_loop",
]


def __getattr__(name: str) -> Any:
    if name in _SWEEPER_EXPORTS:
        from agentcore.runtime.leases import sweeper as _sweeper

        return getattr(_sweeper, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
