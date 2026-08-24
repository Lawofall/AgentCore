from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.runs.cutoff import (
    DEGRADED_HANDOFF_WARNING,
    REASON_DEGRADED_HANDOFF,
)
from agentcore.runtime.runs.executor.shared import (
    _hard_gap_blocks_completion,
    _is_hard_failure,
    _priced_failure,
)
from agentcore.runtime.runs.types import Deliverable, RunPhase
from agentcore.tools.file_products import file_product, with_file_products_marker


def test_priced_failure_stamps_landed_files_from_transcript():
    """队员写成功后异常失败：FAILED RunState 带着 transcript 上已自报的落盘产物。"""
    paths = ["收入.csv", "支出.csv"]
    transcript = [
        LLMMessage(
            role="tool",
            tool_call_id="w1",
            content=with_file_products_marker("已写入", [file_product(p) for p in paths]),
        )
    ]
    state = _priced_failure(
        "上游限流，暂时无法继续本回合。",
        model="m",
        usage=TokenUsage(),
        rounds=2,
        duration_ms=10,
        transcript=transcript,
    )
    assert state.phase is RunPhase.FAILED
    assert state.files_touched == paths
    assert [a["path"] for a in state.file_acceptance] == paths
    assert all(a["status"] == "accepted" for a in state.file_acceptance)


def test_priced_failure_without_products_leaves_ledger_empty():
    """无自报落盘的异常失败不得发明产物。"""
    state = _priced_failure(
        "boom before messages",
        model=None,
        usage=TokenUsage(),
        rounds=0,
        duration_ms=1,
        transcript=[LLMMessage(role="assistant", content="半成品草稿")],
    )
    assert state.files_touched == []
    assert state.file_acceptance == []


def test_is_hard_failure_empty_is_not_hard():
    assert _is_hard_failure("   ", None) is False
    assert _is_hard_failure("", Deliverable(strict=False)) is False
    assert _is_hard_failure("", Deliverable(strict=True)) is True


def test_is_hard_failure_nonempty_depends_on_strict():
    assert _is_hard_failure("x", None) is False
    assert _is_hard_failure("x", Deliverable(strict=False)) is False
    assert _is_hard_failure("x", Deliverable(strict=True)) is True


def test_is_hard_failure_files_form_zero_disk_is_soft():
    """甲⁺：form=files ∧ files_touched==0 不再硬失败（有正文即可 soft-complete）。"""
    d = Deliverable(form="files", strict=False)
    assert _is_hard_failure("有正文但未落盘", d, files_touched=0) is False
    assert _is_hard_failure("有正文且已落盘", d, files_touched=1) is False


def test_hard_gap_blocks_completion_never_fails_empty_or_unlanded():
    """空交 / 未落盘不再把节点打成 FAILED。"""
    gaps = [{"description": DEGRADED_HANDOFF_WARNING, "reason": REASON_DEGRADED_HANDOFF}]
    assert (
        _hard_gap_blocks_completion(gaps, {"degraded": True}, Deliverable(strict=False))
        is None
    )
    assert _hard_gap_blocks_completion(gaps, {"degraded": True}, None) is None
    assert (
        _hard_gap_blocks_completion(
            gaps,
            {"summary": "薄", "degraded": True},
            Deliverable(strict=True, form="files"),
            files_touched=0,
        )
        is None
    )
    assert (
        _hard_gap_blocks_completion(
            [{"description": "声明的交付物路径未落盘：site/sections/s0.html"}],
            None,
            Deliverable(strict=True),
        )
        is None
    )


def test_hard_gap_blocks_completion_soft_warning_alone_ok():
    """Anti-slop soft warnings alone must not trip hard-gap fail."""
    gaps = [{"description": "anti-slop：渐变过多"}]
    assert (
        _hard_gap_blocks_completion(gaps, None, Deliverable(strict=True, form="files"))
        is None
    )
