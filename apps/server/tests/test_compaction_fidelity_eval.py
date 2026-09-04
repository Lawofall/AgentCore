"""compaction fidelity eval 自测（per-PR 零 LLM 硬门禁）。"""

from __future__ import annotations

import asyncio

import pytest

from agentcore.evals.compaction_fidelity import (
    SAMPLES,
    _ideal_summary,
    check_prompt_contract,
    check_summary,
    compaction_fidelity_to_dict,
    format_compaction_fidelity_report,
    lint_samples,
    run_compaction_fidelity,
    select_samples,
)
from agentcore.evals.types import EvalConfigError
from agentcore.llm.provider.protocol import LLMResponse


def test_samples_lint_ok() -> None:
    lint_samples(SAMPLES)
    assert len(SAMPLES) >= 8
    assert {s.lane for s in SAMPLES} == {"conversation", "worker"}


def test_prompt_contract_ok() -> None:
    assert check_prompt_contract() == []


def test_check_keeps_identifiers() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_identifiers")
    ok = check_summary(_ideal_summary(sample), sample)
    assert ok.ok


def test_check_rejects_missing_identifier() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_identifiers")
    result = check_summary(
        "## 已确立的事实 / 背景\n忘了路径\n## 涉及的文件与标识符\n- x",
        sample,
    )
    assert not result.ok
    assert any(f.startswith("missing:apps/billing/omega_ledger.py") for f in result.failures)


def test_check_rejects_closed_item_still_open() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_closed_not_open")
    result = check_summary(
        "## 未决问题 / 待办\n- hx9f2a-ticket\n- LOGIN_COPY_FROZEN 还要改\n"
        "## 涉及的文件与标识符\n- hx9f2a-ticket",
        sample,
    )
    assert not result.ok
    assert "stale_in:open:LOGIN_COPY_FROZEN" in result.failures


def test_check_open_section_omitted_is_ok_for_absent() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_closed_not_open")
    # 未决整段省略 → 已关闭项没有被当成还要做；但本条还要求工单出现在未决。
    result = check_summary(
        "## 已确立的事实 / 背景\nhx9f2a-ticket 仍开着\n## 涉及的文件与标识符\n- hx9f2a-ticket",
        sample,
    )
    assert not result.ok
    assert any(f.startswith("missing_section:open:") for f in result.failures)


def test_check_veto_must_not_reopen() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_veto_stays")
    result = check_summary(
        "## 关键决策与理由\n- 否决 Omega-7\n"
        "## 未决问题 / 待办\n- 还要在 neon_cache_v4 和别的方案里选\n"
        "## 涉及的文件与标识符\n- apps/billing/omega_ledger.py",
        sample,
    )
    assert not result.ok
    assert "stale_in:open:neon_cache_v4" in result.failures


def test_ledger_token_required_in_files() -> None:
    sample = next(s for s in SAMPLES if s.id == "chat_file_ledger")
    result = check_summary(
        "## 已确立的事实 / 背景\nsettleOmegaBatch\n## 涉及的文件与标识符\n- apps/billing/omega_ledger.py",
        sample,
    )
    assert not result.ok
    assert any("src/payments/omega_settle.ts" in f for f in result.failures)


def test_empty_summary_fails() -> None:
    sample = SAMPLES[0]
    result = check_summary("  ", sample)
    assert result.failures == ("empty",)


def test_select_samples_keys() -> None:
    picked = select_samples(SAMPLES, "chat_identifiers,worker_paths")
    assert [s.id for s in picked] == ["chat_identifiers", "worker_paths"]


def test_select_samples_unknown() -> None:
    with pytest.raises(EvalConfigError, match="未知"):
        select_samples(SAMPLES, "no_such_probe")


def test_lint_rejects_too_few() -> None:
    with pytest.raises(EvalConfigError, match="不足 8"):
        lint_samples(SAMPLES[:3])


def test_run_with_scripted_provider() -> None:
    class _BySample:
        async def complete(self, request):  # noqa: ANN001
            assert request.tools is None
            assert request.thinking is False
            assert request.scenario == "eval.compaction_fidelity"
            user = next(m.content for m in request.messages if m.role == "user")
            sample = next(s for s in SAMPLES if s.fold_payload() == user)
            return LLMResponse(content=_ideal_summary(sample))

    result = asyncio.run(run_compaction_fidelity(_BySample(), "fake-model", SAMPLES))
    assert result.n == len(SAMPLES)
    assert result.n_ok == len(SAMPLES)
    assert result.compliance_rate == 1.0
    report = format_compaction_fidelity_report(result)
    assert "compaction_fidelity" in report
    data = compaction_fidelity_to_dict(result)
    assert data["n_ok"] == len(SAMPLES)


def test_run_detects_lossy_scripted_summary() -> None:
    class _Drop:
        async def complete(self, request):  # noqa: ANN001
            return LLMResponse(content="## 已确立的事实 / 背景\n什么都忘了")

    result = asyncio.run(run_compaction_fidelity(_Drop(), "fake-model", SAMPLES[:1]))
    assert result.n_ok == 0
    assert result.failures
