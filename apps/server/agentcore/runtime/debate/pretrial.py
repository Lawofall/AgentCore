"""庭前取证（辩论编排设计.md §二之二）。

开赛后、首轮立论前的固有阶段：

1. **共享证据包优先**（附件已在主持人上下文）→ 组装 Evidence Pack
   - ``completeness=full``：不开外证扫网；辩手发言期 ``retrieval_budget=0``
   - ``partial``/``empty``：直接完成庭前；辩手对称有界发言期检索
2. **无可用 pack**：直接完成庭前；认真档各方对称有界发言期检索
3. ``thorough=False``：秒过（fast）

边界（就地否决）：
- 庭前调查员舰队已删除（点单 / 代派 / gap_fill 补跑）
- 预算对称；台账强制汇流（Evidence Pack 登记）
- 禁止以同批去重 / retry 冒充「共享事实库」改造
- 外证是否开由完整度驱动（产品约束）；发言期预算由完整度对称分配
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from agentcore.runtime.debate.evidence_pack import ExternalEvidencePlan
    from agentcore.runtime.debate.moderator_common import CompleteJson
    from agentcore.runtime.debate.types import DebateConfig
    from agentcore.tools.builtin.debate.tool import DebateTool

logger = get_logger(__name__)

SkipReason = Literal["", "fast", "evidence_pack", "no_pack"]
PackCompleteness = Literal["full", "partial", "empty"]


@dataclass
class PretrialResult:
    skipped: bool = False
    skip_reason: SkipReason = ""
    orders: list[Any] = field(default_factory=list)
    fallback_self_search: bool = False
    evidence_ready: bool = False
    debater_run_ids: dict[str, str] = field(default_factory=dict)
    evidence_pack: Any | None = None
    # 取证完整度一等公民：失败 / 截断不得伪装成满分完成。
    completeness: PackCompleteness = "empty"
    # 外证计划观测（mode 恒为 skip）。
    external_evidence_mode: str = ""
    external_evidence_reason: str = ""

    @property
    def incomplete(self) -> bool:
        # intentional 秒过（fast / evidence_pack / no_pack）不得标 incomplete；
        # 完整度仍写入 completeness，供发言期预算与约定文档标注使用。
        if self.skipped:
            return False
        return self.completeness != "full"

    def to_completed_payload(self) -> dict[str, Any]:
        if self.skipped:
            status = "skipped"
        elif self.fallback_self_search or self.incomplete:
            status = "degraded"
        else:
            status = "done"
        pack = self.evidence_pack
        return {
            "status": status,
            "skip_reason": self.skip_reason or None,
            "orders": list(self.orders),
            "fallback_self_search": self.fallback_self_search,
            "evidence_ready": self.evidence_ready,
            "evidence_pack": pack.to_wire() if pack is not None else None,
            "completeness": self.completeness,
            "incomplete": self.incomplete,
            "external_evidence_mode": self.external_evidence_mode or None,
            "external_evidence_reason": self.external_evidence_reason or None,
        }


def _apply_debater_budgets(
    config: DebateConfig,
    *,
    completeness: PackCompleteness,
) -> None:
    from agentcore.runtime.debate.evidence_pack import debater_budgets_from_completeness

    config.debater_retrieval_budgets = debater_budgets_from_completeness(
        side_keys=[s.key for s in config.sides],
        completeness=completeness,
    )


def _log_external_plan(plan: ExternalEvidencePlan, *, path: str) -> None:
    logger.info(
        "debate.pretrial.external_evidence_plan",
        path=path,
        mode=plan.mode,
        reason=plan.reason,
        retrieval_budget=plan.retrieval_budget,
        sides=list(plan.sides),
        allow_read_url=plan.allow_read_url,
        max_tasks_per_side=plan.max_tasks_per_side,
        allow_external=plan.allow_external,
    )
    if not plan.allow_external:
        logger.info(
            "debate.pretrial.external_evidence_skipped",
            path=path,
            reason=plan.reason,
            completeness_driven=True,
        )


def _maybe_write_incomplete_notice(
    config: DebateConfig,
    *,
    completeness: PackCompleteness,
    path: str,
) -> None:
    if completeness == "full":
        return
    from agentcore.runtime.debate.evidence_pack import format_evidence_completeness_notice

    notice = format_evidence_completeness_notice(
        completeness=completeness,
        path=path,
    )
    if not notice:
        return
    prev = (config.research_dossier_index or "").strip()
    config.research_dossier_index = f"{notice}\n{prev}" if prev else notice
    logger.warning(
        "debate.pretrial.evidence_incomplete",
        path=path,
        completeness=completeness,
    )


async def run_pretrial_phase(
    tool: DebateTool,
    *,
    execution_id: str,
    moderator_run_id: str,
    config: DebateConfig,
    complete_json: CompleteJson,
    on_started: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_orders: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_completed: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> PretrialResult:
    """庭前阶段编排入口（无调查员 spawn）。"""
    del complete_json  # 点单 LLM 不再使用
    from agentcore.runtime.debate.evidence_pack import resolve_external_evidence_plan

    base_payload = {
        "execution_id": execution_id,
        "moderator_run_id": moderator_run_id,
        "thorough": bool(config.policy.thorough),
        "sides": [{"key": s.key, "name": s.name} for s in config.sides],
    }

    # 快速档：秒过
    if not config.policy.thorough:
        plan = resolve_external_evidence_plan(completeness="empty", path="fast")
        _log_external_plan(plan, path="fast")
        result = PretrialResult(
            skipped=True,
            skip_reason="fast",
            completeness="empty",
            external_evidence_mode=plan.mode,
            external_evidence_reason=plan.reason,
        )
        config.external_evidence_mode = plan.mode
        config.external_evidence_reason = plan.reason
        if on_started is not None:
            await on_started({**base_payload, "skip_reason": "fast"})
        if on_completed is not None:
            await on_completed({**base_payload, **result.to_completed_payload()})
        return result

    if on_started is not None:
        await on_started(base_payload)

    pack_result = await _try_evidence_pack_path(
        tool,
        execution_id=execution_id,
        moderator_run_id=moderator_run_id,
        config=config,
        base_payload=base_payload,
        on_orders=on_orders,
        on_completed=on_completed,
    )
    if pack_result is not None:
        return pack_result

    # 无可用 pack：直接完成；认真档对称有界发言期检索。
    plan = resolve_external_evidence_plan(completeness="empty", path="no_pack")
    _log_external_plan(plan, path="no_pack")
    config.evidence_completeness = "empty"
    config.external_evidence_mode = plan.mode
    config.external_evidence_reason = plan.reason
    _apply_debater_budgets(config, completeness="empty")
    _maybe_write_incomplete_notice(config, completeness="empty", path="no_pack")
    result = PretrialResult(
        skipped=True,
        skip_reason="no_pack",
        completeness="empty",
        external_evidence_mode=plan.mode,
        external_evidence_reason=plan.reason,
    )
    if on_orders is not None:
        await on_orders(
            {
                **base_payload,
                "orders": [],
                "completeness": "empty",
                "incomplete": True,
                "external_evidence": plan.to_wire(),
            }
        )
    if on_completed is not None:
        await on_completed(
            {
                **base_payload,
                **result.to_completed_payload(),
                "evidence_ledger_count": len(tool._evidence_ledger),
                "evidence_ledger_delta": tool._evidence_ledger.drain_delta(),
            }
        )
    return result


async def _try_evidence_pack_path(
    tool: DebateTool,
    *,
    execution_id: str,
    moderator_run_id: str,
    config: DebateConfig,
    base_payload: Mapping[str, Any],
    on_orders: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_completed: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> PretrialResult | None:
    """附件已在主持人上下文 → 组装共享证据包。

    无可用正文 → ``None``（回落 no_pack）。
    """
    del execution_id, moderator_run_id
    from agentcore.runtime.debate.evidence_pack import (
        assemble_evidence_pack_from_host,
        merge_pack_into_dossier_index,
        register_evidence_pack_on_ledger,
        resolve_external_evidence_plan,
    )

    pack = assemble_evidence_pack_from_host(
        system_prompt=getattr(tool, "_system_prompt", "") or "",
        motion=config.motion,
        sides=config.sides,
        background=config.background,
    )
    if pack is None or not pack.has_usable_body():
        return None

    register_evidence_pack_on_ledger(tool._evidence_ledger, pack)
    config.evidence_pack = pack
    config.research_dossier_index = merge_pack_into_dossier_index(
        config.research_dossier_index, pack
    )
    config.pretrial_evidence_ready = True

    plan = resolve_external_evidence_plan(
        completeness=pack.completeness,
        path="evidence_pack",
    )
    _log_external_plan(plan, path="evidence_pack")
    config.external_evidence_mode = plan.mode
    config.external_evidence_reason = plan.reason
    config.evidence_completeness = pack.completeness
    _apply_debater_budgets(config, completeness=pack.completeness)

    logger.info(
        "debate.pretrial.evidence_pack_assembled",
        sources=len(pack.sources),
        usable=sum(
            1
            for s in pack.sources
            if (s.excerpt or "").strip() and s.failure not in ("binary_no_text", "empty_body")
        ),
        completeness=pack.completeness,
        incomplete=pack.completeness != "full",
        external_mode=plan.mode,
        external_reason=plan.reason,
    )

    if pack.completeness != "full":
        _maybe_write_incomplete_notice(
            config,
            completeness=pack.completeness,
            path="evidence_pack",
        )

    result = PretrialResult(
        skipped=True,
        skip_reason="evidence_pack",
        evidence_ready=True,
        evidence_pack=pack,
        completeness=pack.completeness,
        external_evidence_mode=plan.mode,
        external_evidence_reason=plan.reason,
    )
    if on_orders is not None:
        await on_orders(
            {
                **base_payload,
                "orders": [],
                "evidence_pack": pack.to_wire(),
                "path": "evidence_pack",
                "completeness": pack.completeness,
                "incomplete": pack.completeness != "full",
                "external_evidence": plan.to_wire(),
            }
        )
    if on_completed is not None:
        await on_completed(
            {
                **base_payload,
                **result.to_completed_payload(),
                "evidence_ledger_count": len(tool._evidence_ledger),
                "evidence_ledger_delta": tool._evidence_ledger.drain_delta(),
            }
        )
    return result
