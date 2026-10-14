from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentcore.runtime.runs.constants import MAX_DELEGATION_DEPTH
from agentcore.tools.protocol import Tool


@dataclass(frozen=True)
class LeadSubteam:
    """A worker-captain's nested-delegation handle (阶段2 嵌套 + 受监督子计划 B).

    The factory mints this in the tools layer so ``runs`` stays free of a concrete
    tools dependency (it only touches the opaque :class:`~agentcore.tools.protocol.Tool`
    objects + the ``dispose`` closure here):

    - ``tools`` — the lead's own ``delegate`` PLUS the companion ``replan`` bound to
      THAT delegate instance. Both ride the opening table (prefix cache). Idle
      ``replan`` fails at execute until a nested sub-plan exists.
      Wiring ``replan`` for a lead (not just the root CEO) is the 去特例 fix: a lead
      supervises its own sub-plan's 波边界 (子队员 escalate scope)
      exactly like the CEO — without it a yielding sub-plan would be a dead-end.
    - ``tool_names`` — re-grant those names on a least-privilege allow-list (mirrors
      how ``escalate`` is kept callable for a restricted worker).
    - ``dispose`` — turn-end disposition of a sub-plan the lead yielded but never
      resumed (堵漏账): fold its completed workers' usage/ledger/citations in before
      the parent absorbs this child, so a lead that wrapped up without a ``replan``
      never strands sub-team spend. No-op when nothing is paused; best-effort.
    """

    tools: tuple[Tool, ...]
    tool_names: tuple[str, ...]
    dispose: Callable[[], Awaitable[None]]


# A worker's nested-delegate factory: given (captain_run_id, captain_depth) — the
# worker's own run id + depth — it mints the worker's :class:`LeadSubteam` (its own
# ``delegate`` + the companion ``replan`` bound to it as the sub-team's captain).
# Owned by the GraphTool (which can import the tools package), passed in here so
# ``runs`` stays free of a concrete tools dependency.
DelegateFactory = Callable[[str, int], LeadSubteam]

# 阻塞式求决策 并发上限 (设计 §4.6): at most this many workers may be suspended on a
# wait-escalate at once (per conversation). Beyond it a further wait-escalate
# degrades to finish-under-assumption — caps card-flood + stops a whole
# wave's width being parked on the user. Tunable; start conservative.
ESCALATION_CONCURRENCY_CAP = 3


def worker_child_nest_fact(*, depth: int) -> str:
    """Captain-only opening fact: whether this lead's children may nest.

    Not ``<身份>`` — the tool table already distinguishes captain/leaf;
    this sentence is the residual the captain's own tools cannot encode
    (it is about the children's cap).
    """
    if depth < MAX_DELEGATION_DEPTH - 1:
        return "你的子成员仍可再向下委派一层。"
    return "你的子成员不能再向下委派。"


def build_worker_identity_catalog(*, captain: bool, depth: int = 1) -> str:
    """Toolbox template: factory worker ``<身份>`` is empty.

    Nest-cap is a live opening fact, not a catalog identity. Form HOW is
    per-turn 交付物规格.
    """
    _ = (captain, depth)
    return ""


# 环境能力自述（能写 ≠ 能跑）: appended ONLY when the turn's worker registry carries no
# execution class (cloud location=server without sandbox — see
# ``tools.builtin.code_execution_enabled_for``). Distinguishes「能写脚本落盘」from「能运行」
# so a worker in a no-exec workspace does not over-claim a runnable
# ``code_execute`` the turn withheld, nor burns rounds escalating for a tool
# that will never appear. Kept OFF the local / sandboxed paths
# (byte-identical identities there).
_WORKER_NO_EXECUTION_POLICY = (
    "【本回合执行环境未装配】没有 run：【能】写文件，【不能】运行。注明未运行。"
)


def build_worker_identity(
    *,
    has_dependents: bool,
    captain: bool = False,
    depth: int = 1,
    can_execute: bool = True,
) -> str:
    """Per-turn worker facts that are not ``<身份>``.

    Leaf default is empty: scope / no-delegate / audience live in the tool
    table, task block, and ``escalate`` / ``handoff`` descriptions.
    Captain adds nest-cap honesty (children's tools, not this node's).
    ``has_dependents`` is accepted for call-site stability; handoff must-vs-may
    lives on the handoff tool description.
    Form HOW lives on the per-turn 交付物规格 channel.
    ``can_execute`` False layers 能写≠能跑 so the prompt never over-claims
    a callable ``run`` the turn withheld.
    """
    _ = has_dependents
    parts: list[str] = []
    if captain:
        parts.append(worker_child_nest_fact(depth=depth))
    if not can_execute:
        parts.append(_WORKER_NO_EXECUTION_POLICY)
    return "\n\n".join(parts)


# Default for ``_build_messages`` when the caller has not resolved topology.
_WORKER_IDENTITY = build_worker_identity(has_dependents=False, captain=False)
