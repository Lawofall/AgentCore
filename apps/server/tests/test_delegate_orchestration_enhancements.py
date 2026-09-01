"""委派编排三项增强：CEO 评审前置 / handoff 写参清理 / 记忆复用。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction
from agentcore.memory.store import FileMemoryStore, topic_path
from agentcore.runtime.context.consult_sources import MemoryConsultSource, MergedConsultSource
from agentcore.runtime.delegate.ceo_review import deterministic_ceo_review, run_ceo_review
from agentcore.runtime.engine.write_args_clear import (
    cleared_write_stub_rejection,
    landed_result_note,
    project_cleared_write_args,
    write_args_identity,
)
from agentcore.runtime.events import plan_review_required
from agentcore.runtime.events.payloads.interaction import PlanReviewRequiredPayload
from agentcore.runtime.memory_consult_cache import (
    consulted_memory_cache,
    get_consult_cache,
    remember_consult,
    seed_consult_cache_from_window,
)
from agentcore.runtime.runs.types import RunPhase, RunSpec, RunState
from agentcore.tools.builtin.consult import ConsultTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(user_id: str = "u") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id=user_id,
    )


def _state(summary: str, *, files: list[str] | None = None) -> RunState:
    return RunState(
        phase=RunPhase.COMPLETED,
        content=summary,
        debrief={"summary": summary, "key_points": ["要点A"], "assumptions": ["假设X"]},
        files_touched=files or [],
    )


# ── 1. CEO 评审前置 ──────────────────────────────────────────────────────────


def test_deterministic_ceo_review_shape():
    nodes = [RunSpec(run_id="r1", agent_id="r1", role="架构师", task="写规格")]
    completed = {"r1": _state("规格已落盘", files=["docs/spec.md"])}
    review = deterministic_ceo_review(nodes, completed)
    assert "规格" in review["conclusion"] or "架构师" in review["conclusion"]
    assert review["risks"]
    assert review["suggestions"]
    assert review["source"] == "deterministic"
    assert any("docs/spec.md" in s for s in review["suggestions"])


async def test_run_ceo_review_uses_llm_json():
    class _LLM:
        async def complete(self, request):  # noqa: ANN001
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "conclusion": "规格可过，缺错误处理",
                        "risks": ["无超时策略"],
                        "suggestions": ["补错误边界"],
                    },
                    ensure_ascii=False,
                )
            )

    nodes = [RunSpec(run_id="r1", agent_id="r1", role="架构师", task="写规格")]
    review = await run_ceo_review(
        nodes=nodes,
        completed={"r1": _state("done", files=["a.md"])},
        llm=_LLM(),
        model="test-model",
    )
    assert review["conclusion"] == "规格可过，缺错误处理"
    assert review["risks"] == ["无超时策略"]
    assert review["suggestions"] == ["补错误边界"]
    assert review["source"] == "llm"


def test_plan_review_required_carries_ceo_review():
    event = plan_review_required(
        checkpoint_id="cp1",
        conversation_id="c1",
        steps=[{"run_id": "r1", "role": "架构师", "summary": "ok"}],
        pending=[{"run_id": "r2", "role": "实现"}],
        ceo_review={
            "conclusion": "可过",
            "risks": ["风险"],
            "suggestions": ["建议"],
        },
    )
    assert event.payload["ceo_review"]["conclusion"] == "可过"
    PlanReviewRequiredPayload.model_validate(event.payload)


def test_plan_review_required_omits_ceo_review_when_absent():
    event = plan_review_required(
        checkpoint_id="cp1",
        conversation_id="c1",
        steps=[{"run_id": "r1", "role": "A", "summary": "x"}],
        pending=[],
    )
    assert "ceo_review" not in event.payload
    PlanReviewRequiredPayload.model_validate(event.payload)


# ── 2. handoff 写参清理（原写工具名 + 参数只留 path，摘要归 tool result）──


def test_projected_write_args_carry_nothing_worth_echoing():
    """参数槽只剩 path：不是可提交载荷，也就没有可照抄的东西。"""
    from agentcore.runtime.engine.write_args_clear import LANDED_STATUS_TOOL

    args = json.dumps({"path": "docs/spec.md", "content": "X" * 2000}, ensure_ascii=False)
    projected = write_args_identity(args)
    assert json.loads(projected) == {"path": "docs/spec.md"}
    # 历代被回灌过的形态一个都不许再出现在参数槽里。
    for bait in ("status", "landed", "via", "chars", "_landed_summary", "_cleared", "[已清理]"):
        assert bait not in projected
    # Constant kept for residual-imitation rejection only — never a projected name.
    assert LANDED_STATUS_TOOL == "_write_landed"
    assert LANDED_STATUS_TOOL not in {"file_write", "file_append", "str_replace"}


def test_projected_str_replace_drops_body_keys():
    """str_replace 清参：old/new 全部消失，正文不回流参数槽。"""
    anchor = (
        "- 本轮检索未获得阿里 AI 板块单独营收数据（阿里整体财报口径以集团为主），"
        "标注为待核实。\n\n---\n"
    )
    body = "## 百度\n" + ("段落内容。" * 80)
    args = json.dumps(
        {
            "path": "research/ai_cn_notes.md",
            "old_string": anchor,
            "new_string": anchor + body,
        },
        ensure_ascii=False,
    )
    projected = write_args_identity(args)
    assert json.loads(projected) == {"path": "research/ai_cn_notes.md"}
    assert body not in projected
    assert "[已清理" not in projected
    # 规模落在结果侧，供模型判断改法（整写 vs 定点替换）。
    note = landed_result_note(args, len(anchor + body))
    assert note is not None
    assert str(len(anchor + body)) in note


def test_landed_result_note_keeps_html_structure():
    """结构摘要挪到 tool result：后续改稿仍能对照，不必凭记忆盲写。"""
    from agentcore.runtime.engine.write_args_clear import structural_write_summary

    html = (
        "<!doctype html><html><body>"
        '<div id="app" class="hero shell">'
        '<button class="btn primary" id="cta">Go</button>'
        '<span class="muted">hint</span>'
        "</div></body></html>"
    )
    # Pad past min_chars so project path also exercises the summary.
    html = html + ("<!-- pad -->" * 80)
    summary = structural_write_summary("index.html", html)
    assert summary is not None
    assert "app" in summary and "cta" in summary
    assert "hero" in summary and "btn" in summary and "primary" in summary

    args = json.dumps({"path": "index.html", "content": html}, ensure_ascii=False)
    note = landed_result_note(args, len(html))
    assert note is not None
    assert "classes=[" in note and "hero" in note and "primary" in note
    assert "ids=[" in note and "app" in note
    # 摘要不得把正文带回来；参数槽更不许留下正文。
    assert html[:40] not in note
    assert html[:40] not in write_args_identity(args)
    assert len(note) < 1400  # 摘要有帽，不能变成第二份正文


def test_project_cleared_write_args_collapses_completed_writes():
    from agentcore.runtime.engine.write_args_clear import LANDED_STATUS_TOOL

    big = "正文" * 400
    call_id = "w1"
    msgs = [
        LLMMessage(role="user", content="go"),
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(
                        name="file_write",
                        arguments=json.dumps(
                            {"path": "docs/a.md", "content": big}, ensure_ascii=False
                        ),
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content="已写入 100 字节到 docs/a.md", tool_call_id=call_id),
        LLMMessage(role="assistant", content="准备 handoff"),
    ]
    out = project_cleared_write_args(msgs, min_chars=100, keep_recent=0)
    assert out is not msgs
    call = out[1].tool_calls[0]
    # Keep original write name — never emit _write_landed as function.name bait.
    assert call.function.name == "file_write"
    assert call.function.name != LANDED_STATUS_TOOL
    assert json.loads(call.function.arguments) == {"path": "docs/a.md"}
    assert big not in call.function.arguments
    # 规模落在结果侧，原结果文案保留。
    assert out[2].role == "tool"
    assert "已写入 100 字节到 docs/a.md" in out[2].content
    assert "已落盘" in out[2].content
    # No bait name anywhere in the projected window's tool_calls.
    for msg in out:
        if msg.tool_calls:
            for tc in msg.tool_calls:
                assert tc.function.name != LANDED_STATUS_TOOL
    # 幂等：再投影一次不得二次追加摘要，也不得改动参数。
    out2 = project_cleared_write_args(out, min_chars=100, keep_recent=0)
    assert out2[1].tool_calls[0].function.arguments == call.function.arguments
    assert out2[1].tool_calls[0].function.name == "file_write"
    assert out2[2].content == out[2].content


def test_project_cleared_write_args_str_replace_readonly_summary():
    """完成后投影为原名 + 只剩 path，不再保留可提交的 old/new 形状。"""
    from agentcore.runtime.engine.write_args_clear import LANDED_STATUS_TOOL

    anchor = "END_MARK\n---\n"
    big = "章节正文" * 200
    call_id = "s1"
    msgs = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(
                        name="str_replace",
                        arguments=json.dumps(
                            {
                                "path": "notes.md",
                                "old_string": anchor,
                                "new_string": anchor + big,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content="已替换 notes.md", tool_call_id=call_id),
    ]
    out = project_cleared_write_args(msgs, min_chars=100, keep_recent=0)
    assert out is not msgs
    call = out[0].tool_calls[0]
    assert call.function.name == "str_replace"
    assert call.function.name != LANDED_STATUS_TOOL
    assert json.loads(call.function.arguments) == {"path": "notes.md"}
    assert big not in call.function.arguments
    assert "已替换 notes.md" in out[1].content
    assert big not in out[1].content


def test_project_cleared_write_args_migrates_legacy_write_landed_name():
    """旧窗里的 `_write_landed` function.name 迁回 via，去掉仿调诱饵。"""
    from agentcore.runtime.engine.write_args_clear import LANDED_STATUS_TOOL

    call_id = "legacy1"
    status = json.dumps(
        {
            "status": "landed",
            "via": "file_append",
            "chars": 900,
            "path": "docs/a.md",
            "note": "已写入",
        },
        ensure_ascii=False,
    )
    msgs = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(name=LANDED_STATUS_TOOL, arguments=status),
                )
            ],
        ),
        LLMMessage(role="tool", content="ok", tool_call_id=call_id),
    ]
    out = project_cleared_write_args(msgs, min_chars=100)
    assert out is not msgs
    assert out[0].tool_calls[0].function.name == "file_append"
    assert out[0].tool_calls[0].function.name != LANDED_STATUS_TOOL
    assert json.loads(out[0].tool_calls[0].function.arguments)["via"] == "file_append"


def test_project_cleared_write_args_skips_pending_write():
    """No tool result yet → keep args (model may still be mid-write)."""
    call_id = "w1"
    msgs = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(
                        name="file_write",
                        arguments=json.dumps({"path": "a.md", "content": "Y" * 800}),
                    ),
                )
            ],
        )
    ]
    assert project_cleared_write_args(msgs, min_chars=100) is msgs


def _write_round(
    call_id: str, path: str, body: str, *, tool: str = "file_write"
) -> list[LLMMessage]:
    if tool == "str_replace":
        args = {"path": path, "old_string": "OLD_MARK", "new_string": body}
    else:
        args = {"path": path, "content": body}
    return [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    function=ToolCallFunction(
                        name=tool,
                        arguments=json.dumps(args, ensure_ascii=False),
                    ),
                )
            ],
        ),
        LLMMessage(role="tool", content=f"ok {path}", tool_call_id=call_id),
    ]


def test_project_cleared_write_args_keeps_sole_completed_write():
    """默认 keep_recent=1：刚落下的唯一一刀全文留着，供下一刀当 old_string。"""
    body = "章节正文" * 200
    msgs = [LLMMessage(role="user", content="go")] + _write_round(
        "s1", "notes.md", body, tool="str_replace"
    )
    out = project_cleared_write_args(msgs, min_chars=100)
    assert out is msgs
    args = json.loads(out[1].tool_calls[0].function.arguments)
    assert args["new_string"] == body
    assert "old_string" in args


def test_project_cleared_write_args_collapses_older_keeps_recent():
    """连续两轮写：更早的压成 path，最近一轮全文仍在。"""
    old_body = "旧稿" * 200
    new_body = "新稿" * 200
    msgs = (
        [LLMMessage(role="user", content="go")]
        + _write_round("w0", "a.md", old_body)
        + _write_round("w1", "a.md", new_body, tool="str_replace")
    )
    out = project_cleared_write_args(msgs, min_chars=100)
    assert out is not msgs
    older = json.loads(out[1].tool_calls[0].function.arguments)
    recent = json.loads(out[3].tool_calls[0].function.arguments)
    assert older == {"path": "a.md"}
    assert old_body not in out[1].tool_calls[0].function.arguments
    assert "已落盘" in out[2].content
    assert recent["new_string"] == new_body
    assert new_body not in (out[2].content or "")


def test_project_cleared_write_args_keeps_parallel_writes_in_same_round():
    """同一 assistant 消息里并行两刀写：keep_recent=1 都留（都是刚落下的那一轮）。"""
    a = "AAAA" * 200
    b = "BBBB" * 200
    msgs = [
        LLMMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="p0",
                    function=ToolCallFunction(
                        name="str_replace",
                        arguments=json.dumps(
                            {"path": "a.ts", "old_string": "x", "new_string": a}
                        ),
                    ),
                ),
                ToolCall(
                    id="p1",
                    function=ToolCallFunction(
                        name="str_replace",
                        arguments=json.dumps(
                            {"path": "b.ts", "old_string": "y", "new_string": b}
                        ),
                    ),
                ),
            ],
        ),
        LLMMessage(role="tool", content="ok a", tool_call_id="p0"),
        LLMMessage(role="tool", content="ok b", tool_call_id="p1"),
    ]
    out = project_cleared_write_args(msgs, min_chars=100)
    assert out is msgs
    args0 = json.loads(out[0].tool_calls[0].function.arguments)
    args1 = json.loads(out[0].tool_calls[1].function.arguments)
    assert args0["new_string"] == a
    assert args1["new_string"] == b


def test_cleared_write_stub_rejection_exact_markers_only():
    """硬拒仅命中 stub / landed 形；正常短文 / 含「已清理」散文不拦。"""
    assert cleared_write_stub_rejection({"path": "a.md", "content": "[已清理]"}) is not None
    assert (
        cleared_write_stub_rejection(
            {"path": "a.md", "old_string": "x", "new_string": "[已清理·须重填]"}
        )
        is not None
    )
    assert (
        cleared_write_stub_rejection(
            {"path": "a.md", "_landed_summary": "只读", "status": "landed"}
        )
        is not None
    )
    assert (
        cleared_write_stub_rejection(
            {"path": "a.md", "content": "hi", "_cleared": "legacy"}
        )
        is not None
    )
    # Compact landed-status echo under a write tool name.
    landed_err = cleared_write_stub_rejection(
        {"path": "a.md", "status": "landed", "via": "file_write", "chars": 100}
    )
    assert landed_err is not None
    assert "已落盘" in landed_err
    assert "不是可提交写参" in landed_err or "落盘" in landed_err
    # Normal short / prose must pass.
    assert cleared_write_stub_rejection({"path": "a.md", "content": "短文"}) is None
    assert (
        cleared_write_stub_rejection(
            {"path": "a.md", "content": "本节已清理历史遗留问题。"}
        )
        is None
    )
    assert (
        cleared_write_stub_rejection(
            {"path": "a.md", "old_string": "a", "new_string": "b"}
        )
        is None
    )


def test_landed_status_name_rejection_is_explicit():
    """仿调 `_write_landed` → 早拒文案点名「落盘状态不是工具」，非神秘 not_found。"""
    from agentcore.runtime.engine.write_args_clear import (
        LANDED_STATUS_TOOL,
        landed_status_name_rejection,
    )

    err = landed_status_name_rejection(LANDED_STATUS_TOOL)
    assert err is not None
    assert "_write_landed" in err
    assert "落盘" in err
    assert "不是可调用工具" in err
    assert "not_found" not in err.lower()
    assert "file_read" in err
    assert landed_status_name_rejection("file_write") is None
    assert landed_status_name_rejection("web_search") is None


def test_landed_summary_echo_fingerprint_collapses_per_path():
    """不同摘要文本同 path → 同 fingerprint；正常正文不塌缩。"""
    from agentcore.runtime.loop_controller import fingerprint_tool_call

    fp_a = fingerprint_tool_call(
        "file_write",
        json.dumps(
            {
                "path": "docs/a.md",
                "_landed_summary": "【已落盘摘要·只读】file_write 已成功写入 A",
                "status": "landed",
            },
            ensure_ascii=False,
        ),
    )
    fp_b = fingerprint_tool_call(
        "file_write",
        json.dumps(
            {
                "path": "docs\\a.md",
                "_landed_summary": "完全不同的摘要正文 B · 约 9000 字符",
                "status": "landed",
            },
            ensure_ascii=False,
        ),
    )
    fp_stub = fingerprint_tool_call(
        "file_write",
        json.dumps({"path": "docs/a.md", "content": "[已清理]"}, ensure_ascii=False),
    )
    fp_other = fingerprint_tool_call(
        "file_write",
        json.dumps(
            {
                "path": "docs/other.md",
                "_landed_summary": "【已落盘摘要·只读】file_write 已成功写入 A",
                "status": "landed",
            },
            ensure_ascii=False,
        ),
    )
    fp_ok = fingerprint_tool_call(
        "file_write",
        json.dumps(
            {"path": "docs/a.md", "content": "正常完整正文，不是摘要。"},
            ensure_ascii=False,
        ),
    )
    assert fp_a == fp_b
    assert fp_a == fp_stub
    assert fp_a != fp_other
    assert fp_a != fp_ok

    # Compact landed-status echo (no _landed_summary) also collapses per path.
    fp_status = fingerprint_tool_call(
        "file_write",
        json.dumps(
            {
                "path": "docs/a.md",
                "status": "landed",
                "via": "file_write",
                "chars": 1200,
            },
            ensure_ascii=False,
        ),
    )
    assert fp_status == fp_a

    fp_sr_a = fingerprint_tool_call(
        "str_replace",
        json.dumps(
            {
                "path": "docs/a.md",
                "_landed_summary": "摘要一",
                "status": "landed",
            },
            ensure_ascii=False,
        ),
    )
    fp_sr_b = fingerprint_tool_call(
        "str_replace",
        json.dumps(
            {
                "path": "docs/a.md",
                "old_string": "[已清理]",
                "new_string": "x",
            },
            ensure_ascii=False,
        ),
    )
    assert fp_sr_a == fp_sr_b


def test_landed_summary_echo_validation_stop_names_file_read():
    """摘要回灌：首次拒写即 path-stop（点名 file_read→str_replace/真文）；写工具保持可用。"""
    from agentcore.runtime.engine.write_args_clear import cleared_write_stub_rejection
    from agentcore.runtime.loop_controller import (
        LoopController,
        ToolAttempt,
        fingerprint_tool_call,
    )

    args = {
        "path": "docs/a.md",
        "_landed_summary": "【已落盘摘要·只读】不可当写盘参数",
        "status": "landed",
    }
    err = cleared_write_stub_rejection(args)
    assert err is not None
    assert "已落盘摘要" in err
    assert "docs/a.md" in err
    assert "file_read" in err
    assert "真文" in err
    assert "str_replace" in err
    fp = fingerprint_tool_call("file_write", json.dumps(args, ensure_ascii=False))

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    rej = ToolAttempt(
        fp,
        "file_write",
        success=False,
        contract_failure=True,
        error_summary=err,
        meta={"error_class": "validation", "path": "docs/a.md"},
    )
    # 首次即舵（不再等第二次同指纹）。
    c.record([rej])
    cb = c.tool_circuit_breaker()
    assert cb.validation_stop is not None
    stop = cb.validation_stop or ""
    assert "file_read" in stop
    assert "真文" in stop
    assert "str_replace" in stop
    assert "file_write" in stop
    assert "docs/a.md" in stop
    assert cb.disabled == ()
    assert c.tool_failure_count("file_write") == 0
    # 同指纹再撞 → thrash 早停。
    c.record([rej])
    assert c.is_thrashing() or c.take_validation_hard_stop()


def test_every_landed_rejection_is_recognized_by_early_stop():
    """安全网：三种遗留形态的拒绝文案都得被早停判定认出，改措辞不得静默丢掉一拍。"""
    from agentcore.runtime.engine.write_args_clear import (
        cleared_write_stub_rejection,
        is_landed_echo_rejection,
    )

    # 现投影已不再产出这些形态（参数槽只剩 path），它们仅作为回灌安全网留存。
    shapes = {
        "landed_status": {"path": "site/main.js", "status": "landed", "chars": 800},
        "legacy_summary": {"path": "docs/a.md", "_landed_summary": "【已落盘摘要·只读】"},
        "cleared_body": {"path": "docs/a.md", "content": "[已清理]"},
    }
    for label, args in shapes.items():
        err = cleared_write_stub_rejection(args)
        assert err is not None, label
        assert is_landed_echo_rejection(err), label


def test_landed_status_echo_gets_one_strike_stop():
    """遗留 landed 形态若仍被回灌 → 首拍即 path-stop，与旧摘要形态同待遇。"""
    from agentcore.runtime.engine.write_args_clear import cleared_write_stub_rejection
    from agentcore.runtime.loop_controller import (
        LoopController,
        ToolAttempt,
        fingerprint_tool_call,
    )

    args = {"path": "site/main.js", "status": "landed", "chars": 800}
    err = cleared_write_stub_rejection(args)
    assert err is not None
    assert "已落盘摘要" not in err  # 走的是 landed-status 分支，不是旧措辞

    c = LoopController(tool_failure_warn=2, tool_failure_disable=3)
    c.record(
        [
            ToolAttempt(
                fingerprint_tool_call("file_write", json.dumps(args, ensure_ascii=False)),
                "file_write",
                success=False,
                contract_failure=True,
                error_summary=err,
                meta={"error_class": "validation", "path": "site/main.js"},
            )
        ]
    )
    stop = c.tool_circuit_breaker().validation_stop or ""
    assert "file_read" in stop
    assert "site/main.js" in stop


# ── 3. 记忆复用 ──────────────────────────────────────────────────────────────


async def test_consult_reuses_turn_cache(tmp_path):
    store = FileMemoryStore(tmp_path)
    body = "## 审美\n- 简约商务\n"
    await store.save("u", topic_path("设计审美"), body)
    tool = ConsultTool(source=MergedConsultSource(memory=MemoryConsultSource(store=store)))
    token = consulted_memory_cache.set({})
    try:
        first = await tool.execute({"name": "设计审美"}, _ctx())
        assert first.success and first.output == body
        assert first.display["origin"] == "user"
        assert "kind" not in first.display
        assert "设计审美" in get_consult_cache()
        second = await tool.execute({"name": "设计审美"}, _ctx())
        assert second.success and second.output == body
        assert (second.display or {}).get("reused") is True
        assert (second.display or {}).get("origin") == "user"
        assert "kind" not in (second.display or {})
        # Pause 帧仍是 slug→正文；origin 不进 consulted_memory。
        assert dict(get_consult_cache()) == {"设计审美": body}
    finally:
        consulted_memory_cache.reset(token)


async def test_consult_reuse_from_frame_omits_origin(tmp_path):
    """Resume-from-frame only restores bodies; display must not invent origin."""
    store = FileMemoryStore(tmp_path)
    body = "## 审美\n- 简约商务\n"
    await store.save("u", topic_path("设计审美"), body)
    tool = ConsultTool(source=MergedConsultSource(memory=MemoryConsultSource(store=store)))
    token = consulted_memory_cache.set({"设计审美": body})
    try:
        result = await tool.execute({"name": "设计审美"}, _ctx())
        assert result.success and result.output == body
        assert (result.display or {}).get("reused") is True
        assert "origin" not in (result.display or {})
        assert "kind" not in (result.display or {})
    finally:
        consulted_memory_cache.reset(token)


def test_seed_consult_cache_from_window():
    token = consulted_memory_cache.set({})
    try:
        msgs = [
            LLMMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        function=ToolCallFunction(
                            name="consult",
                            arguments=json.dumps({"name": "设计审美"}),
                        ),
                    )
                ],
            ),
            LLMMessage(role="tool", content="审美正文", tool_call_id="c1"),
        ]
        assert seed_consult_cache_from_window(msgs) == 1
        assert get_consult_cache()["设计审美"] == "审美正文"
        remember_consult("其他", "x")
        assert seed_consult_cache_from_window(msgs) == 0  # already present
    finally:
        consulted_memory_cache.reset(token)
