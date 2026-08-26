"""Error-code catalog + classifier contract (统一错误码共享目录).

Guards the single ``ErrorCode`` directory: every ``AgentCoreError`` uses a
catalogued code, the wire value equals the member name (so the frontend mirror
``contract-types/errorCodes.ts`` and the logs match on the same string), and
``error_fields_for`` preserves a coded error's code/message while only collapsing
an unrecognized crash to the fallback (避免 pipeline 把多种错误压成 PIPELINE_ERROR).
"""

import inspect
import re
from pathlib import Path

from agentcore.core import errors as errors_module
from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import (
    MAX_RETRY_AFTER,
    UNCLASSIFIED_EXCEPTION_USER_MESSAGE,
    AgentCoreError,
    LLMAuthError,
    LLMInsufficientBalanceError,
    error_fields_for,
)


def _all_error_classes() -> list[type[AgentCoreError]]:
    return [
        obj
        for _, obj in inspect.getmembers(errors_module, inspect.isclass)
        if issubclass(obj, AgentCoreError)
    ]


def test_every_error_class_code_is_catalogued():
    catalog = set(ErrorCode)
    for cls in _all_error_classes():
        assert cls.code in catalog, (
            f"{cls.__name__}.code={cls.code!r} is not in the ErrorCode catalog — "
            "add it to core/error_codes.py then run `pnpm gen:types`."
        )


def test_error_code_value_equals_name():
    # The wire value is the member name verbatim (UPPER_SNAKE); the frontend mirror
    # and the structured logs key off this exact string.
    for member in ErrorCode:
        assert member.value == member.name


def test_error_fields_for_preserves_agentcore_code_and_message():
    code, message, err_ctx = error_fields_for(
        LLMAuthError(),
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message="fallback should be ignored",
    )
    assert code == ErrorCode.LLM_KEY_INVALID
    assert "无效" in message  # the curated zh message, not the fallback


def test_error_fields_for_fills_empty_coded_message_from_fallback():
    code, message, err_ctx = error_fields_for(
        AgentCoreError(""),  # coded (base INTERNAL_ERROR) but no message
        fallback_code=ErrorCode.STREAM_ERROR,
        fallback_message="服务出错了",
    )
    assert code == ErrorCode.INTERNAL_ERROR
    assert message == "服务出错了"


def test_error_fields_for_collapses_unknown_exception_to_product_fallback():
    code, message, err_ctx = error_fields_for(
        ValueError("raw technical boom"),
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message="管线执行失败，请稍后重试。",
    )
    assert code == ErrorCode.PIPELINE_ERROR
    assert message == "管线执行失败，请稍后重试。"
    assert "raw technical" not in message


def test_unclassified_fallback_is_pipeline_copy_not_llm():
    """Pre-LLM crashes (e.g. tool ctor) share this fallback; must not claim 模型调用."""
    from agentcore.core.message_merge import DEFAULT_FAILED_ERROR_MESSAGE

    assert DEFAULT_FAILED_ERROR_MESSAGE == UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    assert "模型" not in UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    code, message, _ = error_fields_for(
        TypeError(
            "TerminalTool.__init__() got an unexpected keyword argument 'languages'"
        ),
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message=UNCLASSIFIED_EXCEPTION_USER_MESSAGE,
    )
    assert code == ErrorCode.PIPELINE_ERROR
    assert message == UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    assert "TerminalTool" not in message


def test_error_fields_for_empty_fallback_degrades_to_product_default():
    """A caller with no curated copy still owes the user a sentence, not silence."""
    boom = ValueError("PIPELINE_ERROR: build_turn_router requires explicit credentials")
    code, message, err_ctx = error_fields_for(
        boom,
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message="",
    )
    assert code == ErrorCode.PIPELINE_ERROR
    assert message == UNCLASSIFIED_EXCEPTION_USER_MESSAGE
    assert "build_turn_router" not in message


def test_error_fields_for_preserves_agentcore_over_unclassified_fallback():
    """Curated AgentCoreError copy must not be overwritten by the unclassified default."""
    code, message, err_ctx = error_fields_for(
        LLMAuthError(),
        fallback_code=ErrorCode.PIPELINE_ERROR,
        fallback_message=UNCLASSIFIED_EXCEPTION_USER_MESSAGE,
    )
    assert code == ErrorCode.LLM_KEY_INVALID
    assert "无效" in message
    assert message != UNCLASSIFIED_EXCEPTION_USER_MESSAGE


def test_insufficient_balance_backend_flag_matches_frontend_policy():
    # The desktop now marks LLM_INSUFFICIENT_BALANCE non-retriable via the shared
    # catalog; assert the backend's own retryable flag agrees so the two can't drift.
    assert LLMInsufficientBalanceError().retryable is False
    assert LLMAuthError().retryable is False


def _contract_types_max_retry_after() -> float:
    root = Path(__file__).resolve().parents[3]
    text = (root / "packages/contract-types/src/rateLimit.ts").read_text(
        encoding="utf-8",
    )
    match = re.search(r"export const MAX_RETRY_AFTER = (\d+(?:\.\d+)?)", text)
    assert match, "MAX_RETRY_AFTER not found in contract-types rateLimit.ts"
    return float(match.group(1))


def test_max_retry_after_matches_contract_types():
    assert _contract_types_max_retry_after() == MAX_RETRY_AFTER
