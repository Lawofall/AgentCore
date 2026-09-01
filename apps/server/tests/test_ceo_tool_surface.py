"""CEO tool-surface gating: idle vs coordination (工具面瘦身).

拍板分态：闲聊态 = delegate + ask_user + debate 常驻（debate 与 delegate 同级，
闲聊可开辩）；replan + 协调四件套仅协调态 / 受监督让出时注入（与执行闸对齐）。
"""

from __future__ import annotations

from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.runtime.coordination.session import (
    CoordinationSession,
    clear_active_coordination,
    current_execution_id,
    set_active_coordination,
)
from agentcore.runtime.resolve.ceo_surface import (
    COORDINATION_GATED_TOOLS,
    COORDINATION_PERIOD_HINT,
    coordination_surface_active,
    promote_coordination_surface_if_needed,
    register_coordination_surface,
)
from agentcore.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _clear_coord():
    clear_active_coordination()
    token = current_execution_id.set(None)
    yield
    clear_active_coordination()
    current_execution_id.reset(token)


def _fake_delegate(*, supervised: bool = False, depth: int = 0):
    d = MagicMock()
    d.schema = MagicMock(name="delegate")
    d.schema.name = "delegate"
    d._sink = MagicMock()
    d._supervised = object() if supervised else None
    # depth≥1 = 队员自带的嵌套 delegate 句柄（spawn_lead_subteam）；协调套件不归它。
    d._depth = depth
    return d


def _activate_coordination(eid: str) -> None:
    set_active_coordination(
        CoordinationSession(
            execution_id=eid,
            total_workers=2,
            conversation_id="c1",
        )
    )


def test_solo_worker_enters_coordination_surface():
    """验收：total_workers=1 的活跃 session 也注入 cancel_worker 等协调工具面。"""
    eid = "exec-solo-coord"
    token = current_execution_id.set(eid)
    try:
        set_active_coordination(
            CoordinationSession(
                execution_id=eid,
                total_workers=1,
                conversation_id="c1",
            )
        )
        assert coordination_surface_active(execution_id=eid)
        reg = ToolRegistry()
        delegate = _fake_delegate()
        reg.register(delegate)
        register_coordination_surface(
            reg,
            delegate_tool=delegate,
            sink=MagicMock(),
            include=True,
        )
        assert "cancel_worker" in reg.names
        assert "resolve_escalation" in reg.names
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_idle_surface_omits_gated_tools():
    reg = ToolRegistry()
    delegate = _fake_delegate()
    reg.register(delegate)
    register_coordination_surface(
        reg,
        delegate_tool=delegate,
        sink=MagicMock(),
        include=False,
    )
    names = set(reg.names)
    assert "delegate" in names
    assert names.isdisjoint(COORDINATION_GATED_TOOLS)


def test_coordination_surface_includes_gated_tools():
    eid = "exec-coord-surface"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        assert coordination_surface_active(execution_id=eid)

        reg = ToolRegistry()
        delegate = _fake_delegate()
        reg.register(delegate)
        register_coordination_surface(
            reg,
            delegate_tool=delegate,
            sink=MagicMock(),
            include=True,
        )
        names = set(reg.names)
        assert "delegate" in names
        assert names >= COORDINATION_GATED_TOOLS
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_promote_on_supervised_yield_adds_replan_only():
    reg = ToolRegistry()
    delegate = _fake_delegate(supervised=True)
    reg.register(delegate)

    assert promote_coordination_surface_if_needed(reg) is True
    names = set(reg.names)
    assert "replan" in names
    assert "delegate" in names
    # No live coordination → coord suite stays out
    assert "update_synthesis" not in names


def test_promote_on_coordination_adds_full_surface():
    eid = "exec-promote"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        reg = ToolRegistry()
        delegate = _fake_delegate()
        reg.register(delegate)

        assert promote_coordination_surface_if_needed(reg) is True
        assert set(reg.names) >= COORDINATION_GATED_TOOLS
        assert "delegate" in reg.names
        # Idempotent
        assert promote_coordination_surface_if_needed(reg) is False
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_ensure_before_llm_installs_wait_when_coordination_live():
    """验收钉：协调已活 → 进入 LLM 前 wait 已在工具面（prepare / mid-turn 对齐）。"""
    from agentcore.runtime.resolve.ceo_surface import ensure_coordination_surface_before_llm

    eid = "exec-ensure-before-llm"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        reg = ToolRegistry()
        delegate = _fake_delegate()
        reg.register(delegate)
        assert "wait" not in reg.names

        assert ensure_coordination_surface_before_llm(reg) is True
        assert "wait" in reg.names
        assert set(reg.names) >= COORDINATION_GATED_TOOLS
        # 再次调用不重复注册
        assert ensure_coordination_surface_before_llm(reg) is False
        assert "wait" in reg.names
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_member_never_gets_coordination_suite_from_parent_session():
    """回归钉：队员不得因父图协调活跃而拿到 CEO 协调工具面。

    队员 allowed_tools=None（不限名单），注册即被 offer——真实日志里 depth=1 的
    审计员拿到了 wait / cancel_worker / resolve_escalation。
    """
    eid = "exec-parent-graph"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        reg = ToolRegistry()
        # 队员的嵌套 delegate 句柄：depth≥1，且与父图共享 execution_id
        reg.register(_fake_delegate(depth=1))

        assert promote_coordination_surface_if_needed(reg) is False
        assert set(reg.names).isdisjoint(COORDINATION_GATED_TOOLS)
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_nested_lead_still_gets_replan_on_supervised_yield():
    """嵌套 lead 合法需要 replan：收窄协调套件不得连它一起掐掉。"""
    reg = ToolRegistry()
    reg.register(_fake_delegate(supervised=True, depth=1))

    assert promote_coordination_surface_if_needed(reg) is True
    assert "replan" in reg.names
    # 但父图的协调四件套仍然不归它
    assert "wait" not in reg.names
    assert "cancel_worker" not in reg.names


def test_nested_lead_opening_omits_replan_until_supervised():
    """开场只有 delegate；子计划让出后才挂 replan（与 CEO 闲聊/协调同构）。"""
    reg = ToolRegistry()
    reg.register(_fake_delegate(supervised=False, depth=1))
    assert promote_coordination_surface_if_needed(reg) is False
    assert "replan" not in reg.names

    delegate = reg.get("delegate")
    delegate._supervised = object()
    assert promote_coordination_surface_if_needed(reg) is True
    assert "replan" in reg.names
    assert "wait" not in reg.names


def test_resync_binding_follows_hot_graph_merge():
    """回归钉：合入热图后 CEO 必须重新绑到宿主图，否则不等待也拿不到 wait。

    delegate 在 asyncio.gather 子任务里改 ContextVar，父任务读不到；宿主 eid 只
    落在共享 _base_tool_context 上。回绑前工具面判空（复现「CEO 用正文收口、把在跑
    的队员甩成 detached」），回绑后协调四件套装上。

    跨回合 append 进【已收口】的图现在改走「新图 + prev_execution_id」不再改绑；
    同回合合入热图仍走 tool.py 的改绑，本钉照旧有效。跨回合 live 图的 adopt 绑定
    不得被尚无 session 的本回合 mint 冲掉——见
    ``test_resync_binding_preserves_adopted_live_when_mint_has_no_session``。
    """
    from agentcore.runtime.resolve.ceo_surface import resync_coordination_binding

    minted, host = "exec-minted-this-turn", "exec-host-graph"
    token = current_execution_id.set(minted)
    try:
        # 真实复现而非模拟：会话在 copy_context 里注册，其 current_execution_id.set
        # 停在副本内（等价于 delegate 跑在 asyncio.gather 子任务），父任务仍是 mint。
        copy_context().run(_activate_coordination, host)
        assert current_execution_id.get() == minted

        reg = ToolRegistry()
        delegate = _fake_delegate()
        delegate._base_tool_context = SimpleNamespace(execution_id=host)
        reg.register(delegate)

        # 回绑前：父任务仍指向本回合 mint 的 eid → 找不到宿主会话
        assert coordination_surface_active() is False
        assert promote_coordination_surface_if_needed(reg) is False
        assert "wait" not in reg.names

        assert resync_coordination_binding(reg) is True
        assert current_execution_id.get() == host
        assert coordination_surface_active() is True
        assert promote_coordination_surface_if_needed(reg) is True
        assert set(reg.names) >= COORDINATION_GATED_TOOLS
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_resync_binding_preserves_adopted_live_when_mint_has_no_session():
    """跨回合 adopt：本回合 mint 尚无图时，resync 不得把 ContextVar 从 live 改回 mint。"""
    from agentcore.runtime.resolve.ceo_surface import resync_coordination_binding

    minted, live = "exec-minted-this-turn", "exec-live-adopted"
    token = current_execution_id.set(minted)
    try:
        _activate_coordination(live)
        current_execution_id.set(live)
        assert current_execution_id.get() == live

        reg = ToolRegistry()
        delegate = _fake_delegate()
        delegate._base_tool_context = SimpleNamespace(execution_id=minted)
        reg.register(delegate)

        assert coordination_surface_active() is True
        assert resync_coordination_binding(reg) is False
        assert current_execution_id.get() == live
        assert coordination_surface_active() is True
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_resync_binding_noop_when_already_bound():
    """同回合首次 delegate（未 append）：绑定未动，不应重复回绑/ 刷日志。"""
    from agentcore.runtime.resolve.ceo_surface import resync_coordination_binding

    eid = "exec-same-turn"
    token = current_execution_id.set(eid)
    try:
        reg = ToolRegistry()
        delegate = _fake_delegate()
        delegate._base_tool_context = SimpleNamespace(execution_id=eid)
        reg.register(delegate)

        assert resync_coordination_binding(reg) is False
        assert current_execution_id.get() == eid
    finally:
        current_execution_id.reset(token)


@pytest.mark.parametrize(
    "delegate_factory",
    [
        pytest.param(None, id="no_delegate"),
        pytest.param(lambda: SimpleNamespace(execution_id=None), id="ctx_without_eid"),
        pytest.param(lambda: None, id="no_base_context"),
    ],
)
def test_resync_binding_leaves_binding_alone_without_a_host(delegate_factory):
    """裸装配 / 无宿主 eid：不得把绑定改成空或垃圾值。"""
    from agentcore.runtime.resolve.ceo_surface import resync_coordination_binding

    token = current_execution_id.set("exec-untouched")
    try:
        reg = ToolRegistry()
        if delegate_factory is not None:
            delegate = _fake_delegate()
            delegate._base_tool_context = delegate_factory()
            reg.register(delegate)

        assert resync_coordination_binding(reg) is False
        assert current_execution_id.get() == "exec-untouched"
    finally:
        current_execution_id.reset(token)


def test_assembled_coordination_live_offers_wait_on_tool_defs():
    """协调已活时 assemble 路径的 OpenAI 工具面含 wait（与 ensure 同一验收）。"""
    eid = "exec-assembly"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        reg = _assemble()
        assert "wait" in reg.names
        defs = reg.get_openai_definitions()
        names = {
            (d.get("function") or {}).get("name") or d.get("name") for d in defs
        }
        assert "wait" in names
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_always_on_tools_not_in_gated_set():
    """delegate / ask_user / debate 常驻——不得进协调闸集合。"""
    for name in ("delegate", "ask_user", "debate", "consult"):
        assert name not in COORDINATION_GATED_TOOLS


def test_coordination_period_hint_posture_not_tool_manual():
    assert "【协调期】" in COORDINATION_PERIOD_HINT
    assert "可静默" in COORDINATION_PERIOD_HINT
    assert "请示" in COORDINATION_PERIOD_HINT
    assert "阻塞" in COORDINATION_PERIOD_HINT
    assert "阶段结论" in COORDINATION_PERIOD_HINT
    assert "三选一" not in COORDINATION_PERIOD_HINT
    assert "ceiling" not in COORDINATION_PERIOD_HINT
    assert "max_rounds" not in COORDINATION_PERIOD_HINT
    assert "同质 wait" not in COORDINATION_PERIOD_HINT
    assert "cancel_worker" not in COORDINATION_PERIOD_HINT
    assert "update_synthesis" not in COORDINATION_PERIOD_HINT
    assert "force" not in COORDINATION_PERIOD_HINT
    assert "移除" not in COORDINATION_PERIOD_HINT
    assert "不可用" not in COORDINATION_PERIOD_HINT
    assert "短说谁在后台" not in COORDINATION_PERIOD_HINT
    assert "谁在后台、完成后会再汇报" not in COORDINATION_PERIOD_HINT
    assert "人已派出" not in COORDINATION_PERIOD_HINT  # 派完收束在 host/core，不在协调期 hint


def test_wait_in_gated_set():
    assert "wait" in COORDINATION_GATED_TOOLS
    assert "wait" not in ("delegate", "ask_user", "debate")


# --- assembly-level 分态（真实 _assemble_ceo_toolset） -----------------------


def _ctx(*, vision_reader=None):
    from pathlib import Path

    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    # Assembly never touches the backend; a real one only satisfies the shape.
    return ToolContext.create(
        execution_id="exec-assembly",
        run_id="r",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        vision_reader=vision_reader,
    )


def _assemble(
    *,
    checkpoint_enabled: bool = True,
    vision_reader=None,
    model: str | None = None,
) -> ToolRegistry:
    from agentcore.llm.profiles import default_turn_profiles
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.resolve.prepare import _assemble_ceo_toolset
    from agentcore.runtime.skills import build_system_skill_registry

    _, _, chat_tools = _assemble_ceo_toolset(
        llm=object(),
        sink=EventSink(),
        base_system_prompt="SYS",
        user_message="原始请求",
        history=[],
        worker_tools=ToolRegistry(),
        base_tool_context=_ctx(vision_reader=vision_reader),
        profiles=default_turn_profiles(model=model),
        approval_gate=None,
        session_store=None,
        session_saver=None,
        session_loader=None,
        conversation_id="c",
        captain_run_id="cap",
        checkpoint_enabled=checkpoint_enabled,
        message_id="m",
        suspension_saver=None,
        suspension_deleter=None,
        backend_location="cloud",
        skill_registry=build_system_skill_registry(),
    )
    return chat_tools


def test_assembled_idle_surface_split():
    """闲聊态：delegate / ask_user / debate 在；replan + 协调四件套不在。

    ``consult`` is has_entries-gated via async ``wire_ceo_consult`` (not in sync assemble).
    """
    names = set(_assemble().names)
    assert {"delegate", "ask_user", "debate"} <= names
    assert names.isdisjoint(COORDINATION_GATED_TOOLS)


def test_assembled_ceo_holds_deferred_desktop_notify_not_escalate():
    """Notify is extra-registered on CEO (on-demand); escalate/handoff stay worker-only."""
    reg = _assemble()
    assert "desktop_notify" in reg.names
    assert "desktop_notify" in reg.deferred_names
    assert "escalate" not in reg.names
    assert "handoff" not in reg.names
    offered = {
        (d.get("function") or {}).get("name") or d.get("name")
        for d in reg.get_openai_definitions()
    }
    assert "desktop_notify" not in offered


def test_assembled_offers_create_folder():
    """跨文件夹 P1：create_folder 须进 live CEO 装配（勿只挂 catalog / 漏 prepare.register）。"""
    names = set(_assemble().names)
    assert {
        "list_folders",
        "resolve_folder",
        "create_folder",
        "list_folder_dir",
        "read_folder_file",
    } <= names


def test_register_always_ceo_tools_declare_loop():
    """G3：零参/轻参 ALWAYS 走声明循环；delegate/debate/consult 不进该 helper。"""
    from agentcore.runtime.skills import build_system_skill_registry
    from agentcore.tools.registration import register_always_ceo_tools

    reg = ToolRegistry()
    register_always_ceo_tools(reg, skill_registry=build_system_skill_registry())
    names = set(reg.names)
    assert {
        "list_folders",
        "resolve_folder",
        "create_folder",
        "list_folder_dir",
        "read_folder_file",
        "read_image",
    } <= names
    assert "consult" not in names  # CeoWire.CONSULT — hand-wired with has_entries
    assert names.isdisjoint({"delegate", "debate", "ask_user", "remember", "wait"})


def test_assembled_omits_read_image_when_vision_unconfigured():
    """未配 VisionReader 且主模型非原生多模态 → 不把 read_image 装进 CEO 工具面。"""
    names = set(_assemble().names)
    assert "read_image" not in names


def test_assembled_offers_read_image_when_vision_reader():
    """vision 槽已解析出 VisionReader → read_image 仍在 live CEO 装配。"""
    names = set(_assemble(vision_reader=object()).names)
    assert "read_image" in names


def test_assembled_offers_read_image_when_main_native_vision():
    """主模型厂商契约收图、无 VisionReader → 仍装配（同一能力位）。"""
    names = set(_assemble(model="gpt-4o").names)
    assert "read_image" in names


def test_assembled_coordination_surface_split():
    """协调态：闸内工具齐全；常驻工具（含 delegate）照旧在。"""
    eid = "exec-assembly"
    token = current_execution_id.set(eid)
    try:
        _activate_coordination(eid)
        names = set(_assemble().names)
        assert {"delegate", "ask_user", "debate"} <= names
        assert names >= COORDINATION_GATED_TOOLS
    finally:
        clear_active_coordination()
        current_execution_id.reset(token)


def test_debate_and_review_listed_in_idle_directory():
    """debate 常驻 ⇒ debate_and_review（requires_tools=debate）闲聊态回到按需目录。"""
    from agentcore.runtime.skills import build_system_skill_registry, render_skill_directory

    idle_names = set(_assemble().names)
    directory = render_skill_directory(build_system_skill_registry(), idle_names)
    assert "debate_and_review" in directory


# --- COST-004 tools 面 token 口径 --------------------------------------------


def _tools_offered_line(monkeypatch, defs: list[dict]) -> dict:
    """The single ``cost.tools_offered`` line ``observe_tools_offered`` emits for ``defs``."""
    from agentcore.runtime.resolve import ceo_surface

    captured: list[dict] = []

    class _Spy:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append({"event": event, **kwargs})

    monkeypatch.setattr(ceo_surface, "logger", _Spy())
    ceo_surface.observe_tools_offered(ToolRegistry(), scope="unit", tool_defs=defs)
    return captured[0]


def test_a_chinese_schema_no_longer_reads_as_a_quarter_of_its_chars(monkeypatch):
    """中文正文约 1 汉字 = 1 token；旧的 ``chars // 4`` 把整个工具面算低了三四倍。"""
    prose = "把任务派给队员并给出验收标准。" * 50
    line = _tools_offered_line(
        monkeypatch, [{"function": {"name": "delegate", "description": prose}}]
    )
    assert line["cjk_chars"] == len(prose)  # 汉字 + 中文句号都算 CJK
    assert line["approx_tokens_low"] > line["total_chars"] // 4  # 旧口径
    # 「1 汉字 1 token」这一最坏情形必须落在带内，否则带本身仍在低估。
    assert line["approx_tokens_low"] <= len(prose) <= line["approx_tokens_high"]
    assert "approx_tokens" not in line  # 不留已知偏低的单值


def test_an_ascii_schema_band_brackets_the_classic_four_chars_per_token(monkeypatch):
    line = _tools_offered_line(
        monkeypatch,
        [{"function": {"name": "file_read", "description": "Read a file and return its text."}}],
    )
    assert line["cjk_chars"] == 0
    assert line["approx_tokens_low"] <= line["total_chars"] // 4 <= line["approx_tokens_high"]


def test_an_empty_tool_surface_keeps_the_same_fields(monkeypatch):
    line = _tools_offered_line(monkeypatch, [])
    assert line["tool_count"] == 0
    assert line["total_chars"] == 0 and line["cjk_chars"] == 0
    assert line["approx_tokens_low"] == 0 and line["approx_tokens_high"] == 0


def test_the_real_ceo_surface_is_chinese_enough_to_have_been_underreported(monkeypatch):
    """真实装配面：schema 正文几乎全中文，正是旧口径系统性偏低的来源。"""
    line = _tools_offered_line(monkeypatch, _assemble().get_openai_definitions())
    assert line["cjk_chars"] > 0
    assert line["approx_tokens_low"] > line["total_chars"] // 4
    assert line["per_tool"] and sum(line["per_tool"].values()) == line["total_chars"]
