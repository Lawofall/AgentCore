"""Billing-quota skip cards reuse the memory_updates quota shell with dedup."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.memory import billing_quota_card as mod


def _billing_row() -> SimpleNamespace:
    return SimpleNamespace(
        id="row-1",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        kind="quota",
        summary=mod._CARD_SUMMARY,
        items=[{"action": "quota", "content": mod._BILLING_QUOTA_FINGERPRINT}],
    )


@pytest.mark.asyncio
async def test_record_billing_quota_skip_card_once_writes_summary_only_quota_card(monkeypatch):
    recorded: list[dict] = []
    anchor = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)

    class _Repo:
        async def list_for_conversation(self, conversation_id: str, *, limit: int = 20):
            return []

        async def record(self, **kwargs):
            recorded.append(kwargs)
            return _billing_row()

    monkeypatch.setattr(mod, "MemoryUpdateRepository", lambda _session: _Repo())

    row = await mod.record_billing_quota_skip_card_once(
        MagicMock(),
        user_id="u1",
        conversation_id="c1",
        anchor_at=anchor,
    )

    assert row is not None
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "quota"
    assert recorded[0]["summary"] == mod._CARD_SUMMARY
    assert recorded[0]["anchor_at"] == anchor
    assert recorded[0]["items"][0]["content"] == mod._BILLING_QUOTA_FINGERPRINT


@pytest.mark.asyncio
async def test_record_billing_quota_skip_card_suppresses_repeat_in_same_conversation(monkeypatch):
    existing = _billing_row()

    class _Repo:
        async def list_for_conversation(self, conversation_id: str, *, limit: int = 20):
            return [existing]

        async def record(self, **kwargs):
            raise AssertionError("second card must be suppressed")

    monkeypatch.setattr(mod, "MemoryUpdateRepository", lambda _session: _Repo())

    row = await mod.record_billing_quota_skip_card_once(
        MagicMock(), user_id="u1", conversation_id="c1"
    )
    assert row is None


@pytest.mark.asyncio
async def test_notify_billing_quota_skip_publishes_memory_updated(monkeypatch):
    published: list[dict] = []

    class _Hub:
        async def publish(self, user_ids, event):
            published.append(event)

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(mod, "default_chat_hub", lambda: _Hub())
    monkeypatch.setattr(mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(
        mod,
        "record_billing_quota_skip_card_once",
        AsyncMock(return_value=_billing_row()),
    )

    await mod.notify_billing_quota_skip("u1", "c1")

    assert len(published) == 1
    assert published[0]["type"] == "memory_updated"
    assert published[0]["kind"] == "quota"
    assert published[0]["conversation_id"] == "c1"
