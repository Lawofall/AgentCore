"""回合内工具结果清理 (clear_tool_uses): pure-function + loop-integration tests.

The projection ``project_cleared_window`` collapses OLD re-fetchable read-only tool
results to a compact stable pointer in the model-facing window, while the canonical
``messages`` list keeps the full output (so resume / journal are byte-identical). These
tests pin: what gets cleared vs kept, the prefix-cache invariants (stable + monotonic
+ structure-preserving + idempotent), and the wiring into ``react_loop``.
"""

import json
from pathlib import Path

from agentcore.config import settings
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCall, ToolCallFunction
from agentcore.runtime.engine import react_loop
from agentcore.runtime.engine.round import build_request_window
from agentcore.runtime.engine.tool_clear import (
    EXEC_OUTPUT_CLEAR_TOOLS,
    cleared_placeholder,
    project_cleared_window,
)
from agentcore.runtime.events import EventSink
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.llm_helpers import make_profile_params

CLEARABLE = frozenset({"file_read", "grep", "web_search"})


def test_production_keep_recent_default() -> None:
    """Investigation stacking tax vs independent exec window vs write-args near window."""
    assert settings.engine_tool_clear_keep_recent == 2
    assert settings.engine_tool_clear_exec_keep_recent == 1
    assert settings.engine_write_args_clear_keep_recent == 1
    assert frozenset({"host", "run"}) == EXEC_OUTPUT_CLEAR_TOOLS


def _read_pair(call_id: str, path: str, result: str, *, tool: str = "file_read") -> list[LLMMessage]:
    """An assistant tool-call round + its tool result, as the loop accumulates them."""
    return [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(name=tool, arguments=json.dumps({"path": path})),
                )
            ],
        ),
        LLMMessage(role="tool", content=result, tool_call_id=call_id),
    ]


def _read_batch(ids_and_paths: list[tuple[str, str]], result: str) -> list[LLMMessage]:
    """One assistant message that issued several file_reads in parallel."""
    tool_calls = [
        ToolCall(
            id=call_id,
            function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": path})),
        )
        for call_id, path in ids_and_paths
    ]
    return [
        LLMMessage(role="assistant", content=None, tool_calls=tool_calls),
        *[
            LLMMessage(role="tool", content=result, tool_call_id=call_id)
            for call_id, _path in ids_and_paths
        ],
    ]


def _exec_pair(
    call_id: str, tool: str, arguments: dict, result: str
) -> list[LLMMessage]:
    return [
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(name=tool, arguments=json.dumps(arguments)),
                )
            ],
        ),
        LLMMessage(role="tool", content=result, tool_call_id=call_id),
    ]


def _window(n_pairs: int, *, size: int = 200, tool: str = "file_read") -> list[LLMMessage]:
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    for i in range(n_pairs):
        msgs += _read_pair(f"c{i}", f"src/f{i}.py", "X" * size, tool=tool)
    return msgs


def _cleared_ids(messages: list[LLMMessage]) -> list[str]:
    return [
        m.tool_call_id
        for m in messages
        if m.role == "tool" and (m.content or "").startswith("[已清理")
    ]


# ── pure function ───────────────────────────────────────────────────────────


def test_keeps_recent_clears_old():
    msgs = _window(8)  # 8 serial rounds → 8 assistant messages
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    # First 6 rounds cleared, last 2 (c6, c7) kept verbatim.
    assert _cleared_ids(out) == [f"c{i}" for i in range(6)]
    kept = [m for m in out if m.role == "tool" and not (m.content or "").startswith("[已清理")]
    assert [m.tool_call_id for m in kept] == ["c6", "c7"]
    assert all(len(m.content or "") == 200 for m in kept)


def test_parallel_batch_kept_as_one_round():
    """One assistant with six file_reads is one keep unit — none of the batch clears."""
    msgs = [LLMMessage(role="user", content="go")]
    msgs += _read_batch([(f"b{i}", f"src/f{i}.py") for i in range(6)], "X" * 200)
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=1, min_chars=100)
    assert out is msgs
    assert _cleared_ids(out) == []


def test_older_parallel_batch_clears_when_round_falls_out():
    """keep_recent=2 keeps the last two assistant batches; the first batch of 6 clears."""
    msgs = [LLMMessage(role="user", content="go")]
    for batch in range(3):
        msgs += _read_batch(
            [(f"b{batch}_{i}", f"src/b{batch}_{i}.py") for i in range(6)],
            "X" * 200,
        )
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    assert _cleared_ids(out) == [f"b0_{i}" for i in range(6)]
    kept = [
        m.tool_call_id
        for m in out
        if m.role == "tool" and not (m.content or "").startswith("[已清理")
    ]
    assert kept == [f"b{b}_{i}" for b in (1, 2) for i in range(6)]


def test_keep_recent_zero_clears_all_qualifying():
    msgs = _window(3)
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=0, min_chars=100)
    assert _cleared_ids(out) == ["c0", "c1", "c2"]


def test_small_results_not_cleared():
    msgs = _window(8, size=50)  # every result < min_chars
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    assert out is msgs  # no-op → same object


def test_non_investigation_tool_not_cleared():
    msgs = _window(8, tool="code_execute")  # not in clearable set
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    assert out is msgs


def test_injected_user_and_assistant_untouched():
    msgs = _window(4)
    msgs.append(LLMMessage(role="user", content="[系统提示] 复盘一下进度。"))  # a nudge/reflection
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=1, min_chars=100)
    # the injected user steer and all assistant messages survive verbatim
    assert any(m.role == "user" and m.content == "[系统提示] 复盘一下进度。" for m in out)
    assert all(o.content == n.content for o, n in zip(msgs, out, strict=True) if o.role == "assistant")


def test_structure_preserved_no_orphans():
    msgs = _window(8)
    out = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    # every tool message still pairs with an assistant tool_call of the same id
    issued = {
        c.id for m in out if m.role == "assistant" and m.tool_calls for c in m.tool_calls
    }
    tool_ids = [m.tool_call_id for m in out if m.role == "tool"]
    assert len(tool_ids) == 8  # none dropped
    assert all(tid in issued for tid in tool_ids)  # no orphan tool message


def test_placeholder_stable_and_monotonic():
    # Same cleared result yields byte-identical pointer across two successive rounds
    # (prefix-cache invariant), and clearing only grows (monotonic).
    win_k = project_cleared_window(_window(5), clearable_tools=CLEARABLE, keep_recent=2, min_chars=10)
    win_k1 = project_cleared_window(_window(6), clearable_tools=CLEARABLE, keep_recent=2, min_chars=10)

    def cleared_content(window: list[LLMMessage], call_id: str) -> str | None:
        for m in window:
            if m.role == "tool" and m.tool_call_id == call_id:
                return m.content
        return None

    # c0 is cleared in both; its pointer bytes must be identical.
    assert cleared_content(win_k, "c0").startswith("[已清理")
    assert cleared_content(win_k, "c0") == cleared_content(win_k1, "c0")
    # monotonic: everything cleared at round K is still cleared at round K+1.
    assert set(_cleared_ids(win_k)).issubset(set(_cleared_ids(win_k1)))


def test_idempotent():
    msgs = _window(8)
    once = project_cleared_window(msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    twice = project_cleared_window(once, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100)
    assert [m.content for m in twice] == [m.content for m in once]


def test_placeholder_names_the_call():
    ph = cleared_placeholder("file_read", json.dumps({"path": "src/foo.py"}), 8421)
    assert "file_read" in ph and "src/foo.py" in ph and "8421" in ph
    assert "status=content_cleared" in ph
    assert "reread=allowed" in ph
    assert "path='src/foo.py'" in ph
    # deterministic
    assert ph == cleared_placeholder("file_read", json.dumps({"path": "src/foo.py"}), 8421)


def test_grep_placeholder_keeps_refetch_invite():
    ph = cleared_placeholder("grep", json.dumps({"path": "src/foo.py", "pattern": "x"}), 900)
    assert "可重新调用该工具获取" in ph
    assert "如仍需要可重新调用该工具获取" not in ph
    assert "仍有该正文则勿重调" in ph


def test_exec_placeholder_forbids_rerun():
    ph = cleared_placeholder(
        "host",
        json.dumps({"action": "shell", "command": "Get-ChildItem"}),
        4000,
        already_executed=True,
    )
    assert "host" in ph and "Get-ChildItem" in ph
    assert "勿仅为回看而重跑" in ph
    assert "可重新调用该工具获取" not in ph
    assert ph == cleared_placeholder(
        "host",
        json.dumps({"action": "shell", "command": "Get-ChildItem"}),
        4000,
        already_executed=True,
    )


def test_exec_placeholder_truncates_long_command():
    long = "echo " + ("x" * 200)
    ph = cleared_placeholder(
        "host", json.dumps({"action": "shell", "command": long}), 111, already_executed=True
    )
    assert long not in ph
    assert "…" in ph
    assert "111" in ph


def test_empty_clearable_is_noop():
    msgs = _window(8)
    assert project_cleared_window(msgs, clearable_tools=frozenset(), keep_recent=2, min_chars=100) is msgs


# ── loop wiring ─────────────────────────────────────────────────────────────


class _FakeReadTool:
    """A read-only NEVER-approval FILESYSTEM tool so the loop classifies it as an
    investigation tool (clearable). Never executed by these tests (the window is
    pre-seeded and the provider finishes round 0)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_read",
            description="read a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            category=ToolCategory.FILESYSTEM,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments, context) -> ToolResult:  # noqa: ANN001 - duck-typed
        return ToolResult(tool_call_id="", success=True, output="unused")


class _CapturingProvider:
    """Records the request window each round, then yields scripted chunks."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0
        self.windows: list[list[LLMMessage]] = []

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        self.windows.append(list(request.messages))
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )


async def _run_loop(provider: _CapturingProvider) -> None:
    registry = ToolRegistry()
    registry.register(_FakeReadTool())
    messages = _window(8)  # pre-seeded prior reads
    profile = make_profile_params(max_rounds=4)
    await react_loop(
        messages=messages,
        llm=provider,
        tools=registry,
        sink=EventSink(),
        tool_context=_context(),
        profile=profile,
        turn_model="m",
        approval_gate=None,
    )


async def test_loop_clears_old_reads_in_request_window(monkeypatch):
    monkeypatch.setattr(settings, "engine_tool_clear_keep_recent", 2)
    monkeypatch.setattr(settings, "engine_tool_clear_min_chars", 100)
    provider = _CapturingProvider([[LLMChunk(delta_content="调查完成，结论如下。")]])
    await _run_loop(provider)
    window = provider.windows[0]
    # 6 older serial rounds cleared, 2 most-recent rounds kept full — the canonical
    # messages the loop holds are untouched (only the request view is projected).
    assert len(_cleared_ids(window)) == 6
    kept = [m for m in window if m.role == "tool" and not (m.content or "").startswith("[已清理")]
    assert len(kept) == 2 and all(len(m.content or "") == 200 for m in kept)


async def test_loop_no_clear_when_keep_recent_high(monkeypatch):
    monkeypatch.setattr(settings, "engine_tool_clear_keep_recent", 100)  # effectively off
    monkeypatch.setattr(settings, "engine_tool_clear_min_chars", 100)
    provider = _CapturingProvider([[LLMChunk(delta_content="调查完成。")]])
    await _run_loop(provider)
    window = provider.windows[0]
    assert _cleared_ids(window) == []  # nothing cleared
    assert all(len(m.content or "") == 200 for m in window if m.role == "tool")


# ── R1: file_read digest + sticky re-read ───────────────────────────────────


def test_file_read_clear_keeps_structural_summary_under_min_chars():
    from agentcore.runtime.engine.tool_clear import structural_file_read_summary

    md = "# Title\n\n## Section\n\n" + ("body " * 400)
    path = "docs/spec.md"
    msgs = [LLMMessage(role="user", content="go")]
    for i in range(4):
        msgs += _read_pair(f"c{i}", path if i < 2 else f"other/{i}.md", md)
    out = project_cleared_window(
        msgs,
        clearable_tools=CLEARABLE,
        keep_recent=1,
        min_chars=200,
        summary_max_chars=1200,
    )
    cleared = [
        m
        for m in out
        if m.role == "tool" and (m.content or "").startswith("[已清理")
    ]
    assert cleared
    for m in cleared:
        assert len(m.content or "") < 200
        # file_read of the md path should carry a digest when room allows
        if "docs/spec.md" in (m.content or ""):
            assert "自动" in (m.content or "") or "标题" in (m.content or "")
    # pure + idempotent
    digest = structural_file_read_summary(path, md, max_chars=400)
    assert digest is not None and "Title" in digest
    assert digest == structural_file_read_summary(path, md, max_chars=400)
    once = project_cleared_window(
        msgs, clearable_tools=CLEARABLE, keep_recent=1, min_chars=200, summary_max_chars=1200
    )
    twice = project_cleared_window(
        once, clearable_tools=CLEARABLE, keep_recent=1, min_chars=200, summary_max_chars=1200
    )
    assert [m.content for m in twice] == [m.content for m in once]


def test_file_read_summary_max_chars_zero_is_pointer_only():
    md = "# H\n" + ("x" * 300)
    msgs = _window(4, size=300)
    # overwrite first result with structured md for the named path
    msgs = [LLMMessage(role="user", content="go")]
    for i in range(4):
        msgs += _read_pair(f"c{i}", f"f{i}.md", md)
    out = project_cleared_window(
        msgs,
        clearable_tools=CLEARABLE,
        keep_recent=1,
        min_chars=100,
        summary_max_chars=0,
    )
    for m in out:
        if m.role == "tool" and (m.content or "").startswith("[已清理"):
            assert "自动" not in (m.content or "")
            assert "\n" not in (m.content or "")


def test_grep_clear_stays_pointer_only_even_with_summary_budget():
    big = "hit " * 80
    msgs = [LLMMessage(role="user", content="go")]
    for i in range(4):
        msgs += _read_pair(f"c{i}", f"f{i}.py", big, tool="grep")
    out = project_cleared_window(
        msgs,
        clearable_tools=CLEARABLE,
        keep_recent=1,
        min_chars=100,
        summary_max_chars=1200,
    )
    for m in out:
        if m.role == "tool" and (m.content or "").startswith("[已清理"):
            assert "自动" not in (m.content or "")


def test_canonical_messages_untouched_by_projection():
    """Journal / canonical keep full bodies — only the projected view clears."""
    msgs = _window(6, size=200)
    originals = [m.content for m in msgs if m.role == "tool"]
    out = project_cleared_window(
        msgs, clearable_tools=CLEARABLE, keep_recent=2, min_chars=100, summary_max_chars=400
    )
    assert out is not msgs
    assert [m.content for m in msgs if m.role == "tool"] == originals


# ── exec output family (host / run) ──────────────────────────────


def test_exec_keeps_one_clears_old():
    msgs = [LLMMessage(role="user", content="go")]
    for i in range(4):
        msgs += _exec_pair(
            f"h{i}", "host", {"action": "shell", "command": f"echo {i}"}, "X" * 200
        )
    out = project_cleared_window(
        msgs,
        clearable_tools=EXEC_OUTPUT_CLEAR_TOOLS,
        keep_recent=1,
        min_chars=100,
        already_executed=True,
    )
    assert _cleared_ids(out) == ["h0", "h1", "h2"]
    kept = [m for m in out if m.role == "tool" and not (m.content or "").startswith("[已清理")]
    assert [m.tool_call_id for m in kept] == ["h3"]
    stub = next(m for m in out if m.tool_call_id == "h0")
    assert "勿仅为回看而重跑" in (stub.content or "")
    assert "可重新调用该工具获取" not in (stub.content or "")


def test_exec_parallel_batch_kept_as_one_round():
    msgs = [LLMMessage(role="user", content="go")]
    tool_calls = [
        ToolCall(
            id=f"h{i}",
            function=ToolCallFunction(
                name="host",
                arguments=json.dumps({"action": "shell", "command": f"echo {i}"}),
            ),
        )
        for i in range(3)
    ]
    msgs.append(LLMMessage(role="assistant", content=None, tool_calls=tool_calls))
    msgs.extend(
        LLMMessage(role="tool", content="X" * 200, tool_call_id=f"h{i}") for i in range(3)
    )
    out = project_cleared_window(
        msgs,
        clearable_tools=EXEC_OUTPUT_CLEAR_TOOLS,
        keep_recent=1,
        min_chars=100,
        already_executed=True,
    )
    assert out is msgs
    assert _cleared_ids(out) == []


def test_exec_and_investigation_windows_independent(monkeypatch):
    monkeypatch.setattr(settings, "engine_tool_clear_keep_recent", 2)
    monkeypatch.setattr(settings, "engine_tool_clear_exec_keep_recent", 1)
    monkeypatch.setattr(settings, "engine_tool_clear_min_chars", 100)
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    for i in range(3):
        msgs += _read_pair(f"r{i}", f"src/f{i}.py", "R" * 200)
        msgs += _exec_pair(f"s{i}", "run", {"action": "read", "process_id": f"p{i}"}, "S" * 200)
    out = build_request_window(msgs, investigation_tools=CLEARABLE, round_idx=0)
    assert set(_cleared_ids(out)) == {"r0", "s0", "s1"}
    stub = next(m for m in out if m.tool_call_id == "s0")
    assert "p0" in (stub.content or "")
    assert "勿仅为回看而重跑" in (stub.content or "")
    read_stub = next(m for m in out if m.tool_call_id == "r0")
    assert "path='src/f0.py'" in (read_stub.content or "")
    assert "status=content_cleared" in (read_stub.content or "")
    assert "reread=allowed" in (read_stub.content or "")


def test_build_request_window_leaves_code_execute(monkeypatch):
    monkeypatch.setattr(settings, "engine_tool_clear_min_chars", 100)
    msgs = _window(4, tool="code_execute")
    out = build_request_window(msgs, investigation_tools=CLEARABLE, round_idx=0)
    assert _cleared_ids(out) == []


def test_build_request_window_keeps_recent_write_args():
    """近端一轮 str_replace 全文留在 arguments；更早的 file_write 压成 path。"""
    old = "OLD" * 200
    new = "NEW" * 200
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    msgs += [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="w0",
                    function=ToolCallFunction(
                        name="file_write",
                        arguments=json.dumps({"path": "a.md", "content": old}),
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content="ok", tool_call_id="w0"),
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="w1",
                    function=ToolCallFunction(
                        name="str_replace",
                        arguments=json.dumps(
                            {"path": "a.md", "old_string": "x", "new_string": new}
                        ),
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content="ok", tool_call_id="w1"),
    ]
    out = build_request_window(msgs, investigation_tools=CLEARABLE, round_idx=0)
    older = json.loads(out[1].tool_calls[0].function.arguments)
    recent = json.loads(out[3].tool_calls[0].function.arguments)
    assert older == {"path": "a.md"}
    assert recent["new_string"] == new


async def test_loop_clears_old_exec_in_request_window(monkeypatch):
    monkeypatch.setattr(settings, "engine_tool_clear_exec_keep_recent", 1)
    monkeypatch.setattr(settings, "engine_tool_clear_min_chars", 100)
    provider = _CapturingProvider([[LLMChunk(delta_content="完成。")]])
    registry = ToolRegistry()
    registry.register(_FakeReadTool())
    messages: list[LLMMessage] = [LLMMessage(role="user", content="go")]
    for i in range(4):
        messages += _exec_pair(f"h{i}", "host", {"action": "shell", "command": f"dir {i}"}, "Y" * 200)
    await react_loop(
        messages=messages,
        llm=provider,
        tools=registry,
        sink=EventSink(),
        tool_context=_context(),
        profile=make_profile_params(max_rounds=4),
        turn_model="m",
        approval_gate=None,
    )
    window = provider.windows[0]
    assert _cleared_ids(window) == ["h0", "h1", "h2"]
    stub = next(m for m in window if m.tool_call_id == "h0")
    assert "勿仅为回看而重跑" in (stub.content or "")
