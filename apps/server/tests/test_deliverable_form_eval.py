"""deliverable.form eval 自测（per-PR 零 LLM 硬门禁）。"""

import asyncio

import pytest

from agentcore.evals.deliverable_form import (
    SAMPLES,
    check_form_call,
    check_prompt_contract,
    deliverable_form_to_dict,
    format_deliverable_form_report,
    lint_samples,
    run_deliverable_form,
)
from agentcore.evals.types import EvalConfigError
from agentcore.llm.provider.protocol import LLMResponse


class _FixedForm:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, request):  # noqa: ANN001
        self.calls += 1
        assert request.tools is None
        return LLMResponse(content=self.content)


def test_samples_lint_ok():
    lint_samples(SAMPLES)
    assert len(SAMPLES) >= 8
    assert {s.expected_form for s in SAMPLES} == {"prose", "files", "workspace"}


def test_prompt_contract_ok():
    assert check_prompt_contract() == []


def test_check_ok_prose():
    r = check_form_call(
        '{"form":"prose","task":"用正文向用户打招呼，不要落盘"}',
        expected_form="prose",
        expect_landing_hint=False,
    )
    assert r.ok
    assert r.form == "prose"


def test_check_ok_workspace():
    r = check_form_call(
        '{"form":"workspace","task":"用 file_write 改 src/app.py"}',
        expected_form="workspace",
    )
    assert r.ok
    assert r.form == "workspace"


def test_check_ok_files():
    r = check_form_call(
        '{"form":"files","task":"用 file_write 写 index.html 到工作区"}',
        expected_form="files",
    )
    assert r.ok
    assert r.form == "files"


def test_check_rejects_form_mismatch():
    r = check_form_call(
        '{"form":"files","task":"用 file_write 写 hello.md"}',
        expected_form="prose",
        expect_landing_hint=False,
    )
    assert not r.ok
    assert any(f.startswith("form_mismatch") for f in r.failures)


def test_check_rejects_files_without_landing():
    r = check_form_call(
        '{"form":"files","task":"做一个漂亮的网页"}',
        expected_form="files",
    )
    assert not r.ok
    assert "files_task_missing_landing_hint" in r.failures


def test_check_rejects_prose_with_landing():
    r = check_form_call(
        '{"form":"prose","task":"用 file_write 写问候.md"}',
        expected_form="prose",
        expect_landing_hint=False,
    )
    assert not r.ok
    assert "prose_task_has_landing_hint" in r.failures


def test_run_with_scripted_provider():
    async def _go() -> None:
        # Alternate contents matching sample order is fragile; use one that always
        # matches by building a provider that keys off the user message.
        class _ByPrompt:
            async def complete(self, request):  # noqa: ANN001
                user = next(m.content for m in request.messages if m.role == "user")
                sample = next(s for s in SAMPLES if s.user_prompt == user)
                if sample.expected_form == "prose":
                    body = '{"form":"prose","task":"正文交付，勿落盘"}'
                elif sample.expected_form == "workspace":
                    body = '{"form":"workspace","task":"用 file_write 写入工作区"}'
                else:
                    body = '{"form":"files","task":"用 file_write 写入工作区"}'
                return LLMResponse(content=body)

        result = await run_deliverable_form(_ByPrompt(), "fake-model", SAMPLES)
        assert result.n == len(SAMPLES)
        assert result.n_ok == len(SAMPLES)
        assert result.compliance_rate == 1.0
        report = format_deliverable_form_report(result)
        assert "deliverable_form" in report
        data = deliverable_form_to_dict(result)
        assert data["n_ok"] == len(SAMPLES)

    asyncio.run(_go())


def test_lint_rejects_too_few():
    with pytest.raises(EvalConfigError, match="不足 8"):
        lint_samples(SAMPLES[:3])
