"""Ratchet: reload-visible settle usage is one key set, not a sidecar dialect."""

from __future__ import annotations

import re
from pathlib import Path

from agentcore.api.schemas.messages import RecordTurnRequest
from agentcore.conversation.store.cloud import _usage_metadata
from agentcore.conversation.store.usage_settle import (
    LOCAL_USAGE_EXTRA_KEYS,
    USAGE_SETTLE_INT_KEYS,
    USAGE_SETTLE_KEYS,
    USAGE_SETTLE_PASSTHROUGH_KEYS,
    USAGE_SETTLE_POSITIVE_INT_KEYS,
    USAGE_SETTLE_WIRE_INT_KEYS,
)

_DESKTOP_STRATEGY = (
    Path(__file__).resolve().parents[2]
    / "desktop"
    / "src"
    / "main"
    / "outbox"
    / "strategy.ts"
)
_STRING_LIT = re.compile(r'"([^"]+)"')


def _ts_const_tuple(src: str, name: str) -> tuple[str, ...]:
    m = re.search(
        rf"export const {re.escape(name)}\s*=\s*\[(.*?)\]\s*as const",
        src,
        flags=re.DOTALL,
    )
    if not m:
        raise AssertionError(f"TypeScript const {name!r} not found in strategy.ts")
    members = _STRING_LIT.findall(m.group(1))
    assert members, f"TypeScript const {name!r} parsed empty"
    return tuple(members)


def test_record_turn_request_carries_usage_settle_keys():
    missing = USAGE_SETTLE_KEYS - set(RecordTurnRequest.model_fields)
    assert not missing, f"RecordTurnRequest missing settle keys: {sorted(missing)}"


def test_usage_settle_int_keys_are_wire_plus_positive():
    assert USAGE_SETTLE_INT_KEYS == (
        USAGE_SETTLE_WIRE_INT_KEYS + USAGE_SETTLE_POSITIVE_INT_KEYS
    )


def test_desktop_writeback_mirrors_usage_settle_keys():
    src = _DESKTOP_STRATEGY.read_text(encoding="utf-8")
    assert _ts_const_tuple(src, "USAGE_SETTLE_WIRE_INT_KEYS") == USAGE_SETTLE_WIRE_INT_KEYS
    assert (
        _ts_const_tuple(src, "USAGE_SETTLE_POSITIVE_INT_KEYS")
        == USAGE_SETTLE_POSITIVE_INT_KEYS
    )
    assert (
        _ts_const_tuple(src, "USAGE_SETTLE_PASSTHROUGH_KEYS")
        == USAGE_SETTLE_PASSTHROUGH_KEYS
    )


def test_usage_metadata_omits_absent_token_keys():
    meta = _usage_metadata(
        {"finish_reason": "paused"},
        status="running",
        extra={"paused": True},
    )
    assert meta["status"] == "running"
    assert meta["paused"] is True
    assert meta["finish_reason"] == "paused"
    for key in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "rounds",
    ):
        assert key not in meta


def test_usage_metadata_projects_prompt_tokens_to_last_prompt():
    meta = _usage_metadata(
        {
            "input_tokens": 10,
            "output_tokens": 4,
            "prompt_tokens": 120_000,
            "collab": {"boundary_yields": 1},
            "outcome": "ok",
            "finish_reason": "end_turn",
        },
        status="complete",
    )
    assert meta["last_prompt_tokens"] == 120_000
    assert "prompt_tokens" not in meta
    assert meta["collab"] == {"boundary_yields": 1}
    assert meta["outcome"] == "ok"
    assert LOCAL_USAGE_EXTRA_KEYS.isdisjoint(meta)
