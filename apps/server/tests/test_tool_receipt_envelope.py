"""Model failure status line, and per-stream run receipt caps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentcore.api.schemas.messages import normalize_local_turn_tool_failure_code
from agentcore.core.text import DEFAULT_ELISION_MARKER
from agentcore.core.types import ToolFace
from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.delegate.empty_tasks import EMPTY_DELEGATE_MSG
from agentcore.runtime.engine.tool_exec import (
    TOOL_FAILED_MARKER,
    execute_tools,
    strip_model_failure_envelope,
    with_tool_failed_marker,
)
from agentcore.runtime.engine.tool_exec_args import model_failure_status
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.turn.outcome import salvage_captain_delegate_reply
from agentcore.tools.builtin.run_streams import (
    RUN_RECEIPT_BUDGET,
    cap_inserted_bodies,
)
from agentcore.tools.builtin.run_verify import _format_check_output
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.protocol import ExecutionResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_model_failure_status_uses_existing_signals():
    assert model_failure_status(failure_code="source_grep_redirect") == "redirect"
    assert model_failure_status(failure_code="postcondition_failed") == "postcondition"
    assert (
        model_failure_status(
            failure_code="liveness_timeout",
            metadata={"liveness_timeout": True, "error_class": "permanent"},
        )
        == "timeout"
    )
    assert (
        model_failure_status(failure_code="exec_env_sandbox_unavailable") == "permanent"
    )
    assert model_failure_status(failure_code="exec_forced_stop", contract_failure=True) == (
        "timeout"
    )
    assert model_failure_status(contract_failure=True) == "validation"
    assert model_failure_status(policy_failure=True) == "permission"
    assert model_failure_status(metadata={"error_class": "permanent"}) == "permanent"
    assert model_failure_status() == "error"


def test_failure_receipt_leads_with_status_and_strips_for_classifiers():
    marked = with_tool_failed_marker("old_string 为空", status="validation")
    assert marked.startswith("error: validation\nold_string 为空\n")
    assert marked.endswith(TOOL_FAILED_MARKER)
    assert with_tool_failed_marker(marked, status="permission") == marked
    assert strip_model_failure_envelope(marked) == "old_string 为空"
    assert (
        strip_model_failure_envelope(f"error: validation\n{EMPTY_DELEGATE_MSG}")
        == f"error: validation\n{EMPTY_DELEGATE_MSG}"
    )
    enveloped = with_tool_failed_marker(EMPTY_DELEGATE_MSG, status="validation")
    assert strip_model_failure_envelope(enveloped) == EMPTY_DELEGATE_MSG
    assert (
        normalize_local_turn_tool_failure_code(enveloped) == "declaration_empty"
    )
    messages = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="d1",
                    function=ToolCallFunction(name="delegate", arguments="{}"),
                )
            ],
        ),
        LLMMessage(role="tool", content=enveloped, tool_call_id="d1"),
    ]
    assert salvage_captain_delegate_reply(
        final_content="", messages=messages, role="captain"
    ) == EMPTY_DELEGATE_MSG


class _Reject:
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_search",
            description="stub",
            parameters={"type": "object", "properties": {}},
            face=ToolFace.SEARCH,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            success=False,
            output="查询词过多",
            error="查询词过多",
            contract_failure=True,
        )


@pytest.mark.anyio
async def test_status_line_is_model_transcript_only():
    reg = ToolRegistry()
    reg.register(_Reject())
    sink = EventSink()
    messages, _, attempts = await execute_tools(
        [
            ToolCall(
                id="c1",
                function=ToolCallFunction(name="web_search", arguments="{}"),
            )
        ],
        reg,
        ToolContext.create(
            execution_id="e",
            run_id="s",
            agent_id="a",
            user_id="u",
            backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        ),
        sink,
        approval_gate=None,
        run_id="r1",
    )
    assert attempts[0].contract_failure is True
    assert (messages[0].content or "").startswith("error: validation\n查询词过多\n")
    ends = [event for event in sink.history_snapshot() if event.type == EventType.TOOL_USE_END]
    assert len(ends) == 1
    assert ends[0].payload["result"] == "查询词过多"
    assert "error: validation" not in ends[0].payload["result"]


def test_short_layout_is_unchanged_when_it_fits():
    def render(out: str, err: str) -> str:
        parts = []
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        text = "\n".join(parts) if parts else "（无输出）"
        return text + "\n\n退出码：1"

    stdout, stderr = "ok", "no"
    capped = cap_inserted_bodies([stdout, stderr], render)
    assert capped == [stdout, stderr]
    assert render(*capped) == "stdout:\nok\nstderr:\nno\n\n退出码：1"


def test_long_stdout_does_not_eat_stderr_or_exit_line():
    stderr = "ERR_NEEDLE boom"
    exit_line = "\n\n退出码：2"

    def render(out: str, err: str) -> str:
        return f"stdout:\n{out}\nstderr:\n{err}{exit_line}"

    stdout = "S" * 20000
    capped_out, capped_err = cap_inserted_bodies([stdout, stderr], render)
    text = render(capped_out, capped_err)
    assert len(text) <= RUN_RECEIPT_BUDGET
    assert stderr in text
    assert text.endswith("退出码：2")
    assert DEFAULT_ELISION_MARKER in capped_out
    assert DEFAULT_ELISION_MARKER not in capped_err
    assert text.index(DEFAULT_ELISION_MARKER) < text.index("stderr:")
    assert text.index("stderr:") < text.index("退出码：2")


def test_verify_header_survives_a_long_stdout():
    raw = _format_check_output(
        check="command",
        command_argv=["pytest"],
        exec_result=ExecutionResult(
            success=False,
            stdout="A" * 20000,
            stderr="VERIFY_ERR_NEEDLE",
            exit_code=1,
            duration_ms=10,
        ),
        duration_seconds=0.1,
        budget_exceeded=False,
        budget_seconds=1200,
        command_display="pytest",
    )
    assert raw.startswith("## 验证结果：未通过\n退出码 1")
    assert "VERIFY_ERR_NEEDLE" in raw
    assert len(raw) <= RUN_RECEIPT_BUDGET
    assert DEFAULT_ELISION_MARKER in raw
