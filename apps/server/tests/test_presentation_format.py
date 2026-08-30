"""Presentation delivery-format dual-gate + structured resume wire."""

from pathlib import Path

import pytest

from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnPausedFact, current_fact_log
from agentcore.runtime.runs.presentation_format import (
    DEFAULT_FORMAT_ID,
    clear_format_confirmation,
    ensure_full_auto_default_format,
    format_from_journal_entries,
    get_format_confirmation,
    record_format_confirmation,
    rehydrate_format_confirmation,
    resolve_format_from_resume,
    snapshot_presentation_format_for_pause,
)
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_resolve_format_from_resume_by_explicit_format_id():
    opts = [
        {"id": "f0", "label": "PowerPoint（.pptx）"},
        {"id": "f1", "label": "Marp Markdown 幻灯片"},
    ]
    conf = resolve_format_from_resume(
        opts, format_id="f1", note="· 形态：PowerPoint"
    )
    assert conf is not None
    assert conf.format_id == "f1"
    assert conf.label == "Marp Markdown 幻灯片"


def test_resolve_format_from_resume_by_selected_fn():
    opts = [
        {"id": "f0", "label": "PowerPoint（.pptx）"},
        {"id": "f1", "label": "Marp Markdown 幻灯片"},
    ]
    conf = resolve_format_from_resume(
        opts, selected=["受众", "f1"], note="就按这个方案开做："
    )
    assert conf is not None
    assert conf.format_id == "f1"


def test_resolve_format_from_resume_prose_alone_does_not_confirm():
    opts = [
        {"id": "f0", "label": "PowerPoint（.pptx）"},
        {"id": "f1", "label": "Marp Markdown 幻灯片"},
    ]
    conf = resolve_format_from_resume(
        opts, note="就按这个方案开做：\n· 形态：Marp Markdown 幻灯片\n"
    )
    assert conf is None


def test_resolve_format_from_resume_invalid_format_id_rejected():
    opts = [{"id": "f0", "label": "A"}, {"id": "f1", "label": "B"}]
    conf = resolve_format_from_resume(
        opts, format_id="f9", selected=["f0"], note="· 形态：A"
    )
    assert conf is None


def test_resolve_format_from_resume_selected_label_not_enough():
    opts = [
        {"id": "f0", "label": "PowerPoint（.pptx）"},
        {"id": "f1", "label": "Marp Markdown 幻灯片"},
    ]
    conf = resolve_format_from_resume(opts, selected=["Marp Markdown 幻灯片"])
    assert conf is None


def test_ledger_record_and_full_auto_default():
    cid = "test-format-ledger-cid"
    clear_format_confirmation(cid)
    assert get_format_confirmation(cid) is None
    ensure_full_auto_default_format(cid, prefer_pptx=False)
    conf = get_format_confirmation(cid)
    assert conf is not None
    assert conf.format_id == DEFAULT_FORMAT_ID
    assert conf.source == "full_auto_default"
    assert "Marp" in conf.label
    ensure_full_auto_default_format(cid, prefer_pptx=True)  # already set — no overwrite
    assert get_format_confirmation(cid).format_id == DEFAULT_FORMAT_ID
    clear_format_confirmation(cid)
    ensure_full_auto_default_format(cid, prefer_pptx=True)
    assert "PowerPoint" in get_format_confirmation(cid).label
    record_format_confirmation(cid, format_id="f0", label="X", source="ask_user")
    assert get_format_confirmation(cid).format_id == "f0"
    clear_format_confirmation(cid)


def _cache_miss(cid: str) -> bool:
    from agentcore.runtime.runs import presentation_format as pf

    with pf._lock:
        return cid not in pf._LEDGER


def test_record_persists_journal_fact_and_survives_memory_clear():
    """Acceptance: clear hot cache → rehydrate from journal → format still present."""
    cid = "test-format-persist-journal"
    clear_format_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_format_confirmation(
            cid, format_id="f1", label="Marp", source="ask_user"
        )
        entries = log.entries()
        assert any(
            e["kind"] == FactKind.PRESENTATION_FORMAT_CONFIRMED.value for e in entries
        )
        folded = format_from_journal_entries(entries)
        assert folded is not None
        assert folded.format_id == "f1"

        clear_format_confirmation(cid)
        assert _cache_miss(cid)

        conf = get_format_confirmation(cid)
        assert conf is not None
        assert conf.format_id == "f1"
        assert conf.label == "Marp"

        clear_format_confirmation(cid)
        current_fact_log.reset(token)
        token = None
        assert get_format_confirmation(cid) is None
        restored = rehydrate_format_confirmation(cid, entries=entries)
        assert restored is not None
        assert restored.format_id == "f1"
        assert get_format_confirmation(cid).format_id == "f1"
    finally:
        if token is not None:
            current_fact_log.reset(token)
        clear_format_confirmation(cid)


def test_rehydrate_from_turn_paused_format_after_memory_clear():
    """Acceptance: clear hot cache → rehydrate from turn_paused.presentation_format."""
    cid = "test-format-persist-paused"
    clear_format_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_format_confirmation(
            cid, format_id="f0", label="PowerPoint", source="ask_user"
        )
        snap = snapshot_presentation_format_for_pause(
            log.entries(), conversation_id=cid
        )
        assert snap == {
            "format_id": "f0",
            "label": "PowerPoint",
            "source": "ask_user",
        }
        paused = TurnPausedFact(
            checkpoint_id="cp",
            suspension_kind="ask_user",
            presentation_format=snap,
        )
    finally:
        current_fact_log.reset(token)

    clear_format_confirmation(cid)
    restored = rehydrate_format_confirmation(
        cid, turn_paused_format=paused.presentation_format
    )
    assert restored is not None
    assert restored.format_id == "f0"
    assert get_format_confirmation(cid).format_id == "f0"
    clear_format_confirmation(cid)


def test_no_persistent_format_means_gate_miss():
    """Acceptance: no journal/paused/cache → get returns None (delegate gate rejects)."""
    cid = "test-format-absent"
    clear_format_confirmation(cid)
    assert get_format_confirmation(cid) is None
    assert format_from_journal_entries([]) is None
    assert (
        rehydrate_format_confirmation(cid, entries=[], turn_paused_format=None) is None
    )


def _ask_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-format",
    )


def _ask_tool() -> AskUserTool:
    async def _save(_frame) -> None:
        return None

    async def _drop(_mid: str) -> None:
        return None

    return AskUserTool(
        sink=EventSink(),
        conversation_id="c-format",
        timeout_seconds=1.0,
        message_id="m1",
        suspension_saver=_save,
        suspension_deleter=_drop,
        captain_run_id="cap",
        base_system_prompt="sys",
        user_message="做一份 PPT",
    )


@pytest.mark.asyncio
async def test_ask_user_presentation_without_format_options_still_succeeds():
    """引擎不因文案像演讲而强制 format_options；缺省放行。"""
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="做一份产品发布 PPT")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：做一份产品发布 PPT 演示文稿",
                "questions": [
                    {
                        "prompt": "时长",
                        "options": ["10 分钟", "20 分钟"],
                        "default": "10 分钟",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


@pytest.mark.asyncio
async def test_ask_user_presentation_with_format_options_succeeds():
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="做一份产品发布 PPT")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：做一份产品发布 PPT 演示文稿",
                "format_options": [
                    {"label": "PowerPoint（.pptx）— 有 code_execute 时推荐"},
                    {"label": "Marp Markdown 幻灯片 — 无代码执行时推荐"},
                    {"label": "仅讲稿大纲"},
                ],
                "questions": [
                    {
                        "prompt": "时长",
                        "options": ["10 分钟", "20 分钟"],
                        "default": "10 分钟",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True
