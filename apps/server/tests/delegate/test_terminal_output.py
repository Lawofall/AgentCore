"""Structure-preserving cap for ALL_COMPLETED.output — keep roster, shrink bodies."""

from __future__ import annotations

from agentcore.runtime.delegate.terminal_output import (
    ALL_COMPLETED_OUTPUT_LIMIT,
    cap_all_completed_output,
    compose_all_completed_output,
)

ROSTER = (
    "### 队员终态名册（地面真相——写终稿必须对照，禁止编造「全部交付」）\n"
    "计划节点：完成 1 · 失败 1 · 跳过 0 · 取消 0；综述可见产物 2 条；"
    "路径核对：已核 0 · 未通过 0。\n"
    "失败节点：\n- 工程师（`w2`）失败：编译失败"
)
CLOSING = "---\n以上为团队产出。\n【终稿纪律】失败必须写入，禁止编造「全部交付」。"
INTRO = "## 团队执行结果（据此写一段简短概览交给用户；完整详情用户自行查看）"
FAILURES = (
    "### tool_failures\n"
    "各队员 run 内工具失败聚合（引擎地面真相，非模型自评）。\n"
    "#### 工程师 · run_id: `w2`\n- code_execute failures=2 succeeded_after=false"
)
SHORT_WORKER = "### 调研员（completed） · run_id: `w1`\n短结论：甲。"
LONG_WORKER = "### 撰稿（completed） · run_id: `w3`\n" + ("长正文。" * 800)
FAILED_WORKER = "### 工程师（failed） · run_id: `w2`\n（失败：编译失败）"


def test_under_limit_is_unchanged():
    prose = f"{INTRO}\n{SHORT_WORKER}"
    out = compose_all_completed_output(prose, ROSTER, CLOSING, limit=4000)
    assert INTRO in out
    assert "短结论：甲。" in out
    assert "计划节点：完成 1 · 失败 1" in out
    assert "【终稿纪律】" in out
    assert "系统视图截断" not in out


def test_long_worker_shrinks_before_short_worker_and_roster():
    prose = f"{INTRO}\n{FAILURES}\n{SHORT_WORKER}\n{LONG_WORKER}\n{FAILED_WORKER}"
    out = compose_all_completed_output(prose, ROSTER, CLOSING, limit=2000)
    assert len(out) <= 2000
    assert "计划节点：完成 1 · 失败 1" in out
    assert "失败节点：" in out
    assert "编译失败" in out
    assert "### tool_failures" in out
    assert "短结论：甲。" in out
    assert "### 撰稿（completed）" in out
    assert "系统视图截断" in out or "已省略" in out or "…" in out
    assert out.index("队员终态名册") < out.index("【终稿纪律】")
    # Long body is the one that paid; the short conclusion is still whole.
    assert out.count("长正文。") < 800


def test_failed_worker_section_survives_when_completed_bodies_are_huge():
    prose = f"{INTRO}\n{FAILED_WORKER}\n{LONG_WORKER}"
    out = compose_all_completed_output(prose, ROSTER, CLOSING, limit=1800)
    assert len(out) <= 1800
    assert "（失败：编译失败）" in out
    assert "失败节点：" in out
    assert "### 撰稿（completed）" in out


def test_cap_joined_blob_keeps_roster_instead_of_tail_chop():
    """Host backfill used to do ``text[:4000]`` — roster at the end vanished."""
    workers = "\n".join(
        f"### 写手{i}（completed） · run_id: `w{i}`\n" + ("正文。" * 800) for i in range(8)
    )
    # Old harvest join put roster after the worker dump — a head slice dropped it.
    blob = f"{INTRO}\n{workers}\n{ROSTER}\n{CLOSING}"
    assert len(blob) > ALL_COMPLETED_OUTPUT_LIMIT
    tail_cut = blob[:ALL_COMPLETED_OUTPUT_LIMIT]
    assert "计划节点：完成 1 · 失败 1" not in tail_cut
    capped = cap_all_completed_output(blob)
    assert len(capped) <= ALL_COMPLETED_OUTPUT_LIMIT
    assert "计划节点：完成 1 · 失败 1" in capped
    assert "失败节点：" in capped
    assert "【终稿纪律】" in capped
    assert "系统视图截断" in capped or "已省略" in capped or "…" in capped


def test_default_limit_is_the_pathological_valve():
    from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT

    assert ALL_COMPLETED_OUTPUT_LIMIT == DELEGATE_OUTPUT_LIMIT
    prose = f"{INTRO}\n{LONG_WORKER}\n{LONG_WORKER.replace('w3', 'w4').replace('撰稿', '校对')}"
    out = compose_all_completed_output(prose, ROSTER, CLOSING)
    assert len(out) <= ALL_COMPLETED_OUTPUT_LIMIT
    assert "系统视图截断" not in out
    assert "【终稿纪律】" in out
