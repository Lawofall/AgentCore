"""交付事实口径 (executor wiring): a worker that lands its deliverable ONLY through
``run`` (sandbox copy-out) satisfies ``requires_files`` — no wasted rewrite
forcing it to regenerate the whole product via ``file_write``.

Reproduces the collab-graph waste: the product really landed (staging write-back), a
downstream worker read it, but ``requires_files`` counted only ``file_write`` intents
and failed — burning a multi-thousand-token regeneration. The structured write-back
channel makes the landing a fact the gate honours (and the CEO manifest inherits it).
甲⁺：纯正文零落盘改为 soft-complete（warning），不再硬 FAILED。

端到端用**真** ``SubprocessSandbox``：本地腿曾恒不填 ``written_files``，这条链路只能
靠一个模拟云端 copy-out 的假壳 backend 才跑得通——假壳一撤就红。现在本地沙箱自己
事后看盘上报（``tools/sandbox/written_scan``），假壳没有存在理由了：真脚本落真盘，
一路走到 ``files_touched`` 与 CEO 交付清单。
"""

import json
import sys

import pytest

from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime.delegate.completion import collect_delivered_files
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.builtin.run import RunTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import (
    SubprocessSandbox,
    probe_available_languages,
)
from agentcore.workspace.server import ServerWorkspace
from tests.runs_executor.conftest import _ContentProvider, _ctx


class _RunThenNote:
    """Round 1: call ``run`` (lands the file via copy-out); round 2: stream a
    terse chat note — the product is on disk, deliberately NOT pasted into the reply."""

    def __init__(self, command: str, note: str) -> None:
        self._rounds = [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="x1",
                            function_name="run",
                            arguments_delta=json.dumps(
                                {"command": command}, ensure_ascii=False
                            ),
                        )
                    ]
                )
            ],
            [LLMChunk(delta_content=note)],
        ]
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001
        chunks = (
            self._rounds[self.calls]
            if self.calls < len(self._rounds)
            else [LLMChunk(delta_content="done")]
        )
        self.calls += 1
        for chunk in chunks:
            yield chunk


@pytest.mark.skipif(
    "python" not in probe_available_languages(),
    reason=f"no python launcher on PATH ({sys.platform}) for the real sandbox",
)
async def test_requires_files_satisfied_by_code_execute_landing(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    backend = ServerWorkspace(root=root, sandbox=SubprocessSandbox())
    ctx = ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=backend,
        user_id="u",
    )
    plan, _ = build_run_plan(
        [
            {
                "role": "分析",
                "task": "跑脚本生成报告并落盘",
                "deliverable": {"form": "files"},
            }
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(RunTool(location="server"))
    script = (
        "open('report.md', 'w', encoding='utf-8')"
        ".write('# 报告\\n扎实可信的分析正文。')"
    )
    provider = _RunThenNote(
        f"python -c {script!r}",
        "报告已生成，见 report.md",
    )
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=reg,
        sink=EventSink(),
        base_tool_context=ctx,
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    # The script really wrote the workspace (no fake copy-out staging it for us).
    assert "扎实可信" in (root / "report.md").read_text(encoding="utf-8")
    # form=files satisfied by the run landing → no contract shortfall / retry.
    # 落在工作区根：收口认盘，不再发约定目录软提醒。
    assert state.warnings == []
    assert provider.calls == 2  # no wasted regenerate-via-file_write round
    assert state.files_touched == ["report.md"]
    # CEO handoff manifest (collect_delivered_files reads files_touched) inherits it.
    assert collect_delivered_files(res) == ["report.md"]


async def test_files_form_soft_completes_on_pure_prose_no_landing():
    """甲⁺：无 run / file_write，仅散文；strict+form=files 仍 soft-complete。"""
    plan, _ = build_run_plan(
        [
            {
                "role": "分析",
                "task": "生成报告并落盘",
                "deliverable": {"form": "files", "strict": True},
            }
        ],
        id_prefix="t",
    )
    provider = _ContentProvider(["我把整份报告贴在这里……", "还是只有正文没有落盘……"])
    executor = build_agent_executor(
        plan=plan,
        llm=provider,
        tools=ToolRegistry(),
        sink=EventSink(),
        base_tool_context=_ctx(),
        system_prompt="SYS",
        user_message="req",
        execution_id="e",
        approval_gate=None,
    )
    res = await WaveScheduler().run(plan, executor)
    state = res["t_1"]
    assert state.phase is RunPhase.COMPLETED
    assert not (state.files_touched or [])
    assert any("工作区" in w for w in (state.warnings or []))
