"""方向① 变体注入的 eval 侧 plumbing 测试：注册表 / 解析 / 字段 / lint，零 LLM.

「哪个变体更好」需真模型回合（已延后主线），不在此测；这里只证 eval 能正确**选中并注入**
一个变体：注册表解析、``EvalCase.prompt_profile`` 解析、未知名被 lint 与 resolver 双拦。
"""

from __future__ import annotations

import pytest

from agentcore.evals.prompt_profiles import PROFILE_NAMES, resolve_prompt_profile
from agentcore.evals.runner import _parse_case
from agentcore.evals.seed_lint import lint_case
from agentcore.evals.types import EvalConfigError


def test_registry_has_baseline_as_identity() -> None:
    assert "baseline" in PROFILE_NAMES
    # baseline / None 都解析成 None = 恒等（让 harness 走显式恒等路径）。
    assert resolve_prompt_profile("baseline") is None
    assert resolve_prompt_profile(None) is None


def test_resolve_unknown_profile_raises() -> None:
    with pytest.raises(EvalConfigError):
        resolve_prompt_profile("does_not_exist")


def test_eval_case_parses_prompt_profile_field() -> None:
    case = _parse_case(
        {"id": "c", "category": "qa", "user_message": "u", "prompt_profile": "baseline"}
    )
    assert case.prompt_profile == "baseline"


def test_eval_case_default_prompt_profile_is_none() -> None:
    case = _parse_case({"id": "c", "category": "qa", "user_message": "u"})
    assert case.prompt_profile is None


def test_lint_accepts_known_profile() -> None:
    raw = {
        "id": "c",
        "category": "qa",
        "user_message": "u",
        "prompt_profile": "baseline",
        "rubric": "r",
    }
    assert lint_case(raw) == []


def test_lint_rejects_unknown_profile() -> None:
    raw = {
        "id": "c",
        "category": "qa",
        "user_message": "u",
        "prompt_profile": "nope",
        "rubric": "r",
    }
    errs = lint_case(raw)
    assert any("prompt_profile" in e for e in errs)


def test_lint_allows_absent_profile() -> None:
    raw = {"id": "c", "category": "qa", "user_message": "u", "rubric": "r"}
    assert lint_case(raw) == []
