"""逐闸 ``force``：把「一键全开」拆成点名放行的单闸开关。

旧形态是实例上的单个 ``_delegate_force: bool``——一次 ``force=true`` 同时关掉
收口后冷开 / 同构再派 / 触顶换马甲 / 座位重叠四道闸，且因为它活在工具实例上，
上一次 ``delegate`` 的 force 会被后续 ``replan`` 读到（跨调用泄漏）。

现在 ``force`` 收一个闸名数组，每道闸只问自己那一格；调用侧（``execute`` /
``replan``）在入口处**无条件**重解析，实例上不再有跨调用残值。

通道死（``channel_dead_gate``）不在此列：那是能力缺失，任何 scope 都不放行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

# 收口后冷开整团重派（post_close_gate）。
GATE_POST_CLOSE = "post_close"
# 活跃协作图上角色+任务同构再派（coordination.isomorphic）。
GATE_ISOMORPHIC = "isomorphic"
# 触顶打转后换马甲冷派（coordination.thrash）。
GATE_THRASH = "thrash"
# 座位 / 交付物归属重叠（coordination.append_guard）。
GATE_SEAT_OVERLAP = "seat_overlap"

FORCE_GATES: tuple[str, ...] = (
    GATE_POST_CLOSE,
    GATE_ISOMORPHIC,
    GATE_THRASH,
    GATE_SEAT_OVERLAP,
)

_KNOWN = frozenset(FORCE_GATES)


@dataclass(frozen=True, slots=True)
class ForceScopes:
    """本次调用点名放行的闸集合（空 = 四道闸全开着）。"""

    scopes: frozenset[str] = frozenset()

    def allows(self, gate: str) -> bool:
        return gate in self.scopes

    def __bool__(self) -> bool:
        return bool(self.scopes)


EMPTY_FORCE_SCOPES = ForceScopes()


def parse_force_scopes(raw: Any) -> ForceScopes:
    """解析 ``delegate`` / ``replan`` 的 ``force`` 入参为闸集合。

    只认闸名数组（及单个闸名字符串）。其它类型（含历史布尔 ``force=true`` /
    ``"all"`` 等泛化写法）走不可解析 → 空集；具体哪道闸拒绝时由该闸自己报出
    它的 scope 名。未知闸名忽略。
    """
    if raw is None:
        return EMPTY_FORCE_SCOPES
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = list(raw)
    else:
        logger.info("delegate.force_unparsable", value_type=type(raw).__name__)
        return EMPTY_FORCE_SCOPES

    scopes: set[str] = set()
    unknown: list[str] = []
    for item in items:
        name = str(item or "").strip().lower()
        if not name:
            continue
        if name in _KNOWN:
            scopes.add(name)
        else:
            unknown.append(name)
    if unknown:
        logger.info(
            "delegate.force_unknown_gate",
            unknown=unknown[:8],
            known=list(FORCE_GATES),
        )
    return ForceScopes(frozenset(scopes))


def tool_force_scopes(tool: Any) -> ForceScopes:
    """读工具实例上本次调用的 scope（未装配 → 空集）。"""
    scopes = getattr(tool, "_force_scopes", None)
    return scopes if isinstance(scopes, ForceScopes) else EMPTY_FORCE_SCOPES


def force_allows(tool: Any, gate: str) -> bool:
    """本次调用是否点名放行 ``gate``。"""
    return tool_force_scopes(tool).allows(gate)


def force_hint(gate: str) -> str:
    """拒绝文案里指向该闸自己的开关（不指向已退役的一键全开）。"""
    return f'force=["{gate}"]'
