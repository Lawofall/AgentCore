"""Boundary excerpt: prefer handoff debrief over truncated body markdown."""

from agentcore.runtime.graph.drive.boundary import review_summary_text
from agentcore.runtime.runs.constants import HANDOFF_SUMMARY_CHARS
from agentcore.runtime.runs.types import RunState


def test_review_summary_prefers_debrief_over_markdown_body():
    body = (
        "## 交付说明\n基于调研拟提纲。\n\n## 提纲章节结构\n"
        "| 章节 | 内容 | 要点 |\n|---|---|---|\n"
    )
    state = RunState(
        content=body + "| 一 | … | … |\n" * 20,
        debrief={
            "summary": "已拟 8 章提纲，覆盖返还财产与重婚两条路径。",
            "key_points": [
                "一、事实梳理",
                "二、法律路径总览",
                "三、返还财产要件",
                "四、证据清单",
            ],
        },
        files_touched=["outline-editor.md"],
    )
    text = review_summary_text(state)
    assert "已拟 8 章提纲" in text
    assert "· 一、事实梳理" in text
    assert "· 四、证据清单" in text
    assert "| 章节 |" not in text
    assert "交付说明" not in text


def test_review_summary_degraded_with_files_skips_markdown_head():
    state = RunState(
        content="## 交付说明\n| 章节 | 内容 | 要点 |\n",
        debrief={
            "summary": "## 交付说明\n| 章节 | 内容 | 要点 |；已落盘：outline-editor.md",
            "key_points": ["文件：outline-editor.md"],
            "degraded": True,
        },
        files_touched=["outline-editor.md"],
    )
    text = review_summary_text(state)
    assert text == "· 文件：outline-editor.md"
    assert "交付说明" not in text


def test_review_summary_falls_back_to_files_then_content():
    assert review_summary_text(None) == ""
    files_only = RunState(files_touched=["outline-editor.md", "notes.md"])
    assert review_summary_text(files_only) == "已落盘 outline-editor.md, notes.md"

    content_only = RunState(content="纯正文交接，无 handoff")
    assert review_summary_text(content_only) == "纯正文交接，无 handoff"


def test_review_summary_truncates_to_cap():
    long_points = [f"第{i}章：很长的要点说明用于触发截断" for i in range(1, 30)]
    state = RunState(
        debrief={"summary": "超长提纲", "key_points": long_points},
    )
    text = review_summary_text(state)
    assert text.endswith("…")
    assert len(text) == HANDOFF_SUMMARY_CHARS + 1  # body + ellipsis
