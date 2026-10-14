"""Historical execution_harvest unique index: conflict helper still names the constraint."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from agentcore.db.models.conversations import (
    UQ_MESSAGES_EXECUTION_HARVEST,
    is_execution_harvest_conflict,
)


def test_is_execution_harvest_conflict_reads_constraint_name():
    assert is_execution_harvest_conflict(
        IntegrityError("INSERT", {}, Exception(UQ_MESSAGES_EXECUTION_HARVEST))
    )
    assert not is_execution_harvest_conflict(
        IntegrityError("INSERT", {}, Exception("messages_pkey"))
    )
