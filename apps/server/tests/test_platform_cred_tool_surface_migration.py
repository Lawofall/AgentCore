"""Alembic upgrade/downgrade for platform_credentials.tool_surface_limits."""

from __future__ import annotations

from unittest.mock import MagicMock

from agentcore.db.migrations.versions import (
    c2f9a1e4b7d8_platform_cred_tool_surface_limits as mig,
)


def test_upgrade_adds_jsonb_object_column(monkeypatch):
    add = MagicMock()
    check = MagicMock()
    monkeypatch.setattr(mig.op, "add_column", add)
    monkeypatch.setattr(mig.op, "create_check_constraint", check)

    mig.upgrade()

    add.assert_called_once()
    assert add.call_args.args[0] == "platform_credentials"
    col = add.call_args.args[1]
    assert col.name == "tool_surface_limits"
    check.assert_called_once()
    assert check.call_args.args[0] == "ck_platform_credentials_tool_surface_limits_object"


def test_downgrade_drops_constraint_then_column(monkeypatch):
    drop_c = MagicMock()
    drop_col = MagicMock()
    monkeypatch.setattr(mig.op, "drop_constraint", drop_c)
    monkeypatch.setattr(mig.op, "drop_column", drop_col)

    mig.downgrade()

    drop_c.assert_called_once_with(
        "ck_platform_credentials_tool_surface_limits_object",
        "platform_credentials",
        type_="check",
    )
    drop_col.assert_called_once_with("platform_credentials", "tool_surface_limits")
    assert drop_c.call_args.args[0] == "ck_platform_credentials_tool_surface_limits_object"
    assert drop_col.call_count == 1
