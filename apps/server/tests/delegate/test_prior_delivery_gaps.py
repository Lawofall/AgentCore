"""Cross-turn partial/blocked delivery → one-shot prior_delivery_gaps soft block."""

from __future__ import annotations

import pytest

from agentcore.runtime.delegate.prior_delivery_gaps import (
    apply_gaps_vs_redispatch_mutex,
    extract_prior_turn_delivery_status,
    prior_turn_has_blocking_delivery_gaps,
    render_prior_delivery_gaps,
)
from agentcore.runtime.events.types import EventType
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal.entries import KIND_TURN_END


def _delivery(
    *,
    state: str,
    execution_id: str = "exec-1",
    delivered_files: list[str] | None = None,
    gaps: list[dict] | None = None,
) -> dict:
    return {
        "kind": EventType.DELIVERY_STATUS.value,
        "payload": {
            "execution_id": execution_id,
            "state": state,
            "summary": "test",
            "delivered_files": list(delivered_files or []),
            "gaps": list(gaps or []),
            "actions": [],
            "artifacts": [],
        },
        "ts": None,
    }


def _blocking_gap(*, role: str = "验证员", description: str = "测试未绿", reason: str = "verify_failed") -> dict:
    return {"role": role, "description": description, "reason": reason}


def _warning_gap(*, role: str = "作者", description: str = "待核实备注") -> dict:
    return {
        "role": role,
        "description": description,
        "severity": "warning",
        "reason": "unverified_note",
    }


def test_no_gaps_when_journal_empty_or_clean():
    assert prior_turn_has_blocking_delivery_gaps(None) is False
    assert prior_turn_has_blocking_delivery_gaps([]) is False
    assert (
        prior_turn_has_blocking_delivery_gaps(
            [
                {
                    "kind": KIND_TURN_END,
                    "payload": {"finish_reason": "end_turn"},
                }
            ]
        )
        is False
    )
    assert (
        prior_turn_has_blocking_delivery_gaps(
            [
                _delivery(
                    state="delivered",
                    delivered_files=["a.md"],
                    gaps=[],
                )
            ]
        )
        is False
    )


def test_warning_only_does_not_inject():
    """notes / warning-only gaps must not trip the soft ledger."""
    assert (
        prior_turn_has_blocking_delivery_gaps(
            [
                _delivery(
                    state="notes",
                    gaps=[_warning_gap()],
                )
            ]
        )
        is False
    )
    # Even if state were mislabeled partial with only warnings — still no blocking rows.
    assert (
        prior_turn_has_blocking_delivery_gaps(
            [
                _delivery(
                    state="partial",
                    delivered_files=["a.md"],
                    gaps=[_warning_gap()],
                )
            ]
        )
        is False
    )


def test_partial_with_blocking_gaps_injects():
    entries = [
        _delivery(
            state="partial",
            execution_id="exec-abc",
            delivered_files=["docs/out.md"],
            gaps=[_warning_gap(), _blocking_gap()],
        )
    ]
    assert prior_turn_has_blocking_delivery_gaps(entries) is True
    payload = extract_prior_turn_delivery_status(entries)
    assert payload is not None
    text = render_prior_delivery_gaps(payload)
    assert "<prior_delivery_gaps>" in text
    assert "</prior_delivery_gaps>" in text
    assert "state=partial" in text
    assert "execution_id=" not in text
    assert "exec-abc" not in text
    assert "docs/out.md" in text
    assert "role=验证员" in text
    assert "测试未绿" in text
    assert "reason=verify_failed" in text
    assert "一次性" in text and "可忽略" in text
    assert "新目标优先" in text
    assert "continue_from_run_id" in text
    assert "整锅重派" in text
    assert "路径已核" in text


def test_blocked_with_blocking_gaps_injects():
    entries = [
        _delivery(
            state="blocked",
            gaps=[_blocking_gap(role="工程师", description="契约未满足", reason="")],
        )
    ]
    assert prior_turn_has_blocking_delivery_gaps(entries) is True
    text = render_prior_delivery_gaps(extract_prior_turn_delivery_status(entries) or {})
    assert "state=blocked" in text
    line = next(ln for ln in text.splitlines() if ln.startswith("- role=工程师"))
    assert "契约未满足" in line
    # Empty reason omitted from the gap line.
    assert "reason=" not in line


def test_one_shot_fingerprint_uses_latest_delivery_in_prior_journal():
    """Shape: read prior-turn journal entries; keep last delivery_status only."""
    entries = [
        _delivery(state="delivered", execution_id="old", gaps=[]),
        {
            "kind": FactKind.TOOL_CALL.value,
            "payload": {"name": "delegate", "success": True, "result": "ok"},
        },
        _delivery(
            state="partial",
            execution_id="new",
            delivered_files=["x.py"],
            gaps=[_blocking_gap()],
        ),
    ]
    payload = extract_prior_turn_delivery_status(entries)
    assert payload is not None
    assert payload["execution_id"] == "new"
    assert prior_turn_has_blocking_delivery_gaps(entries) is True


def test_gaps_vs_redispatch_mutex():
    gaps = "<prior_delivery_gaps>\nx\n</prior_delivery_gaps>"
    retry = "<prior_delegate_retry>\ny\n</prior_delegate_retry>"
    g, r = apply_gaps_vs_redispatch_mutex(gaps, retry)
    assert g == gaps
    assert r == ""
    g2, r2 = apply_gaps_vs_redispatch_mutex("", retry)
    assert g2 == ""
    assert r2 == retry
    g3, r3 = apply_gaps_vs_redispatch_mutex("   ", retry)
    assert r3 == retry


@pytest.mark.asyncio
async def test_build_hint_injects_once_only_on_fingerprint(monkeypatch):
    from agentcore.runtime.delegate import prior_delivery_gaps as mod

    async def _empty(**_kwargs):
        return []

    async def _hit(**_kwargs):
        return [
            _delivery(
                state="partial",
                execution_id="e1",
                delivered_files=["a.md"],
                gaps=[_blocking_gap()],
            )
        ]

    async def _warning_only(**_kwargs):
        return [_delivery(state="partial", gaps=[_warning_gap()])]

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _empty)
    assert await mod.build_prior_delivery_gaps_hint(conversation_id="c1") == ""

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _warning_only)
    assert await mod.build_prior_delivery_gaps_hint(conversation_id="c1") == ""

    monkeypatch.setattr(mod, "_load_latest_prior_journal", _hit)
    text = await mod.build_prior_delivery_gaps_hint(
        conversation_id="c1",
        exclude_message_id="msg-current",
    )
    assert "<prior_delivery_gaps>" in text
    assert text.count("<prior_delivery_gaps>") == 1
    assert "execution_id=" not in text
    assert "e1" not in text
