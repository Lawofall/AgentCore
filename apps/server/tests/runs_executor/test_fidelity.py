from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.constants import DEP_CONTEXT_BUDGET
from agentcore.runtime.runs.fidelity import allocate, pointer_body, truncate_head_tail
from agentcore.runtime.runs.wave import WaveScheduler
from tests.runs_executor.conftest import _ContentProvider, _executor


def test_allocate_single_dep_gets_whole_budget():
    assert allocate([10_000], 16_000) == [10_000]  # fits → full content
    assert allocate([50_000], 16_000) == [16_000]  # over → capped at the budget


def test_allocate_water_fills_unequal_deps():
    # The small dep takes only what it needs; the freed remainder goes to the big one
    # (not an even split that would starve the big dep and waste the small's share).
    out = allocate([1_000, 50_000], 16_000)
    assert out == [1_000, 15_000]
    assert sum(out) == 16_000


def test_allocate_splits_equal_large_deps_evenly():
    assert allocate([50_000, 50_000], 16_000) == [8_000, 8_000]


def test_allocate_empty_is_empty():
    assert allocate([], 16_000) == []


def test_pointer_body_tells_downstream_to_read_listed_paths():
    body = pointer_body("短交接", ["工作稿/a.md", "工作稿/b.md"])
    assert "工作稿/a.md" in body
    assert "先 file_read" in body
    assert "磁盘真实路径" in body
    assert "子目录" in body
    assert "全仓" in body
    assert "不要凭空臆测" in body


def test_truncate_head_tail_keeps_both_ends():
    content = "HEAD起始" + ("x" * 5_000) + "TAIL尾注金额￥999"
    out = truncate_head_tail(content, 1_000)
    assert out.startswith("HEAD起始")  # head kept
    assert "TAIL尾注金额￥999" in out  # tail kept — the fidelity fix (was dropped before)
    assert "系统视图截断" in out  # transport elision — not delivery-omission wording
    assert "中间省略" not in out
    assert len(out) <= 1_000  # never exceeds the allowance


def test_truncate_head_tail_short_content_unchanged():
    assert truncate_head_tail("short", 1_000) == "short"


async def test_long_upstream_injected_with_head_and_tail_preserved():
    # The fix end-to-end: a long upstream product (over budget) reaches the
    # downstream writer with BOTH ends — the old 4000 head-only cap silently dropped
    # the tail (where 金额 / 法条编号 often live).
    long_upstream = "起始结论" + ("数" * (DEP_CONTEXT_BUDGET + 5_000)) + "关键尾注:法条第42条"
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    provider = _ContentProvider([long_upstream, "FINAL"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    downstream_user = provider.user_messages[1]
    assert "起始结论" in downstream_user  # head preserved
    assert "关键尾注:法条第42条" in downstream_user  # tail preserved (the fix)
    assert "系统视图截断" in downstream_user  # trimmed via transport marker, not shipped whole
    assert "中间省略" not in downstream_user


async def test_summarize_dep_is_compressed_not_passed_through():
    # A dep that declared result_handling="summarize" is digested, not budget-passed:
    # the full content must NOT reach the downstream prompt.
    long_upstream = "S摘要起点" + ("数" * 3_000)
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研", "result_handling": "summarize"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    provider = _ContentProvider([long_upstream, "FINAL"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    downstream_user = provider.user_messages[1]
    assert "摘要起点" in downstream_user  # the head digest is present
    assert long_upstream not in downstream_user  # but not the full 3000-char product


async def test_wide_fanin_shares_budget_bounded_total():
    # Three long upstreams fanning into one writer share the budget (≈ budget/3 each,
    # water-filled), so the total injected upstream context stays bounded instead of
    # multiplying to 3× a per-dep cap.
    big = "甲" * 40_000
    tasks = [
        {"id": "r1", "role": "调研A", "task": "查A"},
        {"id": "r2", "role": "调研B", "task": "查B"},
        {"id": "r3", "role": "调研C", "task": "查C"},
        {"id": "w", "role": "写手", "task": "汇总", "depends_on": ["r1", "r2", "r3"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    provider = _ContentProvider([big, big, big, "FINAL"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    writer_user = provider.user_messages[3]
    # The three "## 前置结果" blocks together stay within the shared budget (+ markers /
    # labels slack), nowhere near 3 × 40_000. Count the block HEADER ("## 前置结果"), not
    # the bare phrase — the team-position block (D) also names 「前置结果」 when telling a
    # terminal node where its upstream products are.
    assert writer_user.count("## 前置结果") == 3
    assert len(writer_user) < DEP_CONTEXT_BUDGET + 2_000
