"""Engine deny / timeout paths must not hand the model's orders to the user.

``tool_use_end`` has two channels: ``result`` steers the model, ``failure.message`` is what
the person reads. Every deny path below used to publish one string to both — so a user who
had just clicked 拒绝 was answered with「不要再调用此工具」, and an unattended run told them to
「让用户在可确认的界面重试」when they *are* the user and no such screen exists.

Each case pins both halves: the model face byte-for-byte (those imperatives earn their keep —
without them the model retries the identical call) and the user face as curated copy.

Coverage is the whole family, not the reported instances: approval / fuse / allow-list /
budget / liveness denials, plus the name paths (unknown tool, off-surface tool, landed-status
bait) and the one authored ``product_message`` that is meant to stay.

收尾窗口 deny lives in ``react_loop`` and is pinned by
``test_worker_cutoff.test_wind_down_breach_journals_denied_tool``.
"""

import asyncio
from pathlib import Path
from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.llm.provider.protocol import ToolCall, ToolCallFunction
from agentcore.runtime.approvals import ApprovalDecision
from agentcore.runtime.engine.tool_exec import execute_tools
from agentcore.runtime.engine.tool_failure_face import _CURATED_BY_CODE
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.retrieval_budget import BUDGET_EXHAUSTED_FEEDBACK
from agentcore.runtime.safety_breaker import evaluate_tool_call
from agentcore.tools.protocol import (
    RetrievalBudgetState,
    ToolContext,
    ToolResult,
    ToolSchema,
)
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.user_face_helpers import assert_user_face_clean

# Codes whose curated copy is the *only* thing these paths show a user.
DENY_FACE_CODES = (
    "safety_breaker_deny",
    "safety_breaker_unattended",
    "approval_unattended",
    "approval_denied",
    "allowlist_deny",
    "wind_down_deny",
    "liveness_timeout",
    "timeout",
    "retrieval_budget_exhausted",
    "landed_status_name",
    ErrorCode.TOOL_NOT_FOUND,
)


def _ctx(**fields: Any) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        **fields,
    )


def _call(tool_id: str, name: str, args: str = "{}") -> ToolCall:
    return ToolCall(id=tool_id, function=ToolCallFunction(name=name, arguments=args))


class _Stub:
    """Registered so the call reaches the gates; never expected to execute."""

    def __init__(
        self,
        name: str,
        *,
        category: ToolCategory = ToolCategory.SEARCH,
        approval: ToolApproval = ToolApproval.NEVER,
        timeout_seconds: float | None = None,
        sleep: float | None = None,
    ) -> None:
        self._name = name
        self._category = category
        self._approval = approval
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep
        self.executed = False

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="stub",
            parameters={"type": "object", "properties": {}},
            category=self._category,
            approval=self._approval,
            timeout_seconds=self._timeout_seconds,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed = True
        if self._sleep is not None:
            await asyncio.sleep(self._sleep)
        return ToolResult(tool_call_id="", success=True, output="ok")


class _DenyingGate:
    """Minimal ``ApprovalGate`` shape: the user refuses (or the card times out)."""

    permission_axes = None
    file_op_tools: frozenset[str] = frozenset()

    def will_prompt(self, **_kwargs: Any) -> bool:
        return True

    async def authorize(self, **_kwargs: Any) -> ApprovalDecision:
        return ApprovalDecision.DENY


def _registry(*tools: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in tools:
        reg.register(tool)
    return reg


def _end_payload(sink: EventSink) -> dict[str, Any]:
    ends = [e for e in sink._history if e.type == EventType.TOOL_USE_END]  # noqa: SLF001
    assert len(ends) == 1, f"expected exactly one tool_use_end, got {ends!r}"
    return ends[0].payload


def _faces(sink: EventSink) -> tuple[str, str]:
    """``(model face, user face)`` for the single tool_use_end on the sink."""
    payload = _end_payload(sink)
    assert payload["status"] == "error"
    failure = payload.get("failure") or {}
    return payload.get("result") or "", failure.get("message") or ""


async def test_every_deny_code_has_curated_user_copy():
    """The table is the whole user face for these paths — no entry may read like a command."""
    for code in DENY_FACE_CODES:
        assert code in _CURATED_BY_CODE, f"{code} has no curated copy"
        assert_user_face_clean(_CURATED_BY_CODE[code])


async def test_user_denied_approval_is_not_answered_with_an_order():
    """User clicks 拒绝 → the model is told to stop; the user is not ordered around."""
    tool = _Stub("delete_folder", approval=ToolApproval.GRANTABLE)
    sink = EventSink()
    _messages, _terminal, attempts = await execute_tools(
        [_call("c1", "delete_folder")],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=_DenyingGate(),  # type: ignore[arg-type]
        run_id="r1",
    )

    assert tool.executed is False
    assert attempts[0].policy_failure is True
    model, user = _faces(sink)
    assert model == (
        "工具 'delete_folder' 未获用户授权，该操作未执行。"
        "请改用其他方案或询问如何继续，不要再调用此工具。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["approval_denied"]


async def test_grantable_without_gate_does_not_promise_a_missing_screen():
    """Unattended path: the reader IS the user — never send them to a confirm screen."""
    tool = _Stub("mcp_write", approval=ToolApproval.GRANTABLE)
    sink = EventSink()
    await execute_tools(
        [_call("c1", "mcp_write")],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == (
        "工具 'mcp_write' 需要用户授权，但当前路径没有可询问的用户，已拒绝执行。"
        "请改用其他方案，或让用户在可确认的界面重试。"
    )
    assert "让用户在可确认的界面重试" in model  # model face intentionally unchanged
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["approval_unattended"]


async def test_always_confirm_without_gate_does_not_promise_a_missing_screen():
    """恒确认 (delete_folder) with nobody to ask — same split, same curated sentence."""
    tool = _Stub("delete_folder")
    sink = EventSink()
    await execute_tools(
        [_call("c1", "delete_folder", '{"folder_id":"f1"}')],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == (
        "工具 'delete_folder' 必须由用户逐次确认，但当前路径弹不出确认卡，已拒绝执行。"
        "请改用其他方案，或让用户在可确认的界面重试。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["approval_unattended"]


async def test_safety_breaker_deny_keeps_steer_off_the_user_face():
    """Fuse DENY: the model keeps the rule reason, the user gets a plain safety sentence."""
    args = '{"subcommand":"reset"}'
    hit = evaluate_tool_call("git", {"subcommand": "reset"})
    assert hit is not None

    sink = EventSink()
    tool = _Stub("git", category=ToolCategory.EXECUTION)
    await execute_tools(
        [_call("c1", "git", args)],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == (f"工具 'git' 被安全熔断拒绝：{hit.reason}请改用其他方案，不要原样重试该路径。")
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["safety_breaker_deny"]


async def test_safety_breaker_without_gate_keeps_steer_off_the_user_face():
    """Fuse FORCE + nobody to ask: user hears the risk, not the rule text."""
    args = '{"command":"rm -rf /"}'
    hit = evaluate_tool_call("run", {"command": "rm -rf /"})
    assert hit is not None

    sink = EventSink()
    tool = _Stub("run", category=ToolCategory.EXECUTION)
    await execute_tools(
        [_call("c1", "run", args)],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == (
        f"工具 'run' 触发安全熔断且当前路径无法人工确认，已拒绝执行。{hit.reason}请改用其他方案。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["safety_breaker_unattended"]


async def test_run_allowlist_deny_keeps_engine_words_off_the_user_face():
    """Landing tool outside the run allow-list: 「本 run 的允许列表」is ours, not theirs."""
    tool = _Stub("file_write", category=ToolCategory.FILESYSTEM)
    sink = EventSink()
    await execute_tools(
        [_call("c1", "file_write", '{"path":"a.md","content":"x"}')],
        _registry(tool, _Stub("file_read", category=ToolCategory.FILESYSTEM)),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
        allowed_tool_names=["file_read"],
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == (
        "工具 'file_write' 不在本 run 的允许列表中，未执行。"
        "本回合未授权该写盘工具；请改用已提供的工具，或 escalate / "
        "handoff 说明缺写盘权限（勿用正文冒充落盘）。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["allowlist_deny"]


async def test_retrieval_budget_exhausted_keeps_ledger_talk_off_the_user_face():
    """Budget out: the model is pointed at the 台账, the user just hears searching stopped."""
    tool = _Stub("web_search")
    sink = EventSink()
    await execute_tools(
        [_call("c1", "web_search", '{"query":"x"}')],
        _registry(tool),
        _ctx(retrieval_budget=RetrievalBudgetState(limit=0)),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert model == BUDGET_EXHAUSTED_FEEDBACK
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["retrieval_budget_exhausted"]


async def test_unknown_tool_name_keeps_the_did_you_mean_steer_off_the_user_face():
    """Hallucinated name: the model gets the naming rule, the user just hears we moved on."""
    sink = EventSink()
    await execute_tools(
        [_call("c1", "quux_tool")],
        _registry(_Stub("grep", category=ToolCategory.FILESYSTEM)),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    model, user = _faces(sink)
    assert model == ("Tool 'quux_tool' not found。请使用合法工具名原样重试，勿夹带协议标签。")
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE[ErrorCode.TOOL_NOT_FOUND]


async def test_tool_off_this_surface_keeps_role_steer_off_the_user_face():
    """Declared but not assembled here: 「CEO 派给 worker」is our org chart, not theirs."""
    from agentcore.tools.registration import (
        execution_class_tool_names,
        worker_only_tool_names,
    )

    # Precondition — execution class is assembled for both roles when the env is on.
    assert "run" in execution_class_tool_names()
    assert "run" not in worker_only_tool_names()

    sink = EventSink()
    await execute_tools(
        [_call("c1", "run")],
        _registry(_Stub("grep", category=ToolCategory.FILESYSTEM)),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    model, user = _faces(sink)
    assert model == (
        "工具 'run' 本回合未装配执行类工具（见 `<工作区>` 的"
        "「本回合执行能力」），勿空转重试。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["allowlist_deny"]


async def test_landed_status_bait_keeps_the_rewrite_recipe_off_the_user_face():
    """``_write_landed`` imitation: the model needs the read-then-rewrite recipe; the user doesn't."""
    sink = EventSink()
    await execute_tools(
        [_call("c1", "_write_landed", '{"path":"a.md","status":"landed"}')],
        _registry(_Stub("grep", category=ToolCategory.FILESYSTEM)),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    model, user = _faces(sink)
    assert model == (
        "拒绝：`_write_landed` 是请求窗里的「已落盘」压缩状态，不是可调用工具。"
        "勿仿调该名称。改稿：先 file_read 取盘上真文，再 str_replace（优先）或 file_write。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["landed_status_name"]


async def test_write_args_parse_failure_is_the_one_legitimate_authored_face():
    """The lone surviving ``product_message`` — authored human copy, not the model's steer.

    Landing tools get a short line for the person and the segmented-write recipe for the
    model. Pinned here so the split cannot quietly collapse back into one string.
    """
    from agentcore.runtime.engine.tool_exec_args import _USER_WRITE_PARSE_MSG

    tool = _Stub("file_write", category=ToolCategory.FILESYSTEM)
    sink = EventSink()
    await execute_tools(
        [_call("c1", "file_write", '{"path": "a.md", "content": "abc')],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    assert tool.executed is False
    model, user = _faces(sink)
    assert user == _USER_WRITE_PARSE_MSG
    assert_user_face_clean(user)
    # Recipe stays model-side, verbatim.
    assert "改为短骨架 + 按节 file_append / str_replace 分段落盘" in model


async def test_liveness_timeout_keeps_retry_ban_off_the_user_face():
    """A wedged tool: 「活性挂起…禁止原样重试」is steer, not something to show a person."""
    tool = _Stub("grep", category=ToolCategory.FILESYSTEM, timeout_seconds=0.01, sleep=5)
    sink = EventSink()
    await execute_tools(
        [_call("c1", "grep", '{"pattern":"x"}')],
        _registry(tool),
        _ctx(),
        sink,
        approval_gate=None,
        run_id="r1",
    )

    model, user = _faces(sink)
    assert model == (
        "工具 'grep' 活性挂起：超过 0s 仍无响应，已中止。"
        "这不是字节/行数触顶——请缩小处理范围、换路径策略或换工具；"
        "禁止原样重试同一次调用。"
    )
    assert_user_face_clean(user)
    assert user == _CURATED_BY_CODE["liveness_timeout"]
