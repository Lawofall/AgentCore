"""Agent/自动化开工形态双闸 + structured resume wire (mirror presentation_format)."""

from pathlib import Path

import pytest

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnPausedFact, current_fact_log
from agentcore.runtime.runs.automation_delivery import (
    DEFAULT_FORMAT_ID,
    classify_delivery_kind,
    clear_delivery_confirmation,
    delivery_from_journal_entries,
    ensure_full_auto_default_delivery,
    format_options_look_like_automation,
    get_delivery_confirmation,
    record_delivery_confirmation,
    rehydrate_delivery_confirmation,
    resolve_delivery_from_resume,
    snapshot_automation_delivery_for_pause,
)
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_format_options_look_like_automation():
    assert format_options_look_like_automation(
        [
            {"label": "可运行自动化 — 真实可调度"},
            {"label": "控制台原型 — 工具台 UI"},
            {"label": "仅方案"},
        ]
    )
    assert not format_options_look_like_automation(
        [
            {"label": "PowerPoint（.pptx）"},
            {"label": "Marp Markdown 幻灯片"},
            {"label": "仅讲稿大纲"},
        ]
    )


def test_resolve_delivery_from_resume_by_explicit_format_id():
    opts = [
        {"id": "f0", "label": "可运行自动化"},
        {"id": "f1", "label": "控制台原型"},
        {"id": "f2", "label": "仅方案"},
    ]
    conf = resolve_delivery_from_resume(opts, format_id="f1", note="· 形态：可运行")
    assert conf is not None
    assert conf.format_id == "f1"
    assert conf.label == "控制台原型"
    assert classify_delivery_kind(conf) == "console"


def test_resolve_delivery_from_resume_prose_alone_does_not_confirm():
    opts = [
        {"id": "f0", "label": "可运行自动化"},
        {"id": "f1", "label": "控制台原型"},
    ]
    conf = resolve_delivery_from_resume(
        opts, note="就按这个方案开做：\n· 形态：控制台原型\n"
    )
    assert conf is None


def test_ledger_record_and_full_auto_default():
    cid = "test-auto-delivery-ledger-cid"
    clear_delivery_confirmation(cid)
    assert get_delivery_confirmation(cid) is None
    ensure_full_auto_default_delivery(cid)
    conf = get_delivery_confirmation(cid)
    assert conf is not None
    assert conf.format_id == DEFAULT_FORMAT_ID
    assert conf.source == "full_auto_default"
    assert classify_delivery_kind(conf) == "runnable"
    ensure_full_auto_default_delivery(cid)  # already set — no overwrite
    assert get_delivery_confirmation(cid).format_id == DEFAULT_FORMAT_ID
    record_delivery_confirmation(cid, format_id="f0", label="仅方案", source="ask_user")
    assert classify_delivery_kind(get_delivery_confirmation(cid)) == "plan"
    clear_delivery_confirmation(cid)


def _cache_miss(cid: str) -> bool:
    from agentcore.runtime.runs import automation_delivery as ad

    with ad._lock:
        return cid not in ad._LEDGER


def test_record_persists_journal_fact_and_survives_memory_clear():
    cid = "test-auto-persist-journal"
    clear_delivery_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_delivery_confirmation(
            cid, format_id="f1", label="控制台原型", source="ask_user"
        )
        entries = log.entries()
        assert any(
            e["kind"] == FactKind.AUTOMATION_DELIVERY_CONFIRMED.value for e in entries
        )
        folded = delivery_from_journal_entries(entries)
        assert folded is not None
        assert folded.format_id == "f1"

        clear_delivery_confirmation(cid)
        assert _cache_miss(cid)

        conf = get_delivery_confirmation(cid)
        assert conf is not None
        assert conf.format_id == "f1"

        clear_delivery_confirmation(cid)
        current_fact_log.reset(token)
        token = None
        assert get_delivery_confirmation(cid) is None
        restored = rehydrate_delivery_confirmation(cid, entries=entries)
        assert restored is not None
        assert restored.format_id == "f1"
        assert get_delivery_confirmation(cid).format_id == "f1"
    finally:
        if token is not None:
            current_fact_log.reset(token)
        clear_delivery_confirmation(cid)


def test_rehydrate_from_turn_paused_delivery_after_memory_clear():
    cid = "test-auto-persist-paused"
    clear_delivery_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_delivery_confirmation(
            cid, format_id="f0", label="可运行自动化", source="ask_user"
        )
        snap = snapshot_automation_delivery_for_pause(
            log.entries(), conversation_id=cid
        )
        assert snap == {
            "format_id": "f0",
            "label": "可运行自动化",
            "source": "ask_user",
        }
        paused = TurnPausedFact(
            checkpoint_id="cp",
            suspension_kind="ask_user",
            automation_delivery=snap,
        )
    finally:
        current_fact_log.reset(token)

    clear_delivery_confirmation(cid)
    restored = rehydrate_delivery_confirmation(
        cid, turn_paused_delivery=paused.automation_delivery
    )
    assert restored is not None
    assert restored.format_id == "f0"
    assert get_delivery_confirmation(cid).format_id == "f0"
    clear_delivery_confirmation(cid)


def test_no_persistent_delivery_means_gate_miss():
    cid = "test-auto-absent"
    clear_delivery_confirmation(cid)
    assert get_delivery_confirmation(cid) is None
    assert delivery_from_journal_entries([]) is None
    assert (
        rehydrate_delivery_confirmation(cid, entries=[], turn_paused_delivery=None)
        is None
    )


def _ask_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-auto",
    )


def _ask_tool(*, conversation_id: str = "c-auto") -> AskUserTool:
    async def _save(_frame) -> None:
        return None

    async def _drop(_mid: str) -> None:
        return None

    return AskUserTool(
        sink=EventSink(),
        conversation_id=conversation_id,
        timeout_seconds=1.0,
        message_id="m1",
        suspension_saver=_save,
        suspension_deleter=_drop,
        captain_run_id="cap",
        base_system_prompt="sys",
        user_message="继续开发",
    )


@pytest.mark.asyncio
async def test_ask_user_whiteboard_continue_with_agentcore_context_ok():
    """白板继续开发 + message 含 AgentCore：不因正文像自动化而拒 ask_user。"""
    tool = _ask_tool(conversation_id="c-auto-wb")
    token = captain_transcript.set(
        [LLMMessage(role="user", content="继续完成白板的开发")]
    )
    try:
        res = await tool.execute(
            {
                "message": (
                    "继续完成白板的开发\n工作区：AgentCore 桌面端协作白板模块"
                ),
                "assumptions": [{"label": "范围", "value": "沿用现有栈"}],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


@pytest.mark.asyncio
async def test_ask_user_automation_without_format_options_still_succeeds():
    """引擎不因文案像自动化而强制 format_options；缺省放行。"""
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="做短视频自动化 Agent")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：做短视频自动化 Agent",
                "questions": [
                    {
                        "prompt": "平台",
                        "options": ["抖音", "小红书"],
                        "default": "抖音",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


@pytest.mark.asyncio
async def test_ask_user_automation_with_format_options_succeeds():
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="做短视频自动化 Agent")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：做短视频自动化 Agent",
                "format_options": [
                    {"label": "可运行自动化 — 真实可调度"},
                    {"label": "控制台原型 — 工具台 UI"},
                    {"label": "仅方案"},
                ],
                "questions": [
                    {
                        "prompt": "平台",
                        "options": ["抖音", "小红书"],
                        "default": "抖音",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


@pytest.mark.asyncio
async def test_ask_user_research_no_forced_format_gate():
    tool = _ask_tool(conversation_id="c-auto-fp")
    token = captain_transcript.set(
        [LLMMessage(role="user", content="用团队做竞品调研")]
    )
    try:
        res = await tool.execute(
            {
                "message": "用团队做竞品调研",
                "assumptions": [{"label": "范围", "value": "三家主流"}],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True
