"""Unit tests for conversation.failure_visible (read-only failure projection)."""

from types import SimpleNamespace

from agentcore.conversation.failure_visible import (
    export_visible_text,
    visible_failure_text,
)
from agentcore.core.error_codes import ErrorCode


def test_partial_content_wins_over_error():
    msg = SimpleNamespace(
        role="assistant",
        content="半成品正文",
        usage={"status": "failed", "error_message": "不该出现"},
    )
    assert export_visible_text(msg) == "半成品正文"
    assert visible_failure_text(msg) is None


def test_empty_failed_uses_usage_error_message():
    msg = SimpleNamespace(
        role="assistant",
        content="",
        usage={
            "status": "failed",
            "error_code": ErrorCode.LLM_TIMEOUT,
            "error_message": "连接超时，请稍后重试",
        },
    )
    assert export_visible_text(msg) == "连接超时，请稍后重试"


def test_empty_failed_falls_back_to_category_label():
    msg = SimpleNamespace(
        role="assistant",
        content="  ",
        usage={"status": "failed", "error_code": ErrorCode.LLM_KEY_INVALID},
    )
    assert "鉴权失败" in (export_visible_text(msg) or "")


def test_empty_pipeline_error_uses_pipeline_label_not_llm():
    msg = SimpleNamespace(
        role="assistant",
        content="",
        usage={"status": "failed", "error_code": ErrorCode.PIPELINE_ERROR},
    )
    text = export_visible_text(msg) or ""
    assert "管线" in text
    assert "模型" not in text


def test_empty_failed_without_code_does_not_claim_llm():
    msg = SimpleNamespace(
        role="assistant",
        content="",
        usage={"status": "failed", "finish_reason": "error"},
    )
    text = export_visible_text(msg) or ""
    assert "模型" not in text
    assert text


def test_empty_failed_from_journal_turn_end():
    msg = SimpleNamespace(
        role="assistant",
        content="",
        usage={"status": "failed", "finish_reason": "error"},
    )
    entries = [
        {
            "kind": "turn_end",
            "payload": {
                "finish_reason": "error",
                "error": {"code": "PIPELINE_ERROR", "message": "管线崩溃"},
            },
        }
    ]
    assert export_visible_text(msg, journal_entries=entries) == "管线崩溃"


def test_empty_non_failed_assistant_skipped():
    msg = SimpleNamespace(role="assistant", content="", usage={"status": "complete"})
    assert export_visible_text(msg) is None
