"""B2: captain recon harvest → worker opening inject."""

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.delegate.captain_recon import (
    captain_recon_heading,
    harvest_captain_recon,
    resolve_captain_recon_for_delegate,
)
from agentcore.runtime.runs.executor.context import _build_messages
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunSpec


def _tc(name: str, args: str, call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=ToolCallFunction(name=name, arguments=args),
    )


def test_harvest_captain_recon_from_transcript():
    messages = [
        LLMMessage(role="user", content="帮我启动"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                _tc("file_list", '{"directory":"."}', "c1"),
                _tc("file_read", '{"path":"package.json"}', "c2"),
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="c1",
            content="package.json\nsrc/\nREADME.md",
        ),
        LLMMessage(
            role="tool",
            tool_call_id="c2",
            content='{"name":"whiteboard","scripts":{"dev":"vite"}}',
        ),
    ]
    brief = harvest_captain_recon(messages)
    assert "file_list" in brief
    assert "package.json" in brief
    assert "whiteboard" in brief
    assert "vite" in brief


def test_harvest_skips_failed_tools_and_empty():
    messages = [
        LLMMessage(
            role="assistant",
            tool_calls=[_tc("file_read", '{"path":"x.ts"}', "f1")],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="f1",
            content="错误：文件不存在<!--agentcore:tool_failed-->",
        ),
    ]
    assert harvest_captain_recon(messages) == ""


def test_harvest_keeps_most_recent_entries():
    calls = []
    results = []
    for i in range(8):
        cid = f"c{i}"
        calls.append(_tc("file_read", f'{{"path":"f{i}.ts"}}', cid))
        results.append(LLMMessage(role="tool", tool_call_id=cid, content=f"body-{i}"))
    messages = [
        LLMMessage(role="assistant", tool_calls=calls),
        *results,
    ]
    brief = harvest_captain_recon(messages, max_entries=3)
    assert "f5.ts" in brief
    assert "f7.ts" in brief
    assert "f0.ts" not in brief


def test_harvest_keeps_source_search_over_low_signal():
    """Real-source grep must survive later .env / generated / release / long docs."""
    grep_hits = (
        "apps/server/agentcore/runtime/credentials.py:12: apply_mode = ...\n"
        "apps/server/agentcore/runtime/credentials.py:40: on_demand_rules\n"
        "apps/server/agentcore/runtime/credentials.py:88: always_rules\n"
        "30 matches across 3 files"
    )
    audit_body = "旧审计报告正文。" * 80
    calls = [
        _tc(
            "grep",
            '{"path":".","pattern":"apply_mode|on_demand_rules|always_rules"}',
            "g1",
        ),
        _tc("file_read", '{"path":".env"}', "e1"),
        _tc(
            "file_read",
            '{"path":"packages/contract-types/src/api.generated.ts"}',
            "gen1",
        ),
        _tc("file_list", '{"directory":"release","pattern":"*"}', "rel1"),
        _tc("file_read", '{"path":"release/win/AgentCore.exe"}', "rel2"),
        _tc("file_read", '{"path":"dist/bundle.js"}', "dist1"),
    ]
    results = [
        LLMMessage(role="tool", tool_call_id="g1", content=grep_hits),
        LLMMessage(role="tool", tool_call_id="e1", content="OPENAI_API_KEY=sk-xxx"),
        LLMMessage(role="tool", tool_call_id="gen1", content="export type Foo = {"),
        LLMMessage(role="tool", tool_call_id="rel1", content="AgentCore.exe\nlatest.yml"),
        LLMMessage(role="tool", tool_call_id="rel2", content="MZ..."),
        LLMMessage(role="tool", tool_call_id="dist1", content="!function(){"),
    ]
    # Later peeks outnumber the keep window so a recency-only harvest would drop the grep.
    for i in range(8):
        cid = f"doc{i}"
        calls.append(_tc("file_read", f'{{"path":"docs/旧审计-{i}.md"}}', cid))
        results.append(LLMMessage(role="tool", tool_call_id=cid, content=audit_body))
    messages = [LLMMessage(role="assistant", tool_calls=calls), *results]
    brief = harvest_captain_recon(messages)
    assert "credentials.py" in brief
    assert "apply_mode" in brief
    assert "on_demand_rules" in brief
    assert "always_rules" in brief
    assert "api.generated.ts" not in brief
    assert "OPENAI_API_KEY" not in brief
    assert "AgentCore.exe" not in brief


def test_harvest_includes_code_search_pointer():
    messages = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                _tc(
                    "code_search",
                    '{"query":"apply_mode rules","path_prefix":"apps/server"}',
                    "cs1",
                ),
            ],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="cs1",
            content="apps/server/agentcore/runtime/credentials.py  apply_mode",
        ),
    ]
    brief = harvest_captain_recon(messages)
    assert "code_search" in brief
    assert "apps/server" in brief
    assert "apply_mode" in brief
    assert "credentials.py" in brief


def test_resolve_skips_nested_depth():
    assert resolve_captain_recon_for_delegate(depth=1) == ""
    assert resolve_captain_recon_for_delegate(depth=2) == ""


def test_build_messages_includes_captain_recon_block():
    plan = RunPlan()
    plan.add(RunSpec(run_id="w0", role="运维", task="启动", agent_id="w0"))
    msgs = _build_messages(
        plan,
        plan.by_id("w0"),
        {},
        "SYS",
        "帮我启动",
        captain_recon="- `file_list` `.` →\npackage.json\nsrc/",
    )
    user = msgs[1].content or ""
    assert captain_recon_heading() in user
    assert "package.json" in user
    assert "勿再无增量" in user
