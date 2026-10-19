"""Empty-response diagnosis helpers."""

import json

from agentcore.llm.errors import (
    EmptyResponseDiagnosis,
    diagnose_empty_response,
    empty_response_event_message,
)


def test_diagnose_content_filtered():
    body = json.dumps(
        {
            "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
        }
    )
    assert (
        diagnose_empty_response(raw_body=body, finish_reason="content_filter")
        is EmptyResponseDiagnosis.CONTENT_FILTERED
    )


def test_diagnose_upstream_non_api_html():
    body = "<html><body>Please sign in to continue</body></html>"
    assert (
        diagnose_empty_response(raw_body=body)
        is EmptyResponseDiagnosis.UPSTREAM_NON_API
    )


def test_upstream_non_api_label_is_generic():
    """HTML/login shells are any-gateway; copy names the page, not a vendor."""
    label = empty_response_event_message(EmptyResponseDiagnosis.UPSTREAM_NON_API)
    assert "网页" in label or "登录" in label
    assert label.startswith("模型空响应")


def test_diagnose_model_unknown():
    body = json.dumps({"error": {"message": "model not found: foo"}})
    assert (
        diagnose_empty_response(raw_body=body)
        is EmptyResponseDiagnosis.MODEL_UNKNOWN
    )


def test_diagnose_silent_empty():
    body = json.dumps(
        {
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        }
    )
    assert (
        diagnose_empty_response(raw_body=body, finish_reason="stop")
        is EmptyResponseDiagnosis.SILENT_EMPTY
    )


def test_diagnose_length_empty():
    """finish_reason=length + empty body → LENGTH_EMPTY (protocol field only)."""
    body = json.dumps(
        {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        }
    )
    assert (
        diagnose_empty_response(raw_body=body, finish_reason="length")
        is EmptyResponseDiagnosis.LENGTH_EMPTY
    )


def test_diagnose_format_mismatch():
    assert (
        diagnose_empty_response(raw_body="not-json {{{", format_mismatch=True)
        is EmptyResponseDiagnosis.FORMAT_MISMATCH
    )


def test_diagnose_streaming_empty_tail_is_silent_not_format_mismatch():
    """A clean tool_calls/stop finish with empty deltas is silent-empty, not a
    format error — the SSE tail (many ``data:`` lines) isn't valid JSON but that
    alone must not be misread as 上游响应格式异常 (only ``format_mismatch=True`` is)."""
    sse_tail = (
        'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": null}]}\n\n'
        'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], '
        '"usage": {"prompt_tokens": 12161, "completion_tokens": 600}}\n\n'
        "data: [DONE]"
    )
    assert (
        diagnose_empty_response(raw_body=sse_tail, finish_reason="tool_calls")
        is EmptyResponseDiagnosis.SILENT_EMPTY
    )


def test_empty_response_error_context_html_and_base_url():
    from agentcore.llm.errors import empty_response_body_kind, empty_response_error_context

    assert empty_response_body_kind('<div id="root"></div>') == "html"
    ctx = empty_response_error_context(
        diagnosis=EmptyResponseDiagnosis.UPSTREAM_NON_API,
        raw_preview="<html><body>x</body></html>",
        base_url="https://api.zdc.mom/",
    )
    assert ctx is not None
    assert ctx["empty_diagnosis"] == "upstream_non_api"
    assert ctx["body_kind"] == "html"
    assert ctx["base_url"] == "https://api.zdc.mom"


def test_empty_response_event_message_appends_diagnosis():
    msg = empty_response_event_message(EmptyResponseDiagnosis.SILENT_EMPTY)
    assert msg.startswith("模型空响应")
    assert "模型返回空内容" in msg


def test_empty_response_event_message_does_not_say_multiple():
    """One completed empty round — copy must not say「多次空响应」."""
    for diagnosis in (
        EmptyResponseDiagnosis.SILENT_EMPTY,
        EmptyResponseDiagnosis.LENGTH_EMPTY,
        EmptyResponseDiagnosis.UPSTREAM_NON_API,
    ):
        msg = empty_response_event_message(diagnosis)
        assert "多次空响应" not in msg
    assert "截断" in empty_response_event_message(EmptyResponseDiagnosis.LENGTH_EMPTY)
