"""Website style ledger + DESIGN helpers (场面 resume wire 已退役)."""

from pathlib import Path

import pytest

from agentcore.core.types import AutonomyPolicy
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import EventSink
from agentcore.runtime.facts import FactKind, TurnFactLog, TurnPausedFact, current_fact_log
from agentcore.runtime.runs.website_style import (
    DEFAULT_STYLE_ID,
    STYLE_ID_HEADING,
    StyleConfirmation,
    build_website_missing_style_error,
    clear_style_confirmation,
    design_prompt_block,
    ensure_full_auto_default_style,
    extract_style_id_from_design,
    get_style_confirmation,
    record_style_confirmation,
    rehydrate_style_confirmation,
    snapshot_website_style_for_pause,
    style_from_journal_entries,
)
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.ask_user import AskUserTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def test_ledger_record_and_full_auto_default():
    cid = "test-style-ledger-cid"
    clear_style_confirmation(cid)
    assert get_style_confirmation(cid) is None
    ensure_full_auto_default_style(cid)
    conf = get_style_confirmation(cid)
    assert conf is not None
    assert conf.style_id == DEFAULT_STYLE_ID
    assert conf.source == "full_auto_default"
    record_style_confirmation(cid, style_id="s0", label="X", source="ask_user")
    assert get_style_confirmation(cid).style_id == "s0"
    clear_style_confirmation(cid)


def _cache_miss(cid: str) -> bool:
    """True when hot cache is empty (bypass ambient-log fallback)."""
    from agentcore.runtime.runs import website_style as ws

    with ws._lock:
        return cid not in ws._LEDGER


def test_record_persists_journal_fact_and_survives_memory_clear():
    """Acceptance: clear hot cache → rehydrate from journal → style still present."""
    cid = "test-style-persist-journal"
    clear_style_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_style_confirmation(cid, style_id="s1", label="简约", source="ask_user")
        entries = log.entries()
        assert any(e["kind"] == FactKind.WEBSITE_STYLE_CONFIRMED.value for e in entries)
        folded = style_from_journal_entries(entries)
        assert folded is not None
        assert folded.style_id == "s1"

        clear_style_confirmation(cid)
        assert _cache_miss(cid)

        # Ambient fact log still bound → get_style_confirmation rehydrates.
        conf = get_style_confirmation(cid)
        assert conf is not None
        assert conf.style_id == "s1"
        assert conf.label == "简约"

        clear_style_confirmation(cid)
        # Explicit rehydrate from entries (simulates cold path without ambient log).
        current_fact_log.reset(token)
        token = None
        assert get_style_confirmation(cid) is None
        restored = rehydrate_style_confirmation(cid, entries=entries)
        assert restored is not None
        assert restored.style_id == "s1"
        assert get_style_confirmation(cid).style_id == "s1"
    finally:
        if token is not None:
            current_fact_log.reset(token)
        clear_style_confirmation(cid)


def test_rehydrate_from_turn_paused_style_after_memory_clear():
    """Acceptance: clear hot cache → rehydrate from turn_paused.website_style."""
    cid = "test-style-persist-paused"
    clear_style_confirmation(cid)
    log = TurnFactLog()
    token = current_fact_log.set(log)
    try:
        record_style_confirmation(cid, style_id="s0", label="深色", source="ask_user")
        snap = snapshot_website_style_for_pause(log.entries(), conversation_id=cid)
        assert snap == {"style_id": "s0", "label": "深色", "source": "ask_user"}
        paused = TurnPausedFact(
            checkpoint_id="cp",
            suspension_kind="ask_user",
            website_style=snap,
        )
    finally:
        current_fact_log.reset(token)

    # No ambient log: only turn_paused snapshot.
    clear_style_confirmation(cid)
    restored = rehydrate_style_confirmation(
        cid, turn_paused_style=paused.website_style
    )
    assert restored is not None
    assert restored.style_id == "s0"
    assert get_style_confirmation(cid).style_id == "s0"
    clear_style_confirmation(cid)


def test_no_persistent_style_means_gate_miss():
    """Acceptance: no journal/paused/cache → get returns None (style miss)."""
    cid = "test-style-absent"
    clear_style_confirmation(cid)
    assert get_style_confirmation(cid) is None
    assert style_from_journal_entries([]) is None
    assert rehydrate_style_confirmation(cid, entries=[], turn_paused_style=None) is None


def test_extract_style_id_from_design():
    text = f"# D\n\n## {STYLE_ID_HEADING}\ns0\n\n## Tokens\n#abc\n"
    assert extract_style_id_from_design(text) == "s0"
    assert extract_style_id_from_design("# no style") is None


def _ask_ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id="c-style",
    )


def _ask_tool() -> AskUserTool:
    async def _save(_frame) -> None:
        return None

    async def _drop(_mid: str) -> None:
        return None

    return AskUserTool(
        sink=EventSink(),
        conversation_id="c-style",
        timeout_seconds=1.0,
        message_id="m1",
        suspension_saver=_save,
        suspension_deleter=_drop,
        captain_run_id="cap",
        base_system_prompt="sys",
        user_message="做个官网",
    )


@pytest.mark.asyncio
async def test_ask_user_website_without_style_options_still_succeeds():
    """引擎不因文案像建站而强制 style_options；缺省放行。"""
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="为 GEO 做官网落地页")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：为 GEO 做官网落地页",
                "questions": [
                    {
                        "prompt": "受众",
                        "options": ["中小商家", "企业"],
                        "default": "中小商家",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


@pytest.mark.asyncio
async def test_ask_user_website_with_style_options_succeeds():
    tool = _ask_tool()
    token = captain_transcript.set(
        [LLMMessage(role="user", content="为 GEO 做官网落地页")]
    )
    try:
        res = await tool.execute(
            {
                "message": "开工：为 GEO 做官网落地页",
                "style_options": [{"label": "深色科技"}, {"label": "简约商务"}],
                "questions": [
                    {
                        "prompt": "受众",
                        "options": ["中小商家", "企业"],
                        "default": "中小商家",
                    }
                ],
            },
            _ask_ctx(),
        )
    finally:
        captain_transcript.reset(token)
    assert res.success is True


def test_build_website_missing_style_error_is_soft_default():
    err = build_website_missing_style_error()
    assert "build_website" not in err
    assert "开工提案" not in err
    assert "s_default" in err
    _ = AutonomyPolicy.MANAGED  # keyed exemption exists in product vocabulary
    assert "full_auto" in err.lower()


def test_design_prompt_block_default_injects_positive_recipe():
    """无确认 / s_default：软注入正向 DESIGN 配方；非默认选定风格不塞完整配方。"""
    none_block = design_prompt_block(style=None)
    assert DEFAULT_STYLE_ID in none_block
    assert "正向配方" in none_block
    assert "单一视觉焦点" in none_block
    assert "中性" in none_block
    assert "渐变" in none_block
    assert "glow" in none_block
    assert "粒子" in none_block
    assert "反馈" in none_block
    assert "#2563eb" not in none_block
    assert "工具台" not in none_block

    default_conf = StyleConfirmation(
        style_id=DEFAULT_STYLE_ID,
        label="简洁克制·高对比",
        source="full_auto_default",
    )
    default_block = design_prompt_block(style=default_conf)
    assert "正向配方" in default_block
    assert "单一视觉焦点" in default_block
    assert "#2563eb" not in default_block

    picked = StyleConfirmation(style_id="s0", label="深色科技", source="ask_user")
    picked_block = design_prompt_block(style=picked)
    assert "s0" in picked_block
    assert "正向配方" not in picked_block
    assert "单一视觉焦点" not in picked_block


def test_design_prompt_block_tool_domain_injects_toolshed_recipe():
    """domain=tool：软注入工具台配方（含 Tailwind 蓝禁令）；marketing 不含该禁。"""
    tool_block = design_prompt_block(style=None, domain="tool")
    assert "正向配方·工具台" in tool_block
    assert "工具" in tool_block
    assert "#2563eb" in tool_block
    assert "blue-600" in tool_block
    assert "中性 chrome" in tool_block
    assert "营销 hero" in tool_block
    assert "单一视觉焦点" not in tool_block

    default_conf = StyleConfirmation(
        style_id=DEFAULT_STYLE_ID,
        label="简洁克制·高对比",
        source="full_auto_default",
    )
    tool_default = design_prompt_block(style=default_conf, domain="tool")
    assert "#2563eb" in tool_default
    assert "正向配方·工具台" in tool_default

    picked = StyleConfirmation(style_id="s0", label="深色科技", source="ask_user")
    tool_picked = design_prompt_block(style=picked, domain="tool")
    assert "正向配方" not in tool_picked
    assert "#2563eb" not in tool_picked

    marketing = design_prompt_block(style=None, domain="marketing")
    assert "#2563eb" not in marketing
    assert "单一视觉焦点" in marketing
    assert "工具台" not in marketing
