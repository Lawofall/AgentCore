from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.executor.context import _dep_context_blocks
from agentcore.runtime.runs.types import RunPhase, RunPolicy, RunSpec, RunState
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.registry import ToolRegistry
from tests.runs_executor.conftest import (
    _ctx,
    _FileWriteTool,
    _plan,
    _ScriptedRounds,
    _state,
)


def test_dep_block_annotates_failed_upstream_absence():
    """FAILED deps must not inject their body; annotate absence + reason instead."""
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="写手", task="写报告"))
    failed = RunState(
        phase=RunPhase.FAILED,
        content="invoke tool file_write fake dsml",
        error="未把产物写入工作区",
    )
    blocks = _dep_context_blocks(plan, ["u"], {"u": failed})
    assert len(blocks) == 1
    block = blocks[0]
    assert block.fidelity == "absent"
    assert "前置缺席" in block.heading
    assert "写手" in block.body
    assert "未把产物写入工作区" in block.body
    assert "invoke tool file_write" not in block.body  # garbage body not shipped


def test_dep_block_annotates_cancelled_upstream_absence():
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="审查员", task="审"))
    cancelled = RunState(phase=RunPhase.CANCELLED, error="运行超时")
    blocks = _dep_context_blocks(plan, ["u"], {"u": cancelled})
    assert len(blocks) == 1
    assert blocks[0].fidelity == "absent"
    assert "已取消" in blocks[0].body
    assert "运行超时" in blocks[0].body


def test_dep_block_file_writer_becomes_pointer():
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="构建器", task="生成数据"))
    completed = {
        "u": _state("已生成数据集，详见文件。", files=["data/out.csv", "data/schema.json"])
    }
    blocks = _dep_context_blocks(plan, ["u"], completed)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.channel == "dependency"
    assert block.source_role == "构建器"
    assert block.source_run_id == "u"
    assert block.fidelity == "pointer"  # file-writer → pointer fidelity
    body = block.body
    assert "已生成数据集" in body  # the worker's prose handoff digest is kept
    assert "data/out.csv" in body and "data/schema.json" in body  # the pointer
    assert "file_read" in body  # told how to pull the full content
    assert block.files == ["data/out.csv", "data/schema.json"]  # artifact paths carried


def test_dep_pointer_digests_prose_instead_of_shipping_whole():
    # A file-writer with a huge prose body is DIGESTED (not budget-passed whole):
    # the artifact is on disk, the prompt only needs orientation + the path.
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="写手", task="写报告"))
    huge = "开头摘要" + ("文" * 5_000)
    blocks = _dep_context_blocks(plan, ["u"], {"u": _state(huge, files=["report.md"])})
    body = blocks[0].body
    assert "开头摘要" in body  # head digest present
    assert huge not in body  # but NOT the full 5000-char product
    assert "report.md" in body


def test_dep_pointer_caps_file_list_with_elision():
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="生成器", task="批量生成"))
    files = [f"f{i}.txt" for i in range(30)]
    body = _dep_context_blocks(plan, ["u"], {"u": _state("done", files=files)})[0].body
    assert "f0.txt" in body  # the first ones are listed
    assert "f25.txt" not in body  # beyond DEP_POINTER_MAX_FILES (20) is elided
    assert "共 30 个文件" in body  # and the full count is disclosed


def test_dep_block_prose_dep_unchanged_full_text():
    # No files → the existing full-text path: a short prose dep is passed through whole.
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="研究员", task="调研"))
    block = _dep_context_blocks(plan, ["u"], {"u": _state("纯文字结论无文件")})[0]
    assert block.body == "纯文字结论无文件"
    assert block.fidelity == "pass_through"  # no files → prose pass_through
    assert block.truncated is False  # short prose fits the budget whole


def test_dep_block_leads_with_author_debrief_summary():
    # 完工交接简报: a pass_through dep's block LEADS with the upstream author's own 结论 (from the
    # structured debrief, so the downstream sees the gist first / it survives any later trim); the
    # deliverable content is already clean and 建议下一步 is for the CEO, not downstream prose.
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="研究员", task="调研"))
    state = _state("详细调研正文……", debrief={"summary": "方案A明显更优", "next_steps": "做A的POC"})
    block = _dep_context_blocks(plan, ["u"], {"u": state})[0]
    assert block.body.startswith("【上游交接结论】方案A明显更优")
    assert "详细调研正文" in block.body  # the deliverable body still follows the lead
    assert "做A的POC" not in block.body  # next_steps is CEO-facing, not shipped downstream


def test_dep_block_promotes_brief_when_content_empty():
    """仅 debrief.summary（无正文、无落盘）→ 升格注入，不当前置缺席。"""
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="研究员", task="调研"))
    state = _state(
        "",
        debrief={"summary": "方案A更优", "key_points": ["成本更低", "风险可控"]},
    )
    blocks = _dep_context_blocks(plan, ["u"], {"u": state})
    assert len(blocks) == 1
    block = blocks[0]
    assert block.fidelity == "pass_through"
    assert "前置缺席" not in block.heading
    assert "方案A更优" in block.body
    assert "成本更低" in block.body
    assert "风险可控" in block.body


def test_dep_block_promoted_brief_does_not_prepend_author_summary():
    """正文为空 + 简报升格：body 已是 summary（+ key_points），不再 prepend 同一句。"""
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="研究员", task="调研"))
    state = _state(
        "",
        debrief={"summary": "方案A更优", "key_points": ["成本更低"]},
    )
    block = _dep_context_blocks(plan, ["u"], {"u": state})[0]
    assert block.body.startswith("方案A更优")
    assert block.body.count("方案A更优") == 1
    assert "【上游交接结论】" not in block.body
    assert "成本更低" in block.body


def test_dep_block_empty_completed_without_brief_still_absent():
    """COMPLETED 但无 content / files / debrief summary → 仍前置缺席。"""
    plan = _plan(RunSpec(run_id="u", agent_id="u", role="研究员", task="调研"))
    blocks = _dep_context_blocks(plan, ["u"], {"u": _state("")})
    assert len(blocks) == 1
    assert blocks[0].fidelity == "absent"
    assert "前置缺席" in blocks[0].heading


def test_dep_summarize_uses_author_summary_over_blind_head_chop(monkeypatch):
    # summarize fidelity: the author's own 结论 beats a mechanical head-chop of noisy prose.
    # Author digest is not a char-cap — do not emit context_capped.
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    spec = RunSpec(
        run_id="u",
        agent_id="u",
        role="研究员",
        task="调研",
        policy=RunPolicy(result_handling="summarize"),
    )
    content = "无关开头" + ("噪" * 2000)
    state = _state(content, debrief={"summary": "真正重要的一句结论"})
    block = _dep_context_blocks(_plan(spec), ["u"], {"u": state})[0]
    assert block.fidelity == "summarize"
    assert block.body == "真正重要的一句结论"  # author 结论, not 噪噪噪… head-chop
    assert block.truncated is True  # the full product is longer than the digest
    assert not any(name == "delegate.context_capped" for name, _ in spy.events)


def test_dep_summarize_mechanical_cap_logs(monkeypatch):
    from agentcore.runtime import context_cap
    from agentcore.runtime.runs.constants import DEP_SUMMARY_CHARS
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    spec = RunSpec(
        run_id="u",
        agent_id="u",
        role="研究员",
        task="调研",
        policy=RunPolicy(result_handling="summarize"),
    )
    content = "头" + ("噪" * (DEP_SUMMARY_CHARS + 200)) + "尾结论"
    block = _dep_context_blocks(_plan(spec), ["u"], {"u": _state(content)})[0]
    assert block.fidelity == "summarize"
    assert block.truncated is True
    fields = spy.get("delegate.context_capped")
    assert fields["site"] == "dep_context"
    assert fields["fidelity"] == "summarize"
    assert fields["original_chars"] == len(content)
    assert fields["final_chars"] == len(block.body)


async def test_dag_file_writing_upstream_passes_pointer_downstream():
    # End-to-end: the upstream WRITES a file; the downstream's opening prompt carries
    # a pointer (path + file_read hint), proving files_touched flows RunState→prompt.
    tasks = [
        {"id": "s1", "role": "构建器", "task": "生成数据文件"},
        {"id": "s2", "role": "分析师", "task": "分析数据", "depends_on": ["s1"]},
    ]
    plan, _ = build_run_plan(tasks, id_prefix="t")
    reg = ToolRegistry()
    reg.register(_FileWriteTool())
    rounds = [
        # s1 round 1: write the file; round 2: a short prose handoff
        [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=0,
                        id="c1",
                        function_name="file_write",
                        arguments_delta='{"path": "data/out.csv", "content": "a,b\\n1,2"}',
                    )
                ]
            )
        ],
        # Pad past MIN_UPSTREAM_BODY_CHARS so brief-promotion fixtures stay realistic.
        # files_touched can flow into the downstream prompt.
            [
                LLMChunk(
                    delta_content=(
                        "已生成 data/out.csv。"
                        "上游构建器已将数据集写入工作区，下游可用 file_read 按路径读取完整内容后继续分析。"
                        "表头与样例行已落盘，请据此完成统计分析。"
                    )
                )
            ],
        # s2: final answer (single round)
        [LLMChunk(delta_content="分析完成")],
    ]
    provider = _ScriptedRounds(rounds)
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="原始请求",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    assert res["t_s1"].files_touched == ["data/out.csv"]
    assert res["t_s2"].phase is RunPhase.COMPLETED
    downstream_user = provider.user_messages[-1]  # the analyst's opening prompt
    assert "data/out.csv" in downstream_user  # got the pointer
    assert "file_read" in downstream_user
