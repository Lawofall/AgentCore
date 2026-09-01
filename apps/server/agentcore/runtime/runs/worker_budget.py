"""派单时为 worker 回填统一 token 顶。

全局 ``engine_worker_token_ceiling``（默认 8M，按 fuse_tokens 计）是防失控安全阀，
**不做**按任务规格的四档启发式分档。预置 ``token_ceiling`` 恒优先（已写入则不动）。
工人寿命墙钟无产品默认；仅 CEO 显式 ``timeout_ms`` 写入 ``policy.timeout_s`` 才武装。

共享谓词（``is_deep_deliverable`` 等）与本模块的统一 token 回填正交。
``verify_policy`` 只规范化 CEO 显式的 inner/outer，不按角色名猜测。
检索 HOW 只写在 grep / code_search / file_read 工具说明，不按职称灌纪律。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import Deliverable, RunSpec

__all__ = [
    "VERIFY_POLICY_INNER",
    "VERIFY_POLICY_OUTER",
    "apply_verify_policies",
    "apply_verify_policies_to_specs",
    "apply_worker_budgets",
    "apply_worker_budgets_to_specs",
    "is_deep_deliverable",
    "is_short_write_posture",
    "should_tighten_verify_exec_thrash",
]

# verify_policy：只规范化显式 inner / outer；不按角色名打标。
VERIFY_POLICY_INNER = "inner"
VERIFY_POLICY_OUTER = "outer"


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


def apply_verify_policies(plan: RunPlan) -> None:
    """Normalise explicit ``inner`` / ``outer`` only — do not guess from role names."""
    apply_verify_policies_to_specs(plan.nodes)


def apply_verify_policies_to_specs(specs: list[RunSpec]) -> None:
    """Same as :func:`apply_verify_policies` for a replan ``add`` batch."""
    for spec in specs:
        raw = (spec.verify_policy or "").strip().lower()
        if raw in (VERIFY_POLICY_INNER, VERIFY_POLICY_OUTER):
            spec.verify_policy = raw


def apply_worker_budgets(
    plan: RunPlan,
    *,
    default_token_ceiling: int | None = None,
) -> None:
    """Stamp unified ``token_ceiling`` backstop on every node."""
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
        # timeout_s：只保留 builder 写入的 CEO 显式 timeout_ms；缺省不填、不武装。


def _settings_default_token_ceiling() -> int:
    try:
        from agentcore.config import settings

        ceiling = int(settings.engine_worker_token_ceiling)
        if ceiling > 0:
            return ceiling
    except Exception:  # noqa: BLE001 — settings optional in unit stubs
        pass
    return 8_000_000
