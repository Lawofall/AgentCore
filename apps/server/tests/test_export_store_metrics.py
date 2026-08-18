"""Export-dir turn_metrics / cost_events join for decision_spine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcore.observability.query.store import ExportConversationStore


@pytest.mark.asyncio
async def test_export_store_joins_turn_metrics_and_cost(tmp_path: Path) -> None:
    tid = "d" * 32
    (tmp_path / "turn_metrics.jsonl").write_text(
        json.dumps(
            {
                "id": "m1",
                "turn_id": "t1",
                "conversation_id": "c1",
                "user_id": "u1",
                "trace_id": tid,
                "kind": "turn",
                "mode": "cloud",
                "status": "ok",
                "finish_reason": "stop",
                "delegated": True,
                "workers": 2,
                "rounds": 3,
                "duration_ms": 100,
                "input_tokens": 1,
                "output_tokens": 2,
                "boundary_yields": 0,
                "scope_signals": 0,
                "revises": 1,
                "escalations": 0,
                "created_at": "2026-07-31T10:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "cost_events.jsonl").write_text(
        json.dumps(
            {
                "trace_id": tid,
                "model": "demo",
                "cost_total_nano": 50,
                "currency": "USD",
            }
        )
        + "\n"
        + json.dumps(
            {
                "trace_id": tid,
                "model": "demo",
                "cost_total_nano": 25,
                "currency": "USD",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = ExportConversationStore(tmp_path)
    metrics = await store.get_turn_metrics_by_trace(tid)
    assert metrics is not None
    assert metrics["workers"] == 2
    assert metrics["revises"] == 1
    assert metrics["mode"] == "cloud"
    cost = await store.get_cost_by_trace(tid)
    assert cost is not None
    assert cost["total_nano"] == 75
    assert cost["estimated_nano"] == 0
    assert cost["billing"] == "platform"
    assert cost["runs"] == 2
    assert await store.get_turn_metrics_by_trace("missing") is None


@pytest.mark.asyncio
async def test_export_store_cost_surfaces_byok_estimate(tmp_path: Path) -> None:
    tid = "e" * 32
    (tmp_path / "cost_events.jsonl").write_text(
        json.dumps(
            {
                "trace_id": tid,
                "model": "demo",
                "cost_total_nano": 0,
                "cost_estimated_nano": 1_200,
                "currency": "USD",
                "cost": {"credential_source": "user"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "trace_id": tid,
                "model": "demo",
                "cost_total_nano": 0,
                "cost_estimated_nano": 300,
                "currency": "USD",
                "cost": {"credential_source": "user"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = ExportConversationStore(tmp_path)
    cost = await store.get_cost_by_trace(tid)
    assert cost is not None
    assert cost["total_nano"] == 0
    assert cost["estimated_nano"] == 1500
    assert cost["billing"] == "BYOK"
    assert cost["estimated_currency"] == "USD"
    assert cost["runs"] == 2
