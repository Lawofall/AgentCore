"""委派前奏：`delegate` 入参的零 await 校验 + 规范化。

从 `DelegateTool.execute` 抽出（纯搬运）。这一段只读 `arguments` 与回合环境
（token 硬顶 / 鉴权死 / 工具表 / 深度），不碰协作图、不碰 DB、不写 `self`：
要么产出一份「规范化后的委派请求」，要么产出一条硬拒。

`_active_playbook` / `_active_playbook_args` 两个 per-call 标记原来在这里直接写 `self`，
现在改由 :class:`DelegateCallFlags` 带回、`execute` 赋值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.llm.turn_auth_dead import (
    is_turn_auth_dead,
    turn_auth_dead_reject_message,
)
from agentcore.runtime.delegate.playbook_declaration import (
    PLAYBOOK_TASKS_XOR_MSG,
    declaration_reject_gate,
    resolve_playbook_declaration,
)
from agentcore.runtime.runs.constants import MAX_WORKER_SUBDELEGATIONS
from agentcore.runtime.runs.playbooks import collect_playbook_notes, expand_playbook
from agentcore.runtime.turn.token_budget import (
    current_turn_tokens,
    is_turn_token_ceiling_hit,
    resolve_turn_token_ceiling,
    turn_token_ceiling_reject_message,
)
from agentcore.tools.protocol import ToolResult

if TYPE_CHECKING:
    from agentcore.tools.registry import ToolRegistry

logger = get_logger(__name__)


def _has_wave_boundary_features(tasks_raw: list[Any]) -> bool:
    """True when any task needs BIND / CHECKPOINT / DAG wave-boundary machinery."""
    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        if task.get("depends_on") or task.get("checkpoint_after") or task.get("bind_after_deps"):
            return True
    return False


def _has_deep_deliverable_signal(tasks_raw: list[Any]) -> bool:
    """True when any task declares a landing deliverable (files / workspace /
    non-empty artifacts / omitted form). Only explicit ``form=prose`` is exempt.
    """
    from agentcore.runtime.runs.types import raw_deliverable_expects_landing

    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        if raw_deliverable_expects_landing(task.get("deliverable")):
            return True
    return False


def _should_auto_light_delegate(tasks_raw: list[Any]) -> bool:
    """True when a single dependency-free worker needs no multi-agent coordination.

    Skips auto-light when the task expects on-disk landing (``form=files`` /
    ``workspace`` / omitted form / non-empty artifacts). Only explicit
    ``form=prose`` auto-lights.
    ``complexity_hint=light`` no longer stamps short ``max_rounds``; browser tool
    surfaces are not excluded from auto-light for round-budget reasons.
    """
    if len(tasks_raw) != 1:
        return False
    task = tasks_raw[0]
    if not isinstance(task, dict):
        return False
    if _has_wave_boundary_features([task]):
        return False
    return not _has_deep_deliverable_signal([task])


@dataclass(frozen=True)
class DelegateCallFlags:
    """本次调用的 per-call 标记，由 `execute` 镜像到工具实例上。"""

    playbook: str | None
    playbook_args: dict[str, Any] | None


@dataclass(frozen=True)
class DelegatePreludeReject:
    """前奏硬拒：`execute` 镜像 ``flags``（若有）后原样返回 ``result``。

    ``flags`` 为 ``None`` = 原内联代码走到这道拒绝时还没写过那几个 per-call 标记。
    """

    result: ToolResult
    flags: DelegateCallFlags | None = None


@dataclass(frozen=True)
class DelegateBatchRequest:
    """规范化后的委派请求：前奏全绿时 `execute` 后续要用的全部输入。"""

    flags: DelegateCallFlags
    tasks_raw: list[Any]
    playbook_notes: list[str]
    valid_tools: set[str]
    # CEO 可传任意值；下游按需 isinstance 收窄（build_run_plan）。
    complexity_hint: Any

    @property
    def playbook(self) -> str | None:
        """本批展开的 playbook 名（手写 tasks 为 ``None``）。"""
        return self.flags.playbook


def resolve_delegate_prelude(
    arguments: dict[str, Any],
    *,
    tools: ToolRegistry,
    user_message: str,
    conversation_id: str | None,
    depth: int,
    sub_workers_spawned: int,
    credential_source: str,
) -> DelegateBatchRequest | DelegatePreludeReject:
    """校验并规范化一次 `delegate` 调用（零 await；硬拒后规范化）。"""
    # Turn 级硬顶：禁新派（在飞不 cancel）；与 per-worker ceiling 正交。
    if is_turn_token_ceiling_hit():
        msg = turn_token_ceiling_reject_message()
        logger.info(
            "delegate.turn_token_ceiling_rejected",
            spent=current_turn_tokens(),
            ceiling=resolve_turn_token_ceiling(),
        )
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                contract_failure=True,
            )
        )

    # 甲+乙：本回合该付款方 API Key 已鉴权死后禁再 delegate 烧同源调用。
    if is_turn_auth_dead(credential_source):
        msg = turn_auth_dead_reject_message(credential_source)
        logger.info("delegate.turn_auth_dead_rejected")
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=msg,
                contract_failure=True,
            )
        )

    # Playbook 声明闸：结构校验；建站/绿场 none 不硬拒。
    declared_playbook, decl_error = resolve_playbook_declaration(arguments)
    if decl_error:
        gate = declaration_reject_gate(decl_error)
        logger.info(
            "delegate.playbook_declaration_rejected",
            playbook_id=arguments.get("playbook"),
            has_tasks=bool(arguments.get("tasks")),
            gate=gate,
        )
        return DelegatePreludeReject(
            ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=decl_error,
                contract_failure=True,
            )
        )
    logger.info(
        "delegate.playbook_declaration",
        playbook_id=declared_playbook or "none",
    )

    # 拆·playbook 固化 (§2.1): a固化形状 instantiates the whole tasks array, then flows through
    # the SAME pipeline below as a hand-written one (纯加法). playbook XOR tasks is enforced
    # in resolve_playbook_declaration (and re-checked here as defense in depth).
    playbook = declared_playbook
    if playbook is not None:
        if arguments.get("tasks"):
            return DelegatePreludeReject(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=PLAYBOOK_TASKS_XOR_MSG,
                    # 契约自纠拒绝——勿进熔断（CEO 去掉 playbook 或去掉 tasks 会误禁用）。
                    contract_failure=True,
                )
            )
        # Mechanism: pass turn user line so playbooks (e.g. multi_lens synthesizer)
        # can inject proposition-fidelity anchors without relying on CEO-filled topic.
        tasks_raw, pb_errors = expand_playbook(
            playbook,
            arguments.get("playbook_args"),
            user_message=user_message,
            conversation_id=conversation_id or "",
        )
        if pb_errors:
            msg = "playbook 实例化失败：" + "；".join(pb_errors)
            logger.info("delegate.playbook_rejected", playbook=playbook, errors=pb_errors)
            return DelegatePreludeReject(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    contract_failure=True,
                )
            )
        active_playbook: str | None = playbook
        raw_args = arguments.get("playbook_args")
        active_playbook_args = dict(raw_args) if isinstance(raw_args, dict) else None
        playbook_notes = collect_playbook_notes(tasks_raw)
        logger.info(
            "delegate.playbook",
            playbook=playbook,
            nodes=len(tasks_raw),
            notes=len(playbook_notes),
        )
        # MLR keep 标记延后到真正开跑（team_preview CONTINUE / pre-auth 跳过），
        # 避免 STOP / 调度失败仍挡住回合收尾 orphan。
    else:
        active_playbook = None
        active_playbook_args = None
        playbook_notes = []
        raw_tasks = arguments.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            msg = "'tasks' 必须是非空数组：每个元素至少包含 role 和 task。"
            return DelegatePreludeReject(
                ToolResult(
                    tool_call_id="",
                    success=False,
                    output="",
                    error=msg,
                    contract_failure=True,
                ),
                DelegateCallFlags(playbook=None, playbook_args=None),
            )
        tasks_raw = raw_tasks

    valid_tools = {s.name for s in tools.list_all()}
    complexity_hint = arguments.get("complexity_hint", "standard")
    flags = DelegateCallFlags(
        playbook=active_playbook,
        playbook_args=active_playbook_args,
    )
    if "complexity_hint" not in arguments and _should_auto_light_delegate(tasks_raw):
        complexity_hint = "light"
        # info 级：档位归责的关键决策事件，debug 级曾导致线上排查只能靠 demo_tape 反推。
        logger.info("delegate.complexity_hint_inferred", hint="light")
    elif complexity_hint == "light" and _has_wave_boundary_features(tasks_raw):
        # 显式 light 与 DAG/波边界并存时忽略 light（避免关掉 on_boundary）。
        # 已删字数字段 / form=files / artifacts alone 不挡 light（修码快修）。
        complexity_hint = "standard"
        logger.info(
            "delegate.complexity_hint_ignored",
            reason="wave_boundary_features",
        )

    if depth >= 1:
        new_nodes = len(tasks_raw)
        if sub_workers_spawned + new_nodes > MAX_WORKER_SUBDELEGATIONS:
            msg = (
                f"子团队扇出已达上限（已派出 {sub_workers_spawned} 个 sub-worker，"
                f"本次 {new_nodes} 个，上限 {MAX_WORKER_SUBDELEGATIONS}）——请合并任务或分批。"
            )
            logger.info(
                "delegate.sub_fanout_rejected",
                spawned=sub_workers_spawned,
                requested=new_nodes,
                cap=MAX_WORKER_SUBDELEGATIONS,
            )
            return DelegatePreludeReject(
                ToolResult(tool_call_id="", success=False, output="", error=msg),
                flags,
            )
    return DelegateBatchRequest(
        flags=flags,
        tasks_raw=tasks_raw,
        playbook_notes=playbook_notes,
        valid_tools=valid_tools,
        complexity_hint=complexity_hint,
    )
