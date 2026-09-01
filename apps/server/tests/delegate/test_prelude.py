"""委派前奏单测：硬拒 / 规范化，不经整条执行链。

`resolve_delegate_prelude` 从 `DelegateTool.execute` 抽出后是纯函数（零 await、不写实例），
这里直接喂 `arguments` 断言这几道闸——不用 mock LLM / 调度 / 协作图。
"""

from __future__ import annotations

import agentcore.runtime.delegate.prelude as prelude_mod
from agentcore.core.errors import LLMAuthError
from agentcore.llm.turn_auth_dead import (
    bind_turn_auth_dead,
    mark_turn_auth_dead,
    reset_turn_auth_dead,
)
from agentcore.runtime.delegate.prelude import (
    DelegateBatchRequest,
    DelegateCallFlags,
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
        "user_message": "原始请求",
        "conversation_id": "conv-1",
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


_ONE_TASK = [{"role": "工程师", "task": "做A"}]


# ── 硬拒 ──────────────────────────────────────────────────────────────────────


def test_turn_token_ceiling_rejects_before_anything_else(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_token_meter(seed=0)
    try:
        record_turn_tokens(resolve_turn_token_ceiling() + 1)
        out = rejected({"tasks": _ONE_TASK})
    finally:
        reset_turn_token_meter(token)
    assert out.result.contract_failure is True
    # 硬顶发生在读 playbook 之前 → 实例上的 per-call 标记保持原样。
    assert out.flags is None
    assert spy.get("delegate.turn_token_ceiling_rejected")["ceiling"] == (
        resolve_turn_token_ceiling()
    )


def test_turn_auth_dead_rejects(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="user"))
        out = rejected({"tasks": _ONE_TASK})
    finally:
        reset_turn_auth_dead(token)
    assert out.result.contract_failure is True
    assert out.flags is None
    assert spy.get("delegate.turn_auth_dead_rejected") == {}


def test_turn_auth_dead_other_source_does_not_reject(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    token = bind_turn_auth_dead()
    try:
        mark_turn_auth_dead(LLMAuthError(provider_name="platform"))
        accepted({"tasks": _ONE_TASK})
    finally:
        reset_turn_auth_dead(token)
    assert "delegate.turn_auth_dead_rejected" not in [n for n, _ in spy.events]


def test_empty_declaration_rejected_with_gate(monkeypatch):
    """既无 tasks 又无 playbook → 声明闸打回，日志带 gate 分类。"""
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = rejected({})
    assert "delegate 缺 tasks/playbook" in (out.result.error or "")
    assert out.result.contract_failure is True
    assert out.flags is None
    assert spy.get("delegate.playbook_declaration_rejected")["gate"] == "empty"


def test_unknown_playbook_rejected(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = rejected({"playbook": "nope"})
    assert "未知 playbook" in (out.result.error or "")
    assert spy.get("delegate.playbook_declaration_rejected")["gate"] == "unknown"


def test_playbook_xor_tasks_defense_in_depth():
    """`tasks` 非 list（声明闸看不见）但真值 → 前奏兜住 XOR，不半跑。"""
    out = rejected({"playbook": "cite_write_review", "tasks": "写点东西"})
    assert "二选一" in (out.result.error or "")
    assert out.result.contract_failure is True
    # XOR 发生在写 _active_playbook 之前。
    assert out.flags is None


def test_playbook_expand_errors_rejected(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = rejected({"playbook": "cite_write_review"})
    assert (out.result.error or "").startswith("playbook 实例化失败：")
    assert out.result.contract_failure is True
    assert out.flags is None
    assert spy.get("delegate.playbook_rejected")["playbook"] == "cite_write_review"


def test_empty_tasks_rejected_and_clears_playbook_marks():
    out = rejected({"tasks": []})
    assert "缺 tasks/playbook" in (out.result.error or "")
    assert out.result.contract_failure is True
    # 声明闸 empty：flags 尚未写入。
    assert out.flags is None


def test_sub_fanout_cap_rejected_at_depth(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    tasks = [{"role": f"r{i}", "task": f"t{i}"} for i in range(MAX_WORKER_SUBDELEGATIONS)]
    out = rejected(
        {"tasks": tasks},
        depth=1,
        sub_workers_spawned=1,
    )
    assert "子团队扇出已达上限" in (out.result.error or "")
    # 扇出拒绝不是契约自纠打回（保持原样：不设 contract_failure）。
    assert out.result.contract_failure is False
    assert out.flags == DelegateCallFlags(playbook=None, playbook_args=None)
    logged = spy.get("delegate.sub_fanout_rejected")
    assert logged["spawned"] == 1
    assert logged["requested"] == MAX_WORKER_SUBDELEGATIONS
    assert logged["cap"] == MAX_WORKER_SUBDELEGATIONS


def test_sub_fanout_within_cap_passes():
    out = accepted({"tasks": _ONE_TASK}, depth=1, sub_workers_spawned=1)
    assert len(out.tasks_raw) == 1


# ── 规范化 ────────────────────────────────────────────────────────────────────


def test_handwritten_tasks_normalized():
    tools = ToolRegistry()
    out = accepted({"tasks": _ONE_TASK}, tools=tools)
    assert out.tasks_raw == _ONE_TASK
    assert out.playbook is None
    assert out.playbook_notes == []
    assert out.valid_tools == {s.name for s in tools.list_all()}
    assert out.flags == DelegateCallFlags(playbook=None, playbook_args=None)


def test_playbook_expands_tasks_and_carries_marks(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    args = {"topic": "GEO 官网"}
    out = accepted({"playbook": "cite_write_review", "playbook_args": args})
    assert out.playbook == "cite_write_review"
    assert len(out.tasks_raw) > 1
    assert out.flags.playbook == "cite_write_review"
    assert out.flags.playbook_args == args
    # 拷贝而非引用：CEO 传进来的 dict 不该被下游改写。
    assert out.flags.playbook_args is not args
    assert spy.get("delegate.playbook")["nodes"] == len(out.tasks_raw)


def test_single_dependency_free_worker_infers_light(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = accepted(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "做A",
                    "deliverable": {"form": "prose"},
                }
            ]
        }
    )
    assert out.complexity_hint == "light"
    assert spy.get("delegate.complexity_hint_inferred")["hint"] == "light"


def test_omitted_form_single_worker_stays_standard():
    out = accepted({"tasks": _ONE_TASK})
    assert out.complexity_hint == "standard"


def test_explicit_hint_is_never_auto_inferred():
    out = accepted({"tasks": _ONE_TASK, "complexity_hint": "standard"})
    assert out.complexity_hint == "standard"


def test_explicit_light_ignored_when_wave_boundary_present(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    out = accepted(
        {
            "tasks": [
                {"id": "s1", "role": "研究员", "task": "调研"},
                {"id": "s2", "role": "写手", "task": "撰写", "depends_on": ["s1"]},
            ],
            "complexity_hint": "light",
        }
    )
    assert out.complexity_hint == "standard"
    assert spy.get("delegate.complexity_hint_ignored")["reason"] == "wave_boundary_features"


def test_deep_deliverable_single_worker_stays_standard():
    out = accepted(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "实现功能并落盘",
                    "deliverable": {"form": "files", "artifacts": ["src/main.py"]},
                }
            ]
        }
    )
    assert out.complexity_hint == "standard"


# ── 一次性软提示族已撤（同形入参仍接受，无告警字段、无事件）──────────────────


def test_former_soft_scan_shapes_accepted_without_warn_fields_or_events(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(prelude_mod, "logger", spy)
    goldbach = accepted(
        {
            "tasks": [
                {"id": "r1", "role": "调研甲", "task": "调研偶数哥德巴赫猜想相关文献"},
                {"id": "r2", "role": "调研乙", "task": "调研奇数哥德巴赫猜想相关文献"},
                {"id": "s", "role": "汇总", "task": "基于前两位队员的产出，整理一份综述报告"},
            ]
        }
    )
    mixed = accepted(
        {
            "tasks": [
                {
                    "id": "fs",
                    "role": "全栈工程师",
                    "task": "新建桌面 AI 编程助手 MVP 骨架",
                    "deliverable": {
                        "form": "files",
                        "artifacts": [
                            "agent-editor/DESIGN.md",
                            "agent-editor/package.json",
                            "agent-editor/src/main.ts",
                        ],
                    },
                }
            ]
        }
    )
    root_ws = accepted(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "从零实现应用 MVP",
                    "deliverable": {"form": "workspace"},
                }
            ]
        }
    )
    nested = accepted(
        {
            "tasks": [
                {
                    "role": "工程师",
                    "task": "从零实现应用 MVP",
                    "deliverable": {"form": "workspace"},
                }
            ]
        },
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
            "tasks": [
                {"id": "r1", "role": "调研", "task": "查资料"},
                {"id": "w1", "role": "写手", "task": "成文", "depends_on": ["r1"]},
            ]
        }
    )
    assert isinstance(out, DelegateBatchRequest)
    assert not hasattr(out, "consumer_deps_warn")
