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
      THAT delegate instance. The bundle mints both (dispose / 波边界 binding); the
      opening registry registers ``delegate`` only. ``replan`` is offered after a
      nested sub-plan exists (``_supervised``), via ``promote_coordination_surface_if_needed``.
      Wiring ``replan`` for a lead (not just the root CEO) is the 去特例 fix: a lead
      supervises its own sub-plan's 波边界 (bind_after_deps / 子队员 escalate scope)
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
# Owned by the DelegateTool (which can import the tools package), passed in here so
# ``runs`` stays free of a concrete tools dependency.
DelegateFactory = Callable[[str, int], LeadSubteam]

# 阻塞式求决策 并发上限 (设计 §4.6): at most this many workers may be suspended on a
# blocking escalate at once (per conversation). Beyond it a further blocking escalate
# degrades to non-blocking (proceed on assumption) — caps card-flood + stops a whole
# wave's width being parked on the user. Tunable; start conservative.
ESCALATION_CONCURRENCY_CAP = 3


def _worker_identity_core(*, captain: bool, depth: int) -> str:
    intro = _worker_captain_intro(depth=depth) if captain else _WORKER_LEAF_INTRO
    return f"<身份>\n{intro}\n</身份>"


def build_worker_identity_catalog(*, captain: bool, depth: int = 1) -> str:
    """Toolbox template: ``<身份>`` only. Form HOW is per-turn 交付物规格."""
    return _worker_identity_core(captain=captain, depth=depth)


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

# Leaf-worker intro (no nested delegate). Isolated context, no follow-ups, no delegate.
# Product membership lives here (shared base does not write 队员 / <身份>).
# 品类介绍 / 标假设 → escalate description；不进身份。
_WORKER_LEAF_INTRO = """\
你是 AgentCore 的队员，只负责划定好的这一件任务（所需上下文已给你）。\
不能再向下委派。够不到用户。"""

# Captain intro: identity + nest honesty. Depth honesty branches on MAX_DELEGATION_DEPTH.
# Staffing HOW → consult(lead_subteam) (requires_tools=delegate, worker-only).
# Not the CEO encyclopedia and not identity.


def _worker_captain_intro(*, depth: int) -> str:
    # Children land at depth+1; they may nest iff depth+1 < MAX (i.e. depth < MAX-1).
    if depth < MAX_DELEGATION_DEPTH - 1:
        nest_honesty = (
            "你可以再向下委派一层子团队（你的子成员仍可再向下委派一层），看到产出后由你整合。"
        )
    else:
        nest_honesty = (
            "你可以再向下委派一层子团队（只能再嵌套这一层，你的子成员不能再向下委派），"
            "看到产出后由你整合。"
        )
    return (
        "你是 AgentCore 的队员，只负责划定好的这一件任务（所需上下文已给你）。够不到用户。"
        f"{nest_honesty}"
    )


def build_worker_identity(
    *,
    has_dependents: bool,
    captain: bool = False,
    depth: int = 1,
    can_execute: bool = True,
) -> str:
    """Assemble a worker's ``<身份>`` preamble (leaf / captain).

    ``has_dependents`` is accepted for call-site stability; handoff must-vs-may
    lives on the handoff tool description, not this identity.
    Form HOW lives on the per-turn 交付物规格 channel, not here.
    ``captain`` selects the nested-delegation intro;
    ``depth`` (when captain) picks honest child-nesting copy vs ``MAX_DELEGATION_DEPTH``.
    ``can_execute`` is computed after exec-env sticky retire (and after the
    execution class is absent from the registry, e.g. cloud without sandbox):
    False layers the 能写≠能跑 self-description so the prompt never over-claims
    a callable ``code_execute`` the turn withheld (能力闸门与交付诚实性).
    """
    _ = has_dependents
    no_exec = "" if can_execute else f"\n\n{_WORKER_NO_EXECUTION_POLICY}"
    core = _worker_identity_core(captain=captain, depth=depth)
    return f"{core}{no_exec}"


# Defaults for callers that don't yet know topology (solo / leaf assumption).
# Prefer :func:`build_worker_identity` at the executor so captain/leaf matches.
_WORKER_IDENTITY = build_worker_identity(has_dependents=False, captain=False)
_WORKER_CAPTAIN_IDENTITY = build_worker_identity(has_dependents=False, captain=True)
