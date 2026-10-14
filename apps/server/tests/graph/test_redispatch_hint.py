"""Cross-turn empty-delegate / unproductive → one-shot redispatch soft hint."""

from __future__ import annotations

from agentcore.runtime.graph.admit.redispatch_hint import (
    prior_turn_has_redispatch_fingerprint,
)
from agentcore.runtime.engine.tool_exec import TOOL_FAILED_MARKER, with_tool_failed_marker
from agentcore.runtime.events.types import FinishReason
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal.entries import KIND_TURN_END
from agentcore.tools.builtin.graph.schema import EMPTY_DELEGATE_MSG


def _tool_call(
    *,
    name: str,
    success: bool,
    result: str,
) -> dict:
    return {
        "kind": FactKind.TOOL_CALL.value,
        "payload": {
            "run_id": "ceo",
            "tool_call_id": "tc1",
            "name": name,
            "arguments": "{}",
            "result": result,
            "success": success,
        },
        "ts": None,
    }


def test_no_fingerprint_when_journal_empty_or_clean():
    assert prior_turn_has_redispatch_fingerprint(None) is False
    assert prior_turn_has_redispatch_fingerprint([]) is False
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                {
                    "kind": KIND_TURN_END,
                    "payload": {"finish_reason": FinishReason.END_TURN.value},
                }
            ]
        )
        is False
    )
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                _tool_call(
                    name="delegate",
                    success=True,
                    result="ok",
                ),
                {
                    "kind": KIND_TURN_END,
                    "payload": {"finish_reason": FinishReason.END_TURN.value},
                },
            ]
        )
        is False
    )


def test_fingerprint_on_unproductive_finish_reason():
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                {
                    "kind": KIND_TURN_END,
                    "payload": {"finish_reason": FinishReason.UNPRODUCTIVE.value},
                }
            ]
        )
        is True
    )


def test_fingerprint_on_empty_gate_failed_delegate():
    raw = EMPTY_DELEGATE_MSG
    assert (
        prior_turn_has_redispatch_fingerprint(
            [_tool_call(name="delegate", success=False, result=raw)]
        )
        is True
    )
    # Journal stores the model-facing trailer after failure.
    marked = with_tool_failed_marker(raw)
    assert TOOL_FAILED_MARKER in marked
    assert (
        prior_turn_has_redispatch_fingerprint(
            [_tool_call(name="delegate", success=False, result=marked)]
        )
        is True
    )


def test_other_delegate_contract_failures_do_not_trip_empty_gate():
    """其它契约打回不是 empty-tasks fingerprint。"""
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                _tool_call(
                    name="delegate",
                    success=False,
                    result="委派任务无效：role 不能为空",
                )
            ]
        )
        is False
    )
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                _tool_call(
                    name="web_search",
                    success=False,
                    result=EMPTY_DELEGATE_MSG,
                )
            ]
        )
        is False
    )


def test_hint_copy_withdrawn():
    from agentcore.runtime.graph.admit import redispatch_hint as mod

    assert not hasattr(mod, "render_redispatch_hint")
    assert not hasattr(mod, "build_prior_failure_redispatch_hint")


def test_fingerprint_ignores_user_message_content():
    """Detector takes journal entries only — caller must not pass user text."""
    # Even if a model somehow put「继续」into a successful tool result, no empty/unproductive.
    assert (
        prior_turn_has_redispatch_fingerprint(
            [
                _tool_call(name="delegate", success=True, result="用户说继续"),
                {
                    "kind": KIND_TURN_END,
                    "payload": {"finish_reason": FinishReason.END_TURN.value},
                },
            ]
        )
        is False
    )


def test_build_hint_withdrawn():
    from agentcore.runtime.graph.admit import redispatch_hint as mod

    assert not hasattr(mod, "build_prior_failure_redispatch_hint")
