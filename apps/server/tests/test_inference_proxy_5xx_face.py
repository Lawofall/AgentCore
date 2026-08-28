"""Sidecar→cloud /inference/ 5xx: our cloud vs true upstream (LLM_* envelope)."""

from __future__ import annotations

import json

import pytest

from agentcore.core.errors import (
    LLMError,
    LLMUpstreamError,
    OurServiceUnavailableError,
)
from agentcore.db.errors import DATABASE_UNAVAILABLE_MESSAGE
from agentcore.llm.errors import (
    _BODY_PREVIEW_MAX,
    HALFWAY_VENDOR_REJECT_MESSAGE,
    SELECTED_MODEL_UNAVAILABLE_MESSAGE,
    is_llm_family_error_code,
    our_inference_service_5xx_error,
    overlay_progress_failure_message,
    parse_agentcore_error_envelope,
    upstream_error,
)
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider

_INFERENCE_BASE = "http://127.0.0.1:8000/v1/inference/v1"
_VENDOR_BASE = "http://example.com/v1"


def _inference_leaf() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(name="user", api_key="tok", base_url=_INFERENCE_BASE)


def _vendor_leaf() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(name="user", api_key="k", base_url=_VENDOR_BASE)


def _envelope(code: str, message: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode()


def test_parse_envelope_requires_catalogued_code():
    env = parse_agentcore_error_envelope(
        _envelope("DATABASE_UNAVAILABLE", DATABASE_UNAVAILABLE_MESSAGE)
    )
    assert env is not None
    assert env.code == "DATABASE_UNAVAILABLE"
    assert env.message == DATABASE_UNAVAILABLE_MESSAGE


def test_parse_envelope_rejects_vendor_shaped_code():
    body = b'{"error":{"code":"invalid_api_key","message":"bad key"}}'
    assert parse_agentcore_error_envelope(body) is None


def test_llm_family_prefix():
    assert is_llm_family_error_code("LLM_ERROR") is True
    assert is_llm_family_error_code("LLM_TIMEOUT") is True
    assert is_llm_family_error_code("DATABASE_UNAVAILABLE") is False
    assert is_llm_family_error_code("INFERENCE_TOKEN_EXPIRED") is False


def test_our_inference_db_unavailable_keeps_real_code():
    err = our_inference_service_5xx_error(
        status=503, body=_envelope("DATABASE_UNAVAILABLE", DATABASE_UNAVAILABLE_MESSAGE)
    )
    assert isinstance(err, OurServiceUnavailableError)
    assert err.code == "DATABASE_UNAVAILABLE"
    assert str(err) == DATABASE_UNAVAILABLE_MESSAGE
    assert "上游" not in str(err)


def test_our_service_error_stays_in_llm_family():
    """Retry budget + committed-partial-content handling key off ``LLMError``."""
    err = our_inference_service_5xx_error(
        status=503, body=_envelope("DATABASE_UNAVAILABLE", DATABASE_UNAVAILABLE_MESSAGE)
    )
    assert isinstance(err, LLMError)
    assert err.retryable is True


def test_our_service_config_faults_are_not_retryable():
    for code in ("KEY_STORAGE_UNAVAILABLE", "PLATFORM_BILLING_UNAVAILABLE"):
        err = our_inference_service_5xx_error(status=503, body=_envelope(code, "服务端配置缺失"))
        assert err is not None
        assert err.code == code
        assert err.retryable is False


def test_our_inference_llm_envelope_defers_to_upstream_path():
    body = _envelope("LLM_ERROR", "上游推理服务不可达")
    assert our_inference_service_5xx_error(status=502, body=body) is None


def test_inference_503_database_unavailable_honest_face():
    leaf = _inference_leaf()
    with pytest.raises(OurServiceUnavailableError) as ei:
        leaf._raise_for_status(
            503,
            1.0,
            {},
            body=_envelope("DATABASE_UNAVAILABLE", DATABASE_UNAVAILABLE_MESSAGE),
            attempt=0,
        )
    err = ei.value
    assert err.code == "DATABASE_UNAVAILABLE"
    assert str(err) == DATABASE_UNAVAILABLE_MESSAGE
    assert "上游模型服务" not in str(err)
    assert "Base URL" not in str(err)


def test_inference_503_without_envelope_stays_generic_internal():
    """A bare gateway page must not claim a fault (e.g. DB) we cannot prove."""
    leaf = _inference_leaf()
    with pytest.raises(OurServiceUnavailableError) as ei:
        leaf._raise_for_status(503, 1.0, {}, body=b"<html>gateway</html>", attempt=0)
    assert ei.value.code == "INTERNAL_ERROR"
    assert "上游模型服务" not in str(ei.value)


def test_inference_relays_real_upstream_status_not_proxy_502():
    """Vendor 503 wrapped by the proxy as 502: the bubble must say 503."""
    leaf = _inference_leaf()
    body = _envelope("LLM_ERROR", "上游模型服务暂时不可用（503），请稍后再试")
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(502, 1.0, {}, body=body, attempt=0)
    assert str(ei.value) == "上游模型服务暂时不可用（503），请稍后再试"


def test_vendor_direct_503_still_upstream_capacity_copy():
    leaf = _vendor_leaf()
    # Direct vendor hop: even a lookalike envelope must not become our-service face.
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(
            503,
            1.0,
            {},
            body=_envelope("DATABASE_UNAVAILABLE", DATABASE_UNAVAILABLE_MESSAGE),
            attempt=0,
        )
    assert str(ei.value) == "上游模型服务暂时不可用（503），请稍后再试"


def test_upstream_error_answers_502_while_details_keep_real_status():
    """Our relay status stays 502 — it is the discriminator for「our 5xx vs vendor's」."""
    err = upstream_error("上游模型服务暂时不可用（503），请稍后再试", status=503, body=None)
    assert err.status_code == 502
    assert err.details.get("upstream_status") == 503


def test_inference_500_internal_error_preserves_code():
    leaf = _inference_leaf()
    body = _envelope("INTERNAL_ERROR", "服务器内部错误，请稍后重试")
    with pytest.raises(OurServiceUnavailableError) as ei:
        leaf._raise_for_status(500, 1.0, {}, body=body, attempt=0)
    err = ei.value
    assert err.code == "INTERNAL_ERROR"
    assert "上游模型服务" not in str(err)
    assert err.retryable is True


def test_parse_envelope_survives_context_longer_than_log_preview():
    """A legal LLM_* envelope with vendor HTML in context must not become AgentCore."""
    preview = "H" * _BODY_PREVIEW_MAX
    payload = {
        "error": {
            "code": "LLM_ERROR",
            "message": "上游模型服务暂时不可用（530），请稍后再试",
            "context": {
                "upstream_status": 530,
                "upstream_body_preview": preview,
                "retry_attempts": 2,
            },
        }
    }
    body = json.dumps(payload, ensure_ascii=False).encode()
    assert len(body) > _BODY_PREVIEW_MAX
    env = parse_agentcore_error_envelope(body)
    assert env is not None
    assert env.code == "LLM_ERROR"
    assert env.context is not None
    assert env.context["upstream_status"] == 530

    leaf = _inference_leaf()
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(502, 1.0, {}, body=body, attempt=0)
    assert str(ei.value) == SELECTED_MODEL_UNAVAILABLE_MESSAGE
    assert "AgentCore" not in str(ei.value)


def test_bare_inference_530_is_selected_model_not_agentcore():
    leaf = _inference_leaf()
    assert (
        our_inference_service_5xx_error(status=530, body=b"<html>origin down</html>")
        is None
    )
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(
            530, 1.0, {}, body=b"<html>origin down</html>", attempt=0
        )
    assert str(ei.value) == SELECTED_MODEL_UNAVAILABLE_MESSAGE
    assert "AgentCore" not in str(ei.value)


def test_vendor_direct_530_is_selected_model():
    leaf = _vendor_leaf()
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(530, 1.0, {}, body=b"origin", attempt=0)
    assert str(ei.value) == SELECTED_MODEL_UNAVAILABLE_MESSAGE
    assert "AgentCore" not in str(ei.value)


def test_overlay_4xx_after_progress_is_halfway():
    assert (
        overlay_progress_failure_message(
            code="LLM_ERROR",
            message="dots3 请求格式被拒绝（400），请检查模型与参数配置",
            context={"upstream_status": 400},
        )
        == HALFWAY_VENDOR_REJECT_MESSAGE
    )


def test_overlay_530_after_progress_is_selected_model():
    assert (
        overlay_progress_failure_message(
            code="INTERNAL_ERROR",
            message="AgentCore 服务暂时不可用，请稍后重试",
            context={"upstream_status": 530},
        )
        == SELECTED_MODEL_UNAVAILABLE_MESSAGE
    )


def test_overlay_other_5xx_after_progress_keeps_upstream_copy():
    msg = "上游模型服务暂时不可用（503），请稍后再试"
    assert (
        overlay_progress_failure_message(
            code="LLM_ERROR",
            message=msg,
            context={"upstream_status": 503},
        )
        == msg
    )


def test_inference_relays_vendor_400_status_inside_proxy_502():
    """Proxy flattens vendor 400 onto 502; details must keep 400 so halfway overlay fires."""
    body = json.dumps(
        {
            "error": {
                "code": "LLM_ERROR",
                "message": "dots3 请求格式被拒绝（400），请检查模型与参数配置",
                "context": {"upstream_status": 400},
            }
        },
        ensure_ascii=False,
    ).encode()
    leaf = _inference_leaf()
    with pytest.raises(LLMUpstreamError) as ei:
        leaf._raise_for_status(502, 1.0, {}, body=body, attempt=0)
    assert ei.value.details.get("upstream_status") == 400
    assert "AgentCore" not in str(ei.value)
