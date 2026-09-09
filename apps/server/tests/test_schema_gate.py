"""Unit tests for the migration↔ORM schema gate (offline; no database)."""

from __future__ import annotations

from agentcore.db import schema_gate as sg


def test_offline_gate_passes_on_current_tree():
    result = sg.run_offline_checks()
    assert result.ok, result.errors
    assert len(result.heads) == 1


def test_net_tombstones_include_july20_drops():
    tables, columns = sg.net_tombstones()
    assert "user_llm_keys" in tables
    assert ("users", "billing_preference") in columns


def test_net_tombstones_include_dropped_skill_slot_overlay():
    tables, _columns = sg.net_tombstones()
    assert "skill_slot_replacements" in tables
    assert "skill_slot_homes" in tables


def test_simulate_stale_orm_fails():
    result = sg.run_offline_checks(simulate_stale_orm=True)
    assert not result.ok
    assert any("billing_preference" in e for e in result.errors)


def test_orm_rejects_dropped_table(monkeypatch):
    tables, columns = sg.net_tombstones()
    real_tables = sg._orm_tables()
    monkeypatch.setattr(sg, "_orm_tables", lambda: {"user_llm_keys", *real_tables})
    errors = sg.check_orm_against_tombstones(tables | {"user_llm_keys"}, columns)
    assert any("user_llm_keys" in e for e in errors)


def test_multiple_heads_fails(monkeypatch):
    monkeypatch.setattr(sg, "script_heads", lambda: ["aaa", "bbb"])
    result = sg.run_offline_checks()
    assert not result.ok
    assert any("multiple Alembic heads" in e for e in result.errors)
