"""委派前奏单测：硬拒 / 规范化，不经整条执行链。

`resolve_delegate_prelude` 从 `GraphTool.execute` 抽出后是纯函数（零 await、不写实例），
这里直接喂 `arguments` 断言这几道闸——不用 mock LLM / 调度 / 协作图。
"""

from __future__ import annotations

import agentcore.runtime.graph.admit.prelude as prelude_mod
from agentcore.core.errors import LLMAuthError
from agentcore.llm.turn_auth_dead import (
    bind_turn_auth_dead,
    mark_turn_auth_dead,
    reset_turn_auth_dead,
)
from agentcore.runtime.graph.admit.prelude import (
    DelegateBatchRequest,
    DelegatePreludeReject,
    resolve_delegate_prelude,
)
from agentcore.runtime.runs.constants import MAX_WORKER_SUBDELEGATIONS
from agentcore.runtime.turn.token_budget import (
    bind_turn_token_meter,
    record_turn_tokens,
    reset_turn_token_meter,
    resolve_turn_token_ceiling,
)
from agentcore.tools.registry import ToolRegistry
from tests.conftest import LogSpy


def run(arguments: dict, **over):
    """Call the prelude with test defaults (root captain, empty tool surface)."""
    kwargs = {
        "tools": ToolRegistry(),
        "depth": 0,
        "sub_workers_spawned": 0,
        "credential_source": "user",
    }
    kwargs.update(over)
    return resolve_delegate_prelude(arguments, **kwargs)


def accepted(arguments: dict, **over) -> DelegateBatchRequest:
    out = run(arguments, **over)
    assert isinstance(out, DelegateBatchRequest), getattr(out, "result", out)
    return out


def rejected(arguments: dict, **over) -> DelegatePreludeReject:
    out = run(arguments, **over)
    assert isinstance(out, DelegatePreludeReject)
    assert out.result.success is False
    return out


_ONE_NODE = [{"id": "n1", "goal": "做A"}]
_ONE_INTERNAL = [{"id": "n1", "goal": "做A", "depends_on": []}]


# ── 硬拒 ──────────────────────────────────────────────────────────────────────


def test_turn_token_ceiling_rejects_before_anything_else(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_token_meter(seed=0)
    try:
        record_turn_tokens(resolve_turn_token_ceiling() + 1)
        out = rejected({"nodes": _ONE_NODE})
    finally:
        reset_turn_token_meter(token)
    assert out.result.contract_failure is True
    assert spy.get("delegate.turn_token_ceiling_rejected")["ceiling"] == (
        resolve_turn_token_ceiling()
    )


def test_turn_auth_dead_rejects(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="user"))
        out = rejected({"nodes": _ONE_NODE})
    finally:
        reset_turn_auth_dead(token)
    assert out.result.contract_failure is True
    assert spy.get("delegate.turn_auth_dead_rejected") == {}


def test_turn_auth_dead_other_source_does_not_reject(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="platform"))
        accepted({"nodes": _ONE_NODE})
    finally:
        reset_turn_auth_dead(token)
    assert "delegate.turn_auth_dead_rejected" not in [n for n, _ in spy.events]


def test_empty_tasks_rejected(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    from agentcore.tools.builtin.graph.schema import (
        EMPTY_DELEGATE_MSG,
        HANDWRITTEN_TASKS_SKELETON,
    )

    out = rejected({})
    assert out.result.error == EMPTY_DELEGATE_MSG
    assert HANDWRITTEN_TASKS_SKELETON in (out.result.error or "")
    assert out.result.contract_failure is True
    assert spy.get("delegate.empty_rejected")["has_nodes"] is False

    empty_list = rejected({"nodes": []})
    assert empty_list.result.error == EMPTY_DELEGATE_MSG
    assert empty_list.result.contract_failure is True

    old_shape = rejected({"tasks": [{"role": "工程师", "task": "做A"}]})
    assert old_shape.result.error == EMPTY_DELEGATE_MSG
    assert old_shape.result.contract_failure is True


def test_unknown_top_level_keys_do_not_expand_or_xor():
    """未知顶层键不展开；没有 nodes 仍是缺 nodes。"""
    from agentcore.tools.builtin.graph.schema import EMPTY_DELEGATE_MSG

    out = rejected({"playbook": "cite_write_review"})
    assert out.result.error == EMPTY_DELEGATE_MSG

    batch = accepted({"playbook": "cite_write_review", "nodes": _ONE_NODE})
    assert batch.tasks_raw == _ONE_INTERNAL


def test_sub_fanout_cap_rejected_at_depth(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    nodes = [{"id": f"n{i}", "goal": f"t{i}"} for i in range(MAX_WORKER_SUBDELEGATIONS)]
    out = rejected(
        {"nodes": nodes},
        depth=1,
        sub_workers_spawned=1,
    )
    assert "子团队扇出已达上限" in (out.result.error or "")
    assert "分批" not in (out.result.error or "")
    # 扇出拒绝不是契约自纠打回（保持原样：不设 contract_failure）。
    assert out.result.contract_failure is False
    logged = spy.get("delegate.sub_fanout_rejected")
    assert logged["spawned"] == 1
    assert logged["requested"] == MAX_WORKER_SUBDELEGATIONS
    assert logged["cap"] == MAX_WORKER_SUBDELEGATIONS


def test_sub_fanout_within_cap_passes():
    out = accepted({"nodes": _ONE_NODE}, depth=1, sub_workers_spawned=1)
    assert len(out.tasks_raw) == 1


# ── 规范化 ────────────────────────────────────────────────────────────────────


def test_handwritten_tasks_normalized():
    tools = ToolRegistry()
    out = accepted({"nodes": _ONE_NODE}, tools=tools)
    assert out.tasks_raw == _ONE_INTERNAL
    assert out.valid_tools == {s.name for s in tools.list_all()}


def test_single_dependency_free_node_infers_light(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = accepted({"nodes": _ONE_NODE})
    assert out.complexity_hint == "light"
    assert spy.get("delegate.complexity_hint_inferred")["hint"] == "light"
    assert out.tasks_raw == _ONE_INTERNAL


def test_explicit_hint_is_never_auto_inferred():
    out = accepted({"nodes": _ONE_NODE, "complexity_hint": "standard"})
    assert out.complexity_hint == "standard"


def test_explicit_light_ignored_when_wave_boundary_present(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = accepted(
        {
            "nodes": [
                {"id": "s1", "goal": "调研"},
                {"id": "s2", "goal": "撰写", "depends_on": ["s1"]},
            ],
            "complexity_hint": "light",
        }
    )
    assert out.complexity_hint == "standard"
    assert out.tasks_raw[1]["id"] == "s2"
    assert out.tasks_raw[1]["goal"] == "撰写"
    assert "role" not in out.tasks_raw[1]
    assert "task" not in out.tasks_raw[1]
    assert out.tasks_raw[1]["depends_on"] == ["s1"]
    assert spy.get("delegate.complexity_hint_ignored")["reason"] == "wave_boundary_features"


def test_retired_item_keys_rejected():
    out = rejected(
        {
            "nodes": [
                {
                    "id": "n1",
                    "goal": "实现功能并落盘",
                    "deliverable": {"form": "files", "artifacts": ["src/main.py"]},
                }
            ]
        }
    )
    assert "只收 id、goal、depends_on" in (out.result.error or "")
    assert out.result.contract_failure is True


# ── 一次性软提示族已撤（同形入参仍接受，无告警字段、无事件）──────────────────


def test_former_soft_scan_shapes_accepted_without_warn_fields_or_events(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    goldbach = accepted(
        {
            "nodes": [
                {"id": "r1", "goal": "调研偶数哥德巴赫猜想相关文献"},
                {"id": "r2", "goal": "调研奇数哥德巴赫猜想相关文献"},
                {"id": "s", "goal": "基于前两个节点的结论，整理一份综述", "depends_on": ["r1", "r2"]},
            ]
        }
    )
    mixed = accepted({"nodes": [{"id": "fs", "goal": "新建桌面 AI 编程助手 MVP 骨架"}]})
    root_ws = accepted({"nodes": [{"id": "app", "goal": "从零实现应用 MVP"}]})
    nested = accepted(
        {"nodes": [{"id": "app", "goal": "从零实现应用 MVP"}]},
        depth=1,
    )
    for out in (goldbach, mixed, root_ws, nested):
        assert not hasattr(out, "consumer_deps_warn")
        assert not hasattr(out, "design_impl_warn")
        assert not hasattr(out, "root_slice_warn")
    names = [n for n, _ in spy.events]
    assert "delegate.consumer_deps_soft_warn" not in names
    assert "delegate.design_impl_same_grant_soft_warn" not in names
    assert "delegate.root_slice_honesty_soft_warn" not in names


def test_declared_depends_still_accepted():
    out = accepted(
        {
            "nodes": [
                {"id": "r1", "goal": "查资料"},
                {"id": "w1", "goal": "成文", "depends_on": ["r1"]},
            ]
        }
    )
    assert out.tasks_raw[1]["goal"] == "成文"
    assert out.tasks_raw[1]["depends_on"] == ["r1"]
    assert isinstance(out, DelegateBatchRequest)
    assert not hasattr(out, "consumer_deps_warn")
