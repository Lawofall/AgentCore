"""Escalation Gate — tool_exec 后区分执行层自愈 vs 方案层上报。

与 Worker 主动 ``escalate`` 工具正交：Gate 是确定性后置检查；
``escalate`` 是模型主动通道（方案层 / 职责偏离的唯一自由文入口）。

Gate **不再**对工具输出做方案层词扫（禁扫自由文猜意图加闸）。
「职责偏离」只来自结构化 ``escalate(kind=scope|…)`` 或写工具层真越界硬拒。
失败默认视为执行层（自愈），避免误报方案层。
"""

from __future__ import annotations

import re
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.runtime.routing.models import (
    EscalationSignal,
    GateVerdict,
    ProblemLayer,
)

logger = get_logger(__name__)

# 执行层：路径 / import / lint / 超时 / 工具瞬时失败（自愈，不上报）
_EXECUTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"FileNotFoundError|No such file|路径(不存在|错误)|ENOENT",
        r"ModuleNotFoundError|ImportError|import\s+error|cannot\s+import",
        r"SyntaxError|IndentationError|lint|ruff|eslint|prettier",
        r"超时|timeout|timed?\s*out|退出码|exit\s*code|Traceback",
        r"工具 '.*' (执行时发生内部错误|执行超过|未找到)",
        r"ConnectionError|ECONNREFUSED|rate\s*limit|429|5\d\d",
        r"请调整方案或换一种方式|不要原样重试",
    )
)

# 协调类工具失败不走 Gate（它们有自己的通道）
_SKIP_TOOLS = frozenset(
    {"escalate", "handoff", "delegate"}
)


def evaluate_after_tools(
    *,
    attempts: list[ToolAttempt],
    tool_outputs: list[str] | None = None,
    run_id: str = "",
) -> GateVerdict:
    """在一轮 ``execute_tools`` 完成后做分层判定。

    方案层不上报：不扫 ``tool_outputs`` 自由文猜契约/范围/矛盾。
    失败默认执行层（自愈）。``tool_outputs`` 仍接受以保持调用方契约稳定。
    """
    del tool_outputs  # 有意不扫：弱内容词不得产 scheme_escalation
    for attempt in attempts:
        if attempt.tool_name in _SKIP_TOOLS:
            continue
        if not attempt.success:
            logger.debug(
                "routing.gate.execution_layer",
                run_id=run_id,
                tool=attempt.tool_name,
                policy_failure=attempt.policy_failure,
            )

    return GateVerdict(layer=ProblemLayer.EXECUTION, action="continue", signals=[])


def classify_problem(text: str) -> ProblemLayer:
    """对任意障碍文本做分层（供测试 / 诊断；Gate 主路径用 :func:`evaluate_after_tools`）。

    不再把方案层词当 SCHEME——方案层走结构化 ``escalate``，不靠自由文猜。
    """
    if any(p.search(text) for p in _EXECUTION_PATTERNS):
        return ProblemLayer.EXECUTION
    # 未知偏执行层：宁可自愈一轮，也不误报方案层
    return ProblemLayer.EXECUTION


def signals_as_dicts(signals: list[EscalationSignal]) -> list[dict[str, Any]]:
    """Serialize gate signals for ``RunState.escalations`` merge."""
    return [s.to_run_escalation_payload() for s in signals]
