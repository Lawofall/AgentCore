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
    assert "先 read" in body
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


async def test_long_upstream_essay_is_not_forwarded():
    # No brief and no file: the essay, including its tail, does not ride the edge.
    long_upstream = "起始结论" + ("数" * (DEP_CONTEXT_BUDGET + 5_000)) + "关键尾注:法条第42条"
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    provider = _ContentProvider([long_upstream, "FINAL"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    downstream_user = provider.user_messages[1]
    assert "起始结论" not in downstream_user
    assert "关键尾注:法条第42条" not in downstream_user
    assert "没有简报" in downstream_user
    assert "勿编造" in downstream_user


async def test_summarize_without_brief_does_not_forward_the_essay():
    long_upstream = "S摘要起点" + ("数" * 3_000)
    tasks = [
        {"id": "s1", "role": "研究员", "task": "调研", "result_handling": "summarize"},
        {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    provider = _ContentProvider([long_upstream, "FINAL"])
    await WaveScheduler().run(plan, _executor(plan, provider, EventSink()))
    downstream_user = provider.user_messages[1]
    assert long_upstream not in downstream_user
    assert "摘要起点" not in downstream_user
    assert "没有简报" in downstream_user


async def test_wide_fanin_without_briefs_does_not_multiply_prose():
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
    assert writer_user.count("前置缺席") == 3
    assert "甲甲甲" not in writer_user
    assert len(writer_user) < DEP_CONTEXT_BUDGET + 2_000
