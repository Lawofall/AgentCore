"""派单时为 worker 回填统一 token 顶与墙钟超时 backstop.

全局 ``engine_worker_token_ceiling``（默认 4M）与统一墙钟 1200s 是防失控安全阀，
**不做**按任务规格的四档启发式分档。CEO 显式 ``timeout_ms`` / 预置 ``token_ceiling``
恒优先（已写入则不动）。

共享谓词（``is_deep_deliverable`` / ``is_directed_search_role`` 等）仍供定向检索
工具面、verify_policy 打标等复用——与本模块的统一 token/超时 backstop 回填正交。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec

__all__ = [
    "DIRECTED_SEARCH_DISCIPLINE",
    "DIRECTED_SEARCH_TOOL_NAMES",
    "VERIFY_POLICY_INNER",
    "VERIFY_POLICY_OUTER",
    "VERIFY_INNER_DISCIPLINE",
    "WORKER_TIMEOUT_BACKSTOP_S",
    "apply_directed_search_tools",
    "apply_directed_search_tools_to_specs",
    "apply_verify_policies",
    "apply_verify_policies_to_specs",
    "apply_worker_budgets",
    "apply_worker_budgets_to_specs",
    "ensure_directed_search_tools",
    "is_deep_deliverable",
    "is_directed_search_role",
    "is_outer_verify_role",
    "is_short_write_posture",
    "should_tighten_verify_exec_thrash",
]

# 统一墙钟 backstop；CEO 显式 timeout_ms 恒优先。
WORKER_TIMEOUT_BACKSTOP_S = 1200

# 审查 / 调查类 worker：定向检索工具（复用现有 grep / code_search，不新造）。
# 真纯丙后执行层忽略 tasks[].tools 收窄；本集合仍供审查角色提示纪律与兼容补回。
DIRECTED_SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"grep", "code_search"})

DIRECTED_SEARCH_DISCIPLINE = (
    "【检索纪律】概念/意图先用 code_search，精确符号或字符串用 grep；"
    "命中后单文件默认 file_read 整读；仅页脚已截断或已有行号时开窗；"
    "禁止无目标地整目录逐文件通读。"
)

# 角色名宽匹配：审查 / 调查 / 质检 / 审校 / review / audit …
# 只影响工具面与检索纪律，不改变 token / 超时 backstop。
_DIRECTED_SEARCH_ROLE_MARKERS: tuple[str, ...] = (
    "审校",
    "审查",
    "质检",
    "评审",
    "复核",
    "调查",
    "调研",
    "review",
    "audit",
    "investigate",
    "inspector",
    "survey",
)

# verify_policy：调查/审查默认 inner；验收/外环角色不自动打标。
VERIFY_POLICY_INNER = "inner"
VERIFY_POLICY_OUTER = "outer"
VERIFY_INNER_DISCIPLINE = (
    "【验证范围】本队员为调查/审查姿态（verify_policy=inner）："
    "禁止全仓 typecheck / build / `tsc -b`（勿用 test_run 烧分钟级预算「再确认」）。"
    "修码自检用内环 code_diagnostics；运行时 blank-page / 挂载问题优先 browser 与入口链路阅读；"
    "外环全量验绿请 escalate 或交验收员。"
)
_OUTER_VERIFY_ROLE_MARKERS: tuple[str, ...] = (
    "验收",
    "验证员",
    "外环",
    "verify",
    "acceptance",
    "typecheck",
    "qa lead",
)

def is_deep_deliverable(deliverable: Deliverable | None) -> bool:
    """True when dispatch-time deliverable signals a write-desk / file report.

    Write-disk recognition: ``form=files`` / ``form=workspace`` / non-empty
    ``artifacts``. Omitted form on a parsed Deliverable is ``files``.
    """
    from agentcore.runtime.runs.types import deliverable_expects_landing

    return deliverable_expects_landing(deliverable)


def is_short_write_posture(*, max_rounds: int | None) -> bool:
    """True when a short round budget is stamped (CEO explicit).

    ``complexity_hint=light`` no longer stamps ``max_rounds``. CEO-declared
    caps still can. Standard workers leave ``max_rounds=None`` (profile default)
    — not short-write posture.
    """
    return max_rounds is not None and max_rounds > 0


def should_tighten_verify_exec_thrash(
    *,
    short_write_posture: bool,
    files_expected: bool,
    has_execution_tools: bool,
) -> bool:
    """Repair verify short posture: tighten unproductive / tool-failure ladders.

    Applies when the worker is short-budget (CEO stamped max_rounds),
    holds execution tools, and is **not** a files-landing node (verify /
    prose). Reuses LoopController repeated-failure / circuit-breaker / unproductive
    paths — does **not** add a parallel fuse. Files short-write nodes skip this
    verify tighten path (delivery pressure stays on round/token ceilings).
    """
    return bool(
        short_write_posture and has_execution_tools and not files_expected
    )


def is_directed_search_role(role: str) -> bool:
    """True when the role should get grep/code_search + 检索纪律（审查 / 调查类）.

    Covers 审查官 / 质检 / 调研员 / 审校 etc. Does **not** change token or timeout
    backstop; only tool-surface enrichment and prompt discipline.
    """
    r = (role or "").strip().lower()
    if not r:
        return False
    return any(marker in r for marker in _DIRECTED_SEARCH_ROLE_MARKERS)


def is_outer_verify_role(role: str) -> bool:
    """True when the role is acceptance / outer-loop verify (do not auto-stamp inner)."""
    r = (role or "").strip().lower()
    if not r:
        return False
    return any(marker in r for marker in _OUTER_VERIFY_ROLE_MARKERS)


def apply_verify_policies(plan: RunPlan) -> None:
    """Stamp ``verify_policy=inner`` on review/investigation seats (unless explicit)."""
    apply_verify_policies_to_specs(plan.nodes)


def apply_verify_policies_to_specs(specs: list[RunSpec]) -> None:
    """Same as :func:`apply_verify_policies` for a replan ``add`` batch."""
    for spec in specs:
        raw = (spec.verify_policy or "").strip().lower()
        if raw in (VERIFY_POLICY_INNER, VERIFY_POLICY_OUTER):
            spec.verify_policy = raw
            continue
        if is_outer_verify_role(spec.role):
            continue
        if is_directed_search_role(spec.role):
            spec.verify_policy = VERIFY_POLICY_INNER


def ensure_directed_search_tools(
    tools: list[str] | None,
    *,
    role: str,
    valid_tools: set[str] | None,
) -> list[str] | None:
    """Ensure review/investigation allow-lists include grep/code_search when available.

    ``None`` (unrestricted) is left alone — the worker registry already offers them.
    Explicit least-privilege lists that omit directed search get them appended when
    present in ``valid_tools`` (or unconditionally when ``valid_tools`` is unknown).
    """
    if not is_directed_search_role(role):
        return tools
    extras = [
        name
        for name in sorted(DIRECTED_SEARCH_TOOL_NAMES)
        if valid_tools is None or name in valid_tools
    ]
    if not extras:
        return tools
    if tools is None:
        return None
    merged = list(tools)
    for name in extras:
        if name not in merged:
            merged.append(name)
    return merged


def apply_directed_search_tools(
    plan: RunPlan,
    *,
    valid_tools: set[str] | None = None,
) -> None:
    """Stamp directed-search tools onto review/investigation nodes (in place)."""
    apply_directed_search_tools_to_specs(plan.nodes, valid_tools=valid_tools)


def apply_directed_search_tools_to_specs(
    specs: list[RunSpec],
    *,
    valid_tools: set[str] | None = None,
) -> None:
    """Same as :func:`apply_directed_search_tools` for a replan ``add`` batch."""
    for spec in specs:
        spec.tools = ensure_directed_search_tools(
            spec.tools, role=spec.role, valid_tools=valid_tools
        )


def apply_worker_budgets(
    plan: RunPlan,
    *,
    default_token_ceiling: int | None = None,
) -> None:
    """Stamp unified ``token_ceiling`` / ``policy.timeout_s`` backstop on every node."""
    apply_worker_budgets_to_specs(
        plan.nodes,
        default_token_ceiling=default_token_ceiling,
    )


def apply_worker_budgets_to_specs(
    specs: list[RunSpec],
    *,
    default_token_ceiling: int | None = None,
) -> None:
    """Same as :func:`apply_worker_budgets` for a replan ``add`` batch."""
    ceiling = (
        default_token_ceiling
        if default_token_ceiling is not None and default_token_ceiling > 0
        else _settings_default_token_ceiling()
    )
    for spec in specs:
        if spec.token_ceiling is None:
            spec.token_ceiling = ceiling
        # CEO 显式 timeout_ms → builder 已写入 timeout_s；未声明时填统一 backstop。
        if spec.policy.timeout_s is None:
            spec.policy.timeout_s = WORKER_TIMEOUT_BACKSTOP_S


def _settings_default_token_ceiling() -> int:
    try:
        from agentcore.config import settings

        ceiling = int(settings.engine_worker_token_ceiling)
        if ceiling > 0:
            return ceiling
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        pass
    return 4_000_000
