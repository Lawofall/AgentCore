"""交付形态对齐 (executor wiring): a FILE deliverable's contract checks read the run's
LANDED files, not just the chat body.

Reproduces the real-conversation false-fail: a worker writes a paper carrying every
required section to a workspace file but streams only a terse chat note — the old gate
checked ``required_sections`` / ``min_length`` against the note alone and wrongly failed
「缺章节 / 太短」. The pure semantics live in ``test_runs_contract.py``; this exercises the
executor's load-then-check path against a real workspace backend.
"""

import json

from agentcore.core.types import ToolApproval, ToolFace
from agentcore.llm.provider.protocol import LLMChunk, ToolCallDelta
from agentcore.runtime.events import EventSink
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.runtime.runs.executor import build_agent_executor
from agentcore.runtime.runs.types import RunPhase
from agentcore.runtime.runs.wave import WaveScheduler
from agentcore.tools.file_products import file_product
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from agentcore.workspace.stage_dirs import RESEARCH_DIR


class _RealFileWriteTool:
    """A ``file_write`` that actually persists to the run's workspace backend, so the
    contract's file-content channel can read the paper back."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_write",
            description="write file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            face=ToolFace.EXECUTION,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001
        self.calls += 1
        await context.backend.write(arguments["path"], arguments["content"])
        # 自报产物: what a run landed comes off the RESULT, so even a stub must report it.
        return ToolResult(
            tool_call_id="",
            success=True,
            output="written",
            file_products=[file_product(arguments["path"])],
        )


class _WriteThenTerseProse:
    """Round 1 writes the paper to disk; round 2 streams a terse chat note that (on
    purpose) lacks the required sections and is short — so only the FILE can satisfy
    the contract."""

    def __init__(self, path: str, paper: str, note: str) -> None:
        self._rounds = [
            [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(
                            index=0,
                            id="w1",
                            function_name="file_write",
                            arguments_delta=json.dumps(
                                {"path": path, "content": paper}, ensure_ascii=False
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


def _ctx_over(root) -> ToolContext:  # noqa: ANN001
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=root, sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def test_file_deliverable_sections_and_length_read_from_written_file(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    ctx = _ctx_over(root)
    # artifact_dir 由声明的 artifacts 反推（role 不再是输入）；约定文档路径本身
    # 就算产品落盘，无需另行声明。
    paper_path = f"{RESEARCH_DIR}/paper.md"
    plan, _ = build_run_plan(
        [
            {
                "role": "研究员",
                "task": "写论文并落盘",
                "deliverable": {
                    "form": "files",
                    "artifacts": [paper_path],
                    "required_sections": ["方法", "结论"],
                    "min_length": 80,
                },
            }
        ],
        id_prefix="t",
    )
    reg = ToolRegistry()
    reg.register(_RealFileWriteTool())
    paper = "# 方法\n" + "详实的方法论描述。" * 12 + "\n\n# 结论\n" + "扎实可信的结论。" * 12
    # The chat note deliberately lacks the sections and is short.
    provider = _WriteThenTerseProse(paper_path, paper, f"论文已写入 {paper_path}")
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
    # Sections + min_length are satisfied by the FILE, so no contract shortfall / retry.
    assert state.warnings == []
    assert provider.calls == 2
    assert state.files_touched == [paper_path]
