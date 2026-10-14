"""Incremental CEO pipeline progress (coordination inject A)."""

from agentcore.runtime.graph.session.pipeline_view import format_pipeline_progress
from agentcore.runtime.graph.session.session import CoordinationSession
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import Deliverable, RunSpec


def _plan(*nodes: RunSpec) -> RunPlan:
    return RunPlan(nodes=list(nodes))


def test_pipeline_progress_names_new_completions_once_not_full_roster():
    """Completed roster is not re-listed by name on every inject — only the delta."""
    live = _plan(
        RunSpec(
            run_id="a",
            role="文案",
            task="写 copy",
            deliverable=Deliverable(artifacts=["copy.md"]),
        ),
        RunSpec(
            run_id="b",
            role="前端",
            task="写页",
            deliverable=Deliverable(artifacts=["index.html"]),
            depends_on=["a"],
        ),
        RunSpec(run_id="c", role="QA", task="质检", depends_on=["b"]),
    )
    session = CoordinationSession(execution_id="e-prog", total_workers=3)
    session.live_plan = live
    session.mark_worker_completed("a")
    session._running_workers["b"] = "前端"
    session.mark_worker_busy("b", "llm")

    first = format_pipeline_progress(session)
    assert "本轮新完成：文案" in first
    assert "文案=新完成" in first or "已完成" in first
    assert "前端=在跑" in first or "在跑：前端" in first
    assert "QA" in first  # still named as blocked/pending

    # Second inject with no new completions — must NOT re-name 文案 as new,
    # and must not grow a completed-name roster.
    second = format_pipeline_progress(session)
    assert "本轮新完成" not in second
    assert "文案=完成" not in second
    assert "文案=新完成" not in second
    assert "已完成×1" in second or "已完成 1/3" in second
    assert "前端" in second

    session.mark_worker_completed("b")
    session._running_workers.pop("b", None)
    session.clear_worker_busy("b")
    session._running_workers["c"] = "QA"
    third = format_pipeline_progress(session)
    assert "本轮新完成：前端" in third
    assert "文案" not in third.split("本轮新完成")[1].split("\n")[0]
    # Historical 文案 still not expanded as a named completed roster entry.
    assert "文案=完成" not in third


def test_pipeline_progress_keeps_failed_and_blocked_signals():
    live = _plan(
        RunSpec(run_id="a", role="文案", task="写"),
        RunSpec(run_id="b", role="前端", task="写", depends_on=["a"]),
        RunSpec(run_id="c", role="QA", task="检", depends_on=["b"]),
    )
    session = CoordinationSession(execution_id="e-fail", total_workers=3)
    session.live_plan = live
    session.mark_worker_completed("a")
    session.failed_run_ids.add("a")
    session._running_workers["b"] = "前端"
    text = format_pipeline_progress(session)
    assert "失败" in text
    assert "文案" in text
    assert "依赖阻塞" in text or "QA" in text
