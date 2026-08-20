"""Delegate playbook declaration gate（结构校验）.

默认主路：手写顶层 ``tasks``（可省略 playbook）。
具名 playbook = 固化流水线快捷进阶（建站 / 工具台 / 绿场等）；与 tasks XOR。
建站快捷：``build_website``（工具台气质用 ``style=toolshed``）；绿场快捷：``build_app``
（软引导见 skill / schema）；省略 playbook / 手写不再因意图硬拒。

场面账（automation delivery / website style / presentation format）已拆除：
具名 playbook 不再因交付形态记账硬拒。
"""

from __future__ import annotations

from typing import Any, Literal

from agentcore.runtime.runs.playbooks import PLAYBOOKS, available_playbooks

DeclarationRejectGate = Literal[
    "empty",
    "unknown",
    "xor",
]

PLAYBOOK_TASKS_XOR_MSG = (
    "playbook 与 tasks 二选一，不可同时传。"
    "默认手写 tasks：去掉具名 playbook，只传 tasks；"
    "快捷固化流水线：只传 playbook（+playbook_args 槽位），不要传 tasks。"
    "已有调查批要按结论修码：去掉 playbook，手写 tasks 并设 continue_from_run_id。"
)

HANDWRITTEN_PLAYBOOK_ARGS_MSG = (
    "手写 tasks 时勿传 playbook_args；"
    "playbook_args 仅配合具名 playbook 使用。"
)

# 弱模型可抄的顶层 tasks 三件套（role/task + 可选 deliverable）；schema 与 empty 拒收共用。
HANDWRITTEN_TASKS_SKELETON = (
    '{"tasks":[{"role":"角色","task":"目标+边界+验收","deliverable":{"form":"prose"}}]}'
)

_EMPTY_DELEGATE_MSG = (
    "delegate 缺 tasks/playbook：默认顶层放非空 `tasks`，"
    f"可抄：{HANDWRITTEN_TASKS_SKELETON}"
    "（deliverable 可选）；"
    "固化流水线时次选具名 `playbook`（+ playbook_args）。"
)


def try_declaration_reject_gate(error: str | None) -> DeclarationRejectGate | None:
    """Return gate when ``error`` matches a known declaration reject template.

    Structured template / prefix match only (not free-text intent scan).
    ``None`` when the message is not a declaration-gate reject — callers must
    not treat arbitrary tool errors as ``unknown``.
    """
    if not error:
        return None
    if error in (
        PLAYBOOK_TASKS_XOR_MSG,
        HANDWRITTEN_PLAYBOOK_ARGS_MSG,
    ) or error.startswith(
        ("playbook 与 tasks 二选一", "手写 tasks 时勿传")
    ):
        return "xor"
    if error == _EMPTY_DELEGATE_MSG or error.startswith(
        ("delegate 须传手写", "delegate 缺 tasks/playbook")
    ):
        return "empty"
    if error.startswith("未知 playbook"):
        return "unknown"
    return None


def declaration_reject_gate(error: str | None) -> DeclarationRejectGate:
    """Classify a declaration reject for logging / probes."""
    return try_declaration_reject_gate(error) or "unknown"


def resolve_playbook_declaration(
    arguments: dict[str, Any],
    *,
    user_message: str = "",
    automation_delivery: Any = None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve declaration → ``(playbook_name|None, none_reason|None, error|None)``.

    ``playbook_name`` set ⇒ expand that playbook. ``none_reason`` is always ``None``
    (legacy slot retained for call-site compat; field removed from CEO schema).
    ``error`` set ⇒ reject the call.

    Free teaming defaults to handwritten ``tasks`` (playbook may be omitted). Named
    playbooks remain a shortcut when declared. ``automation_delivery`` retained for
    call-site compatibility (ignored — scene ledger removed).
    ``user_message`` retained for call-site compatibility (no intent hard-lock).
    """
    _ = user_message  # call-site compat; soft guidance only (no intent hard-lock)
    _ = automation_delivery  # scene ledger removed; kw kept for call-site compat
    legacy = arguments.get("playbook")

    # Named playbook: non-empty ``playbook`` naming a registry entry.
    legacy_s = legacy.strip() if isinstance(legacy, str) and legacy.strip() else ""
    named: str | None = legacy_s or None

    tasks = arguments.get("tasks")
    has_tasks = isinstance(tasks, list) and bool(tasks)

    if named is not None:
        if has_tasks:
            # playbook XOR tasks — reject before expand / fanout (避免半跑).
            return None, None, PLAYBOOK_TASKS_XOR_MSG
        if named not in PLAYBOOKS:
            return None, None, (
                f"未知 playbook『{named}』；可用：{available_playbooks()}。"
                "或手写 `tasks`（可不声明 playbook）；"
                "建站推荐具名 `build_website`（控制台 dense 加 style=toolshed）；"
                "绿场软件推荐具名 `build_app`。"
            )
        # 具名 build_app / build_website 等直接放行。
        return named, None, None

    # Hand-written path: omit playbook, pass non-empty tasks.
    if has_tasks:
        if arguments.get("playbook_args"):
            return None, None, HANDWRITTEN_PLAYBOOK_ARGS_MSG
        return None, None, None

    return None, None, _EMPTY_DELEGATE_MSG
