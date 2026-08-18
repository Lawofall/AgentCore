"""交付状态结构化（能力闸门与交付诚实性）：delegate 批次收尾的确定性交付对账。

把收尾侧引擎已有的信号——路径级验收（``file_acceptance``）、契约 / 交接缺口
(:func:`~agentcore.runtime.delegate.completion.collect_worker_gaps`，含 degraded
交接与 artifacts 对账残差)、失败 / 未执行
节点——汇成一条 ``delivery_status`` 事件（已交付文件 / 缺口 / 待操作元数据 /
``artifacts`` 验收行），模板拼接、不调 LLM。事件继续发射，供产物清单与
``finish_guard``；**用户面**已否决验收大卡——桌面/手机仅
``delivered``/``notes`` 静默、``partial``/``blocked`` 一句轻提示。

``delivered_files`` / CEO「已交付」= 仅 ``accepted``；cite-tier 等合同点名路径为
``rejected``，不得因 soft-COMPLETED 进入 delivered_files。主清单（桌面
FileArtifactsCard）认 ``artifacts``（accepted+rejected）。COMPLETED 只走
``file_acceptance``，不从 ``files_touched`` 合成验收行；FAILED 且未盖戳时，从
``files_touched`` / transcript 自报补入（与 ``product_landed`` 同源——失败前已落盘
但没走完正式交付声明的产物仍计入交付账）。每行随带工具自报的 ``kind`` /
``derived_from``（导出件 ← 源 md），客户端据此把源折成中间稿，口径同
``fold_exported_sources``。blocked = 纯失败无文件；partial = 有落盘有缺口。

刀1 / 方案 A：声明路径已落盘 → verdict 走交付成功路径；``degraded_handoff`` 仅
notes/warning 备注，不整单硬失败、不拖文件 rejected。甲⁺：真无落盘 soft
（``files_not_landed`` → notes），不挡整批收工 /
CEO finish；写盘通道挂仍可在备注里诚实归因。
同图已有 continue_from / replaces 补派已跑时，收掉并排「计划收口时跳过」。

用户面零落盘缺口投影为 ``files_not_landed`` soft（甲⁺：warning/notes，不挡整批）：
有队员归因时按角色保留「本队员本波未交卷」（定案 B）；仅批次谓词时仍可落「本批未见落盘」。
发射时写入回合 :data:`current_delivery_verdict` **以及** ``promotion_ledger.delivery_verdict``
（跨 Task 共享槽，与成品归位台账同一对象）。只写 ContextVar 时，后台
``asyncio.create_task(_background_drive)`` 的 ``set`` 到不了 CEO 父任务的
``finish_guard``。禁止改去查 turn_journal。

文献成文（``research_report`` / 同等成文综述）：证据不足时注入
``reason=evidence_deficit`` blocking gap → state 不得 ``delivered``（见
``research_quality.collect_evidence_deficit_gaps``）；消费搜索真源
``evidence_gap`` + ``search_policy=academic_literature``（兼容旧
``evidence_deficit`` 戳）；不扫完成话术词，不套 ``parallel_brief``。

已声明复核落盘（``form=files`` + ``reviews/``）：声明路径未 accepted / 拒收 /
空壳 → ``reason=thin_review`` blocking（见
``research_quality.collect_thin_review_gaps``）；不扫角色名；有合格报告则短
handoff 不硬降档。``requires_draft_ack`` 扩至 ``evidence_deficit`` /
``thin_review`` / ``verify_failed`` / ``node_failed`` / ``artifact_rejected``
（契约硬失败·节点 FAILED·拒收产物同 thin_review 闩；正向缺口承认，不扩姿势 A 词表）。

挂在 drive 的各收尾路径旁路（正常终态 / 验收未满足 / 部分失败 stash / replan(stop)），
永不抛错；纯 prose 成功批次（无落盘文件、无缺口）保持无声，不发事件。
折叠语义：同 ``execution_id`` 保最新——反映最近一批委派的对账（多批场景下 FileArtifactsCard
仍是全量文件清单，本事件承载「诚实对账」而非全量枚举）。finalize 终态发射幂等：同一
``execution_id`` 同一结论只发一次；结论变了（补跑覆盖）仍发。

严重度：``severity=warning``（示例/虚构自注 / 交接备注等）不单独撑起
partial/blocked。轻 B：无 blocking 且 warnings **除去** ``unverified_note`` 后为空、
且已有 ``delivered_files`` → state=``delivered``（gaps 仍可保留 soft 行）；其余
soft reason → 仍 ``notes``。声明路径 vs 实际落盘是纯字符串比对：失配为
``path_mismatch`` blocking（不得 accepted / 不得进 ``delivered_files`` /
state 不得 ``delivered``），不再当 ``path_hint`` 路径建议。blocking 缺口才标
「部分未满足 / 未满足」。成篇未写完改由对话框接着说——不再发
``continue_writing`` 一键按钮。
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from agentcore.core.logging import get_logger
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import RunPhase, RunState
from agentcore.runtime.turn.token_budget import REASON_QA_DEFERRED, REASON_TURN_TOKEN_BUDGET

if TYPE_CHECKING:
    from agentcore.tools.protocol import TurnPromotionLedger

logger = get_logger(__name__)

_MAX_FILES = 24
_MAX_GAPS = 12

REASON_UNVERIFIED_NOTE = "unverified_note"
REASON_PATH_HINT = "path_hint"
REASON_PATH_MISMATCH = "path_mismatch"
REASON_FILES_NOT_LANDED = "files_not_landed"
# No-exec worker shipped a table file while this turn had an opaque source
# data file (attachment / workspace type signal — not body scan).
REASON_NO_EXEC_TABLE = "no_exec_table"
# Verify-shaped tool failure (browser_navigate / test_run / verify 形 code_execute·terminal).
REASON_VERIFY_FAILED = "verify_failed"
# test_run verify-budget incomplete（进程已中止，非仍在跑）.
REASON_VERIFY_BUDGET = "verify_budget"
# Literature-report evidence deficit (research_quality.REASON_EVIDENCE_DEFICIT).
REASON_EVIDENCE_DEFICIT = "evidence_deficit"
# Declared reviews/ report missing / rejected / shell (research_quality.REASON_THIN_REVIEW).
REASON_THIN_REVIEW = "thin_review"
# Plan node terminal FAILED (incl. contract.failed → RunPhase.FAILED).
REASON_NODE_FAILED = "node_failed"
# Path-level file_acceptance rejected (cite-tier / FAILED landings / …).
REASON_ARTIFACT_REJECTED = "artifact_rejected"
# Unresolved write-ownership collision (closing_posture P0-B · structured only).
REASON_WRITE_OWNERSHIP = "write_ownership_conflict"
# Keep in sync with runtime.runs.cutoff.REASON_DEGRADED_HANDOFF (wire gap reason).
REASON_DEGRADED_HANDOFF = "degraded_handoff"
# B1：超席/空交接风暴；cancel 且零落盘须缺口清单（draft_ack）。
REASON_EMPTY_HANDOFF_STORM = "empty_handoff_storm"
REASON_CANCELLED = "cancelled"
REASON_OVER_SEAT = "over_seat"
_WRITING_CUTOFF_REASONS = frozenset({"token_budget", "worker_timeout"})
_SOFT_GAP_REASONS = frozenset({REASON_UNVERIFIED_NOTE, REASON_FILES_NOT_LANDED})
# Gaps that latch finish_guard draft acknowledgment (扩出文献 evidence_deficit /
# 能力4：契约硬失败 / 节点 FAILED / rejected 产物 —— 不扩姿势 A 词表).
_DRAFT_ACK_GAP_REASONS = frozenset(
    {
        REASON_EVIDENCE_DEFICIT,
        REASON_THIN_REVIEW,
        REASON_VERIFY_FAILED,
        REASON_VERIFY_BUDGET,
        REASON_NODE_FAILED,
        REASON_ARTIFACT_REJECTED,
        REASON_EMPTY_HANDOFF_STORM,
        REASON_CANCELLED,
        REASON_OVER_SEAT,
    }
)
# 刀1：有落盘时 degraded_handoff 并入 soft（见 _soften_landed_degraded_gaps）。
_PLAN_CUTOFF_SKIP_DESC = "未执行（计划收口时跳过）"

# Per-worker contract + batch files_written criteria share this predicate.
_ZERO_LANDING_MARKERS = (
    "本队员本波未交卷",
    "未把产物写入工作区",
    "尚无 worker 将产物写入工作区",
    "本批未见落盘",
)
_BATCH_ACCEPTANCE_ROLE = "验收"


@dataclass(frozen=True)
class DeliveryVerdict:
    """Turn-scoped delivery reconciliation for CEO finish_guard (not a wire payload)."""

    state: str
    delivered_files: tuple[str, ...]
    execution_id: str
    # True when gaps include evidence_deficit / thin_review / verify_failed /
    # node_failed / artifact_rejected (draft / gap acknowledgment required).
    requires_draft_ack: bool = False
    # Structured gap reasons from the same delivery_status payload (no prose).
    # Shadow honesty logs read this; path reconciliation does not.
    gap_reasons: tuple[str, ...] = ()


def _gaps_require_draft_ack(gaps: list[Any] | tuple[Any, ...] | None) -> bool:
    """True when any gap reason latches requires_draft_ack."""
    return any(
        isinstance(g, dict) and str(g.get("reason") or "") in _DRAFT_ACK_GAP_REASONS
        for g in (gaps or [])
    )


def acceptance_counts(
    results: dict[str, RunState],
    plan: RunPlan | None = None,
) -> tuple[int, int]:
    """Path-deduped ``(accepted, rejected)`` from the delivery ledger — synthesis 同源."""
    arts = _collect_artifacts(results, plan)
    accepted = sum(1 for a in arts if a.get("status") == "accepted")
    rejected = sum(1 for a in arts if a.get("status") == "rejected")
    return accepted, rejected


current_delivery_verdict: ContextVar[DeliveryVerdict | None] = ContextVar(
    "current_delivery_verdict", default=None
)


def _gap_reasons_from(gaps: list[Any] | tuple[Any, ...] | None) -> tuple[str, ...]:
    """Extract non-empty gap reasons; never includes gap descriptions / prose."""
    reasons: list[str] = []
    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        reason = str(gap.get("reason") or "").strip()
        if reason:
            reasons.append(reason)
    return tuple(reasons)


def bind_delivery_verdict(
    verdict: DeliveryVerdict | None,
    *,
    promotion_ledger: TurnPromotionLedger | None = None,
) -> None:
    """Stamp the turn verdict on ContextVar (same-task) and the shared ledger slot.

    ``asyncio.create_task`` copies Context: a child ``ContextVar.set`` is invisible
    to the CEO parent. ``TurnPromotionLedger.delivery_verdict`` is the same object
    the parent already holds (``dataclasses.replace`` shallow-copy), matching
    ``promotion_ledger`` itself.
    """
    current_delivery_verdict.set(verdict)
    if promotion_ledger is not None:
        promotion_ledger.delivery_verdict = verdict


def read_delivery_verdict(
    *,
    promotion_ledger: TurnPromotionLedger | None = None,
) -> DeliveryVerdict | None:
    """Prefer the shared ledger slot when the caller has one; else ContextVar."""
    if promotion_ledger is not None:
        return promotion_ledger.delivery_verdict
    return current_delivery_verdict.get()


# sink → {execution_id: fingerprint} — same sink + same conclusion → skip re-emit.
_emitted_delivery_fp: WeakKeyDictionary[Any, dict[str, str]] = WeakKeyDictionary()

# Soft reminder copy markers (placeholder soft + length/keyword soft · 定案乙).
_SOFT_REMINDER_MARKERS = (
    "不阻断验收",
    "未核实/示例自注",
    "待核实/示例自注",
    "示例/虚构自注",
    "含未替换骨架占位",
    "篇幅提醒（软）",
    "素材覆盖提醒（软）",
    "契约软提醒",
)
# Contract path-reconciliation copy (artifacts / artifact_dir). Delivery card
# treats these as path_mismatch blocking — not notes / 路径建议.
_PATH_MISMATCH_MARKERS = (
    "产物未写入约定文档目录",
    "声明的交付物路径未落盘",
)

# build_website task books embed ``站点【…】`` — reuse for verify-action prompt.
_SITE_BRACKET_RE = re.compile(r"站点【([^】]+)】")
# 「不阻断验收，3 处」/「自注（3 处）」/ skeleton soft / legacy soft copy.
_SOFT_HIT_COUNT_RE = re.compile(r"(?:不阻断验收，|自注（)(\d+)\s*处")
_SOFT_PATH_RE = re.compile(r"`([^`]+)`\s*·")


def _infer_website_site(plan: RunPlan) -> str:
    """Best-effort site label from plan task text (empty when unknown)."""
    for node in plan.nodes:
        task = getattr(node, "task", None)
        if not isinstance(task, str) or not task:
            continue
        match = _SITE_BRACKET_RE.search(task)
        if match:
            site = match.group(1).strip()
            if site:
                return site
    return ""


def _continue_skipped_runs_action(roles: list[str]) -> dict[str, str]:
    """CTA when nodes were SKIPPED for turn/nested token budget — not 成篇续写."""
    named = "、".join(roles[:6]) if roles else "未跑节点"
    extra = f"等 {len(roles)} 个角色" if len(roles) > 6 else named
    return {
        "kind": "continue_skipped_runs",
        "description": (
            f"因额度未跑（{extra}）——点此下一回合续跑未执行节点，禁止假装本回合已全部完成"
        ),
        "prompt": (
            "请续跑上一回合因 token 额度跳过、从未开跑的节点："
            f"点名补跑 {named}"
            "；优先 append 同一协作图或 replan/点名角色，"
            "不要另开无关大派，不要把部分完成说成全部交付。"
        ),
    }


def _website_verify_action(site: str) -> dict[str, str]:
    """Structured second-act CTA when whole-page QA deferred for budget."""
    topic_arg = f'topic="{site}"' if site else 'topic="<站点简述>"'
    prompt = (
        "请对本站做第二段整页验收：delegate 时用 playbook=build_website_verify，"
        f"playbook_args 填 {topic_arg}。"
        "工作区已有 site/ 产物，只跑整页/视觉 QA，勿重做文案、骨架或分区。"
    )
    return {
        "kind": "website_verify",
        "description": (
            "整页验收因预算推迟——点此续派页面 QA（不重建站，只用 build_website_verify）"
        ),
        "prompt": prompt,
    }


def _delivered_files(
    results: dict[str, RunState],
    plan: RunPlan | None = None,
) -> list[str]:
    """Ordered, deduped accepted paths from the delivery ledger (stamp or FAILED backfill)."""
    return [a["path"] for a in _collect_artifacts(results, plan) if a["status"] == "accepted"][
        :_MAX_FILES
    ]


def _product_meta(raw: dict[str, Any]) -> dict[str, str]:
    """The producer's self-reported ``kind`` / ``derived_from`` (empty when unreported).

    Wire-side twin of the ledger fields ``build_file_acceptance`` stamps: ``kind``
    lets the client label the product, ``derived_from`` lets it fold the source into
    中间稿 (口径同 ``fold_exported_sources``). Guessing either from the extension is
    exactly what the ledger redesign removed, so unreported stays unreported.
    """
    out: dict[str, str] = {}
    kind = str(raw.get("kind") or "").strip()
    if kind:
        out["kind"] = kind
    source = str(raw.get("derived_from") or "").strip()
    if source:
        out["derived_from"] = source
    return out


def _undeclared_failed_acceptance(state: RunState) -> list[dict[str, Any]]:
    """FAILED worker landed files but never stamped ``file_acceptance``.

    Exception-path failures freeze the transcript (``product_landed=true``) without
    building acceptance rows. Those successful writes still count in the delivery
    ledger; the gap is the failed node, not path rejection. COMPLETED stays silent
    without a stamp (no ``files_touched`` synthesis).
    """
    if state.phase is not RunPhase.FAILED:
        return []
    if state.file_acceptance:
        return []
    from agentcore.runtime.runs.file_acceptance import build_file_acceptance
    from agentcore.runtime.runs.serialize import file_products_from_transcript

    products = file_products_from_transcript(state.transcript or [])
    paths: list[str] = []
    seen: set[str] = set()
    for path in state.files_touched or []:
        text = str(path or "").strip()
        if text and text not in seen:
            seen.add(text)
            paths.append(text)
    for product in products:
        text = str(product.path or "").strip()
        if text and text not in seen:
            seen.add(text)
            paths.append(text)
    if not paths:
        return []
    # Writes already succeeded — accept the paths; node_failed still blocks delivered.
    return build_file_acceptance(paths, phase=RunPhase.COMPLETED, products=products)


def _acceptance_rows_for_state(state: RunState) -> list[dict[str, Any]]:
    """Stamped ``file_acceptance``, or FAILED undeclared landings."""
    stamped = [row for row in (state.file_acceptance or []) if isinstance(row, dict)]
    if stamped:
        return stamped
    return _undeclared_failed_acceptance(state)


def _collect_artifacts(
    results: dict[str, RunState],
    plan: RunPlan | None = None,
) -> list[dict[str, Any]]:
    """Aggregate acceptance rows across workers (dedupe by path, last wins).

    Empty ``file_acceptance`` on COMPLETED → no artifact rows (no ``files_touched``
    synthesis). FAILED with landed files and no stamp → backfill from
    ``files_touched`` / transcript self-report (same facts as ``product_landed``).
    When ``plan`` is given, stamp ``workspace_id=folder:{target_folder_id}`` from
    the matching node (omit when the node has no target — client falls back to
    session birth desk). Does not rewrite session ``folder_id``.
    Self-reported ``kind`` / ``derived_from`` ride along so the client's 主清单 can
    show the export and fold its source (see :func:`_product_meta`).
    """
    from agentcore.runtime.runs.file_acceptance import (
        apply_declared_path_acceptance,
        normalize_acceptance_row,
    )
    from agentcore.workspace.locate import format_workspace_id

    desk_by_run: dict[str, str] = {}
    deliverable_by_run: dict[str, Any] = {}
    if plan is not None:
        for node in plan.nodes:
            rid = str(getattr(node, "run_id", "") or "").strip()
            if not rid:
                continue
            tf = getattr(node, "target_folder_id", None)
            tf_s = str(tf).strip() if tf else ""
            if tf_s:
                desk_by_run[rid] = format_workspace_id(folder_id=tf_s, conversation_id="")
            deliverable = getattr(node, "deliverable", None)
            if deliverable is not None:
                deliverable_by_run[rid] = deliverable

    by_path: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for run_id, state in results.items():
        if state is None:
            continue
        workspace_id = desk_by_run.get(str(run_id))
        deliverable = deliverable_by_run.get(str(run_id))
        for raw in _acceptance_rows_for_state(state):
            row = normalize_acceptance_row(raw)
            if row is None:
                continue
            if deliverable is not None:
                row = apply_declared_path_acceptance(
                    row,
                    artifacts=getattr(deliverable, "artifacts", None),
                    artifact_dir=str(getattr(deliverable, "artifact_dir", "") or ""),
                )
            if workspace_id:
                row = {**row, "workspace_id": workspace_id}
            meta = _product_meta(raw)
            if meta:
                row = {**row, **meta}
            path = row["path"]
            if path not in by_path:
                order.append(path)
            by_path[path] = row
    return [by_path[p] for p in order][:_MAX_FILES]


def _artifact_rejected_gaps(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structured gaps for path-level rejected artifacts (能力4 · draft_ack 闩).

    One summary row — does not scan prose; truth = ``file_acceptance`` rejected.
    """
    rejected = [
        a
        for a in artifacts
        if isinstance(a, dict) and a.get("status") == "rejected" and a.get("path")
    ]
    if not rejected:
        return []
    paths = [str(a["path"]).strip() for a in rejected if str(a.get("path") or "").strip()]
    if not paths:
        return []
    shown = "、".join(f"`{p}`" for p in paths[:6])
    more = f" 等 {len(paths)} 个" if len(paths) > 6 else ""
    return [
        _annotate_gap(
            "验收",
            f"产物路径未核：{shown}{more}",
            reason=REASON_ARTIFACT_REJECTED,
        )
    ]


def _path_declaration_of(node: Any) -> tuple[list[str], str]:
    """Return ``(artifacts, artifact_dir)`` from a plan node; empty = no constraint."""
    deliverable = getattr(node, "deliverable", None)
    if deliverable is None:
        return [], ""
    artifacts = [
        str(a).strip() for a in (getattr(deliverable, "artifacts", None) or []) if str(a).strip()
    ]
    artifact_dir = str(getattr(deliverable, "artifact_dir", "") or "").strip()
    return artifacts, artifact_dir


def _declared_path_mismatch_gaps(
    plan: RunPlan,
    results: dict[str, RunState],
) -> list[dict[str, Any]]:
    """Blocking gaps when declared artifacts / artifact_dir have no accepted match."""
    from agentcore.runtime.runs.file_acceptance import (
        apply_declared_path_acceptance,
        declaration_allows_landed,
        landed_matches_declared,
        normalize_acceptance_row,
    )

    gaps: list[dict[str, Any]] = []
    for node in plan.nodes:
        artifacts, artifact_dir = _path_declaration_of(node)
        if not artifacts and not artifact_dir:
            continue
        state = results.get(node.run_id)
        if state is None:
            continue
        accepted: list[str] = []
        for raw in _acceptance_rows_for_state(state):
            row = normalize_acceptance_row(raw)
            if row is None:
                continue
            row = apply_declared_path_acceptance(
                row, artifacts=artifacts, artifact_dir=artifact_dir
            )
            if row.get("status") == "accepted" and row.get("path"):
                accepted.append(str(row["path"]))
        missing: list[str] = []
        if artifacts:
            missing = [
                pat
                for pat in artifacts
                if not any(landed_matches_declared(p, pat) for p in accepted)
            ]
        elif not any(
            declaration_allows_landed(p, artifacts=[], artifact_dir=artifact_dir) for p in accepted
        ):
            missing = [f"{artifact_dir.rstrip('/')}/"]
        if not missing:
            continue
        role = node.role or node.agent_name or node.run_id
        listed = "、".join(f"`{p}`" for p in missing[:6])
        more = f" 等 {len(missing)} 处" if len(missing) > 6 else ""
        gaps.append(
            _annotate_gap(
                role,
                f"声明的交付物路径未落盘：{listed}{more}",
                reason=REASON_PATH_MISMATCH,
            )
        )
    return gaps


def _dedupe_gap_rows(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate (reason, description) rows; first occurrence wins."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for gap in gaps:
        key = (str(gap.get("reason") or ""), str(gap.get("description") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(gap)
    return out


def _has_completed_revision(run_id: str, results: dict[str, RunState]) -> bool:
    """True when a hot-redirect revision (``{run_id}_rev*``) finished for this node."""
    prefix = f"{run_id}_rev"
    return any(
        rid.startswith(prefix) and st is not None and st.phase is RunPhase.COMPLETED
        for rid, st in results.items()
    )


def _covering_replacement_ran(
    run_id: str,
    plan: RunPlan,
    results: dict[str, RunState],
) -> bool:
    """True when same-plan continue_from / replaces already ran for this node.

    Used to drop scary「计划收口时跳过」when a covering补派 has already progressed.
    """
    for node in plan.nodes:
        if node.run_id == run_id:
            continue
        covers = (node.continue_from_run_id or "").strip() == run_id or (
            node.replaces_run_id or ""
        ).strip() == run_id
        if not covers:
            continue
        st = results.get(node.run_id)
        if st is None:
            continue
        # 已跑或成功（含 FAILED/CANCELLED：补派已发生，勿并排吓人跳过）。
        if st.phase in (
            RunPhase.COMPLETED,
            RunPhase.FAILED,
            RunPhase.CANCELLED,
            RunPhase.RUNNING,
        ):
            return True
    return False


def _is_path_hint(text: str, reason: str = "") -> bool:
    """True for declared-vs-landed path mismatch (legacy path_hint copy included)."""
    if reason in (REASON_PATH_HINT, REASON_PATH_MISMATCH):
        return True
    return any(marker in (text or "") for marker in _PATH_MISMATCH_MARKERS)


def _is_soft_reminder(text: str, reason: str = "") -> bool:
    """True when this gap row is a soft note (待核实等), not blocking."""
    if _is_path_hint(text, reason):
        return False
    if reason in _SOFT_GAP_REASONS:
        return True
    return any(marker in text for marker in _SOFT_REMINDER_MARKERS)


def _soft_paths(text: str) -> list[str]:
    """Extract workspace paths from soft-warning hit lines (``path`` · label · …)."""
    seen: set[str] = set()
    out: list[str] = []
    for path in _SOFT_PATH_RE.findall(text or ""):
        p = path.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _soft_hit_count(text: str) -> int:
    """Best-effort hit count from soft warning copy; fall back to 1."""
    match = _SOFT_HIT_COUNT_RE.search(text or "")
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            pass
    return 1


def _annotate_gap(
    role: str,
    text: str,
    *,
    reason: str = "",
    severity: str = "",
) -> dict[str, Any]:
    """Build one gap row; soft reminders get severity=warning + optional paths."""
    item: dict[str, Any] = {"role": role, "description": text}
    if _is_path_hint(text, reason):
        item["reason"] = REASON_PATH_MISMATCH
        return item
    soft = severity == "warning" or _is_soft_reminder(text, reason)
    if soft:
        item["severity"] = "warning"
        item["reason"] = reason or REASON_UNVERIFIED_NOTE
        paths = _soft_paths(text)
        if paths:
            item["paths"] = paths
        return item
    if reason:
        item["reason"] = reason
    return item


def _node_gaps(plan: RunPlan, results: dict[str, RunState]) -> list[dict[str, Any]]:
    """Terminal-but-undelivered plan nodes → gap rows (failed / skipped / cancelled)."""
    gaps: list[dict[str, Any]] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None:
            continue
        role = node.role or node.agent_name or node.run_id
        if state.phase is RunPhase.FAILED:
            err = (state.error or "").strip()
            desc = f"未完成（失败：{err}）" if err else "未完成（失败）"
            gaps.append({"role": role, "description": desc, "reason": REASON_NODE_FAILED})
        elif state.phase is RunPhase.SKIPPED:
            # Prefer first-class delivery_gaps (turn-ceiling honesty: 未目验 / 未跑
            # web_quality / {{…}}); fall back to a generic skip row.
            emitted = False
            for row in state.delivery_gaps or []:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("description") or "").strip()
                if not text:
                    continue
                reason = str(row.get("reason") or "").strip()
                severity = str(row.get("severity") or "").strip()
                gaps.append(_annotate_gap(role, text, reason=reason, severity=severity))
                emitted = True
            if not emitted:
                # 同图已有 continue_from / replaces 补派已跑 → 收掉吓人「计划收口跳过」。
                if _covering_replacement_ran(node.run_id, plan, results):
                    continue
                gaps.append({"role": role, "description": _PLAN_CUTOFF_SKIP_DESC})
        elif state.phase is RunPhase.CANCELLED and not _has_completed_revision(
            node.run_id, results
        ):
            gaps.append(
                {
                    "role": role,
                    "description": "未完成（中途取消）",
                    "reason": REASON_CANCELLED,
                }
            )
    return gaps


def _count_empty_or_degraded_nodes(
    plan: RunPlan,
    results: dict[str, RunState],
) -> tuple[int, int, list[str]]:
    """Return (emptyish_count, terminal_count, role_labels) for empty-handoff storm.

    emptyish = cancelled(no revision) / failed / completed+degraded_handoff /
    completed with zero files and empty content.
    """
    emptyish = 0
    terminal = 0
    roles: list[str] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None:
            continue
        role = str(node.role or node.agent_name or node.run_id)
        if state.phase is RunPhase.CANCELLED and not _has_completed_revision(node.run_id, results):
            terminal += 1
            emptyish += 1
            roles.append(role)
            continue
        if state.phase is RunPhase.FAILED:
            terminal += 1
            emptyish += 1
            roles.append(role)
            continue
        if state.phase is not RunPhase.COMPLETED:
            continue
        terminal += 1
        files = bool(state.files_touched) or any(
            isinstance(a, dict) and a.get("status") == "accepted"
            for a in (state.file_acceptance or [])
        )
        degraded = False
        for row in getattr(state, "delivery_gaps", None) or []:
            if isinstance(row, dict) and str(row.get("reason") or "") == REASON_DEGRADED_HANDOFF:
                degraded = True
                break
        if not degraded and state.warnings:
            degraded = any("交接说明不够完整" in str(w) for w in state.warnings)
        debrief = state.debrief if isinstance(state.debrief, dict) else None
        if debrief and debrief.get("degraded"):
            degraded = True
        body = (state.content or "").strip()
        if degraded or (not files and len(body) < 40):
            emptyish += 1
            roles.append(role)
    return emptyish, terminal, roles


def _empty_handoff_storm_gap(
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    files_landed: bool,
) -> dict[str, Any] | None:
    """B1：空交接占比高 → blocking PARTIAL gap（禁『仍在进行』空悬）."""
    if files_landed:
        return None
    emptyish, terminal, roles = _count_empty_or_degraded_nodes(plan, results)
    if emptyish < 3 and not (terminal >= 5 and emptyish * 2 >= terminal):
        return None
    shown = "、".join(roles[:6]) if roles else "多席"
    extra = f"等 {len(roles)} 席" if len(roles) > 6 else shown
    return _annotate_gap(
        "验收",
        f"空交接/未交付席位过多（{emptyish}/{terminal}）：{extra}——"
        "须同回合 PARTIAL 终稿（已完成摘要 + 缺口清单 + 下一步），禁止『仍在进行』空悬",
        reason=REASON_EMPTY_HANDOFF_STORM,
    )


def _cancel_zero_output_checklist_gap(
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    files_landed: bool,
) -> dict[str, Any] | None:
    """B1：cancel + 零落盘 → 结构化未交付清单（须 draft_ack）."""
    if files_landed:
        return None
    cancelled_roles: list[str] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if state is None:
            continue
        if state.phase is RunPhase.CANCELLED and not _has_completed_revision(node.run_id, results):
            cancelled_roles.append(str(node.role or node.agent_name or node.run_id))
    if not cancelled_roles:
        return None
    shown = "、".join(cancelled_roles[:8])
    extra = f"等 {len(cancelled_roles)} 项" if len(cancelled_roles) > 8 else shown
    return _annotate_gap(
        "验收",
        f"取消且零落盘——未交付清单：{extra}；请给出可继续动作，禁止仅『重新派工』短句",
        reason=REASON_CANCELLED,
    )


def _soften_landed_degraded_gaps(
    gaps: list[dict[str, Any]],
    *,
    files_landed: bool,
) -> list[dict[str, Any]]:
    """刀1 / 方案 A：有落盘时 degraded_handoff → warning 备注，不挡 delivered/notes。"""
    if not files_landed:
        return gaps
    out: list[dict[str, Any]] = []
    for gap in gaps:
        reason = str(gap.get("reason") or "").strip()
        desc = str(gap.get("description") or "")
        if reason == REASON_DEGRADED_HANDOFF or "交接说明不够完整" in desc or ("降级合成" in desc):
            softened = dict(gap)
            softened["severity"] = "warning"
            softened["reason"] = REASON_DEGRADED_HANDOFF
            # 人口语：去掉可能残留的内部码。
            text = str(softened.get("description") or "").strip()
            if not text or "degraded_handoff" in text or "continue_from" in text:
                softened["description"] = (
                    "交接说明不够完整，系统已代为补写摘要（已落盘文件不受影响）"
                )
            out.append(softened)
        else:
            out.append(gap)
    return out


def _is_blocking(gap: dict[str, Any]) -> bool:
    return gap.get("severity") != "warning"


def _is_zero_landing_text(text: str) -> bool:
    """True when gap copy is the shared zero-``files_touched`` predicate."""
    return any(marker in (text or "") for marker in _ZERO_LANDING_MARKERS)


def _code_ran_without_writeback(results: dict[str, RunState]) -> bool:
    """True when a COMPLETED worker ran ``code_execute`` successfully but landed no files."""
    from agentcore.runtime.delegate.completion import (
        _code_execute_succeeded_in_transcript,
        _worker_files_written,
    )

    for state in results.values():
        if state is None or state.phase is not RunPhase.COMPLETED:
            continue
        if _worker_files_written(state):
            continue
        if state.transcript and _code_execute_succeeded_in_transcript(state.transcript):
            return True
    return False


def _landing_failure_kind_from_results(results: dict[str, RunState]) -> str | None:
    """Aggregate landing-write failure attribution across workers (channel_dead wins)."""
    from agentcore.runtime.runs.serialize import landing_write_failure_kind

    saw_write_failed = False
    for state in results.values():
        if state is None:
            continue
        kind = landing_write_failure_kind(getattr(state, "transcript", None))
        if kind == "channel_dead":
            return "channel_dead"
        if kind == "write_failed":
            saw_write_failed = True
    return "write_failed" if saw_write_failed else None


def _files_not_landed_gap(results: dict[str, RunState]) -> dict[str, Any]:
    """Batch-only soft note when no worker-attributed zero-landing row exists.

    甲⁺：severity=warning → state=notes，不挡整批 / CEO finish。
    """
    failure_kind = _landing_failure_kind_from_results(results)
    if failure_kind == "channel_dead":
        # Mirror zero_files_gap_message(channel_dead): prose/handoff close-out.
        text = (
            "本批未见落盘：写盘通道不可用（工作区通道已挂起 / 活性挂起），"
            "落盘工具调用失败——请在 handoff 或正文交结论，禁止再尝试落盘；"
            "可请用户恢复通道后重试"
        )
    elif failure_kind == "write_failed":
        text = (
            "本批未见落盘：已尝试写盘但未成功（工具失败），工作区仍无新文件"
            "——此提示来自写盘失败，而非粘在回复正文"
        )
    elif _code_ran_without_writeback(results):
        text = (
            "本批未见落盘：已执行代码但未把产物写回工作区"
            "（沙箱内文件不算交付；须用写文件工具落盘，或确保脚本执行后写回工作区）"
        )
    else:
        from agentcore.runtime.runs.serialize import format_file_landing_tools_slash

        tools = format_file_landing_tools_slash()
        text = f"本批未见落盘（须用 {tools} 落盘）"
    return {
        "role": _BATCH_ACCEPTANCE_ROLE,
        "description": text,
        "reason": REASON_FILES_NOT_LANDED,
        "severity": "warning",
    }


def _member_files_not_landed_gap(
    role: str,
    source: dict[str, Any],
    results: dict[str, RunState],
) -> dict[str, Any]:
    """Per-worker soft tip: 本队员本波未交卷（定案 B · 终态可见性）.

    Keeps ``severity=warning`` so CEO can see *who* skipped landing without
    flipping the batch to blocked / criteria_unmet. When the source gap was
    ``node_failed`` (契约硬失败 / FAILED)，保留该 reason 以闩 ``requires_draft_ack``
    （能力4）；其余零落盘仍用 ``files_not_landed``。
    """
    raw = str(source.get("description") or "").strip()
    # Strip FAILED node wrapper so the soft tip stays notes, not「未完成（失败）」.
    if raw.startswith("未完成（失败：") and raw.endswith("）"):
        raw = raw[len("未完成（失败：") : -1].strip()
    if "本队员本波未交卷" in raw:
        text = raw
    elif "未把产物写入工作区" in raw:
        text = f"本队员本波未交卷：{raw}"
    else:
        # Fallback: rebuild from aggregate attribution (role may lack contract copy).
        failure_kind = _landing_failure_kind_from_results(results)
        from agentcore.runtime.runs.contract import zero_files_gap_message

        text = zero_files_gap_message(landing_failure_kind=failure_kind)
    # 能力4：零落盘 soft 投影仍保留 node_failed reason → draft_ack 闩不断。
    src_reason = str(source.get("reason") or "").strip()
    reason = REASON_NODE_FAILED if src_reason == REASON_NODE_FAILED else REASON_FILES_NOT_LANDED
    return {
        "role": role,
        "description": text,
        "reason": reason,
        "severity": "warning",
    }


def _project_user_gaps(
    raw_gaps: list[dict[str, Any]],
    results: dict[str, RunState],
) -> list[dict[str, Any]]:
    """Project zero-landing rows to soft ``files_not_landed`` (per-worker when possible).

    定案 B：有队员角色归因时按人保留「本队员本波未交卷」；仅批次谓词时合并为
    一条「本批未见落盘」。甲⁺：一律 warning，不挡整批。
    """
    zero_by_role: dict[str, dict[str, Any]] = {}
    role_order: list[str] = []
    other: list[dict[str, Any]] = []
    for gap in raw_gaps:
        text = str(gap.get("description") or "")
        if _is_zero_landing_text(text):
            role = str(gap.get("role") or "").strip() or _BATCH_ACCEPTANCE_ROLE
            if role not in zero_by_role:
                zero_by_role[role] = gap
                role_order.append(role)
        else:
            other.append(gap)
    if not zero_by_role:
        return other
    worker_roles = [r for r in role_order if r != _BATCH_ACCEPTANCE_ROLE]
    if worker_roles:
        projected = [
            _member_files_not_landed_gap(role, zero_by_role[role], results) for role in worker_roles
        ]
        return [*projected, *other]
    return [_files_not_landed_gap(results), *other]


def _warning_note_stats(warnings: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (hit_count, distinct_file_count) for soft reminder rows."""
    hits = 0
    files: set[str] = set()
    for gap in warnings:
        hits += _soft_hit_count(str(gap.get("description") or ""))
        for path in gap.get("paths") or []:
            if path:
                files.add(str(path))
        if not gap.get("paths"):
            for path in _soft_paths(str(gap.get("description") or "")):
                files.add(path)
    return max(hits, len(warnings) or 0), len(files)


def _build_summary(
    delivered: list[str],
    blocking: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    """Human summary: separate 未完成 vs 待核实; writing cutoff → 成篇未写完."""
    warn_hits, warn_files = _warning_note_stats(warnings)
    path_only = bool(warnings) and all(
        g.get("reason") in (REASON_PATH_HINT, REASON_PATH_MISMATCH) for g in warnings
    )
    degraded_only = bool(warnings) and all(
        g.get("reason") == REASON_DEGRADED_HANDOFF for g in warnings
    )
    warn_bit = ""
    if warn_hits:
        if path_only:
            warn_bit = f"{warn_hits} 处路径失配"
        elif degraded_only:
            warn_bit = f"{warn_hits} 处交接备注"
        else:
            warn_bit = f"{warn_hits} 处待核实备注"
            if warn_files:
                warn_bit += f"（{warn_files} 个文件）"

    if not blocking:
        if warn_bit:
            if delivered and degraded_only:
                return f"已交付 {len(delivered)} 个文件；另有 {warn_bit}"
            return f"有 {warn_bit}"
        if delivered:
            return f"已交付 {len(delivered)} 个文件"
        return "无交付缺口"

    writing = any(g.get("reason") in _WRITING_CUTOFF_REASONS for g in blocking)
    other_n = sum(1 for g in blocking if g.get("reason") not in _WRITING_CUTOFF_REASONS)

    if not delivered:
        if writing and other_n == 0:
            head = "未能交付：成篇未写完"
        elif writing:
            head = f"未能交付：成篇未写完；另有 {other_n} 项未完成"
        else:
            head = f"未能交付：{len(blocking)} 项未完成"
        if warn_bit:
            return f"{head}；另有 {warn_bit}"
        return head

    parts: list[str] = [f"已交付 {len(delivered)} 个文件"]
    if writing:
        parts.append("成篇未写完")
        if other_n:
            parts.append(f"另有 {other_n} 项未完成")
    else:
        parts.append(f"{len(blocking)} 项未完成")
    if warn_bit:
        parts.append(f"另有 {warn_bit}")
    return "；".join(parts)


def build_delivery_status(
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    backend: Any = None,
    criteria_gaps: list[str] | None = None,
    promotion_ledger: TurnPromotionLedger | None = None,
) -> dict[str, Any] | None:
    """Build a ``delivery_status`` payload, or ``None`` when there is nothing to report.

    Emission gate: at least one accepted file, one gap, or one rejected artifact —
    a pure-prose successful batch stays silent (研究 / 分析类委派不该弹交付卡).
    All inputs are the wrap-up signals the engine already computed; nothing here
    re-verifies the workspace. ``delivered_files`` = accepted only;
    ``artifacts`` carries path-level acceptance (accepted + rejected).

    ``promotion_ledger`` (回合共享台账): 已归位路径在这里被重映射到新位置，
    并带出 ``promoted`` 行；不传（旧调用 / 单测）则完全不涉及归位。
    """
    from agentcore.runtime.delegate.completion import (
        collect_verify_failure_gaps,
        collect_worker_gaps,
        plan_mentions_binary_artifact,
        plan_suggests_code_verification,
    )

    delivered = _delivered_files(results, plan)
    artifacts = _collect_artifacts(results, plan)
    files_landed = bool(delivered) or bool(artifacts)

    # B1：worker 转录里 browser_* 成功 → 闩锁（CEO 综收可对账；非气泡启发式）。
    from agentcore.runtime.closing_posture import note_browser_tool_success_from_messages

    for run_state in results.values():
        if run_state is None:
            continue
        transcript = getattr(run_state, "transcript", None)
        if transcript:
            note_browser_tool_success_from_messages(transcript)

    raw_gaps: list[dict[str, Any]] = []
    # ① 契约 / 交接残差（软接受后仍未对齐的声明交付物、degraded 交接、预算/超时掐断…）。
    for role, rows in collect_worker_gaps(plan, results):
        for row in rows:
            if isinstance(row, dict):
                text = str(row.get("description") or "").strip()
                reason = str(row.get("reason") or "").strip()
                severity = str(row.get("severity") or "").strip()
            else:
                text = str(row).strip()
                reason = ""
                severity = ""
            if not text:
                continue
            raw_gaps.append(_annotate_gap(role, text, reason=reason, severity=severity))
    # ①b 验证形工具失败（可用性诚实性 · 丙）——COMPLETED 但 browser_navigate /
    # test_run / verify 形 code_execute·terminal 失败 → 不得仍为 delivered。
    for role, rows in collect_verify_failure_gaps(plan, results):
        for row in rows:
            text = str(row.get("description") or "").strip()
            if not text:
                continue
            reason = str(row.get("reason") or REASON_VERIFY_FAILED).strip()
            raw_gaps.append(_annotate_gap(role, text, reason=reason or REASON_VERIFY_FAILED))
    # ①c 文献成文证据不足（学术综述诚实性）——仅 research_report / 同等成文综述；
    # parallel_brief 不套。blocking → 不得 delivered（partial/blocked）；不扫完成话术词。
    # 接缝真源：web_search / RunState.evidence_gap（academic_literature）；gap reason
    # 仍为 evidence_deficit（交付卡契约）。
    from agentcore.runtime.runs.research_quality import (
        collect_evidence_deficit_gaps,
        collect_thin_review_gaps,
    )

    for row in collect_evidence_deficit_gaps(plan.nodes, results):
        text = str(row.get("description") or "").strip()
        if not text:
            continue
        reason = str(row.get("reason") or REASON_EVIDENCE_DEFICIT).strip()
        raw_gaps.append(_annotate_gap("验收", text, reason=reason or REASON_EVIDENCE_DEFICIT))
    # ①c2 已声明复核落盘未对齐合格报告（案 thin-review A′）——blocking thin_review；
    # 不扫角色名；有 accepted 合格报告则豁免短 handoff。
    for row in collect_thin_review_gaps(plan.nodes, results):
        text = str(row.get("description") or "").strip()
        if not text:
            continue
        reason = str(row.get("reason") or REASON_THIN_REVIEW).strip()
        role = str(row.get("role") or "").strip() or "验收"
        raw_gaps.append(_annotate_gap(role, text, reason=reason or REASON_THIN_REVIEW))
    # ①d 未解写权冲突（案 ghost-owner P0-B）——denied_paths 仍被他人持锁 → blocking；
    # 不扫「定稿|闭环」正文；真源=账本结构化信号。
    from agentcore.runtime.closing_posture import (
        note_unresolved_write_ownership_from_ledger,
        run_ids_for_write_ownership_scan,
    )

    ownership_paths = note_unresolved_write_ownership_from_ledger(
        execution_id=execution_id,
        run_ids=run_ids_for_write_ownership_scan(plan=plan, results=results),
    )
    if ownership_paths:
        shown = "、".join(f"`{p}`" for p in ownership_paths[:3])
        more = f" 等 {len(ownership_paths)} 处" if len(ownership_paths) > 3 else ""
        raw_gaps.append(
            _annotate_gap(
                "验收",
                f"写权冲突未解：{shown}{more}（账本仍记他人持锁；未结构化移交）",
                reason=REASON_WRITE_OWNERSHIP,
            )
        )
    # ② 完成验收未满足 / soft overlay notes（批次级）。
    # Soft markers（「不阻断验收」等）经 _annotate_gap → severity=warning → state=notes。
    for gap in criteria_gaps or []:
        text = str(gap).strip()
        if text:
            raw_gaps.append(_annotate_gap("验收", text))
    # ③ 失败 / 未执行 / 取消的计划节点（热修已接手的取消节点不算缺口）。
    raw_gaps.extend(_node_gaps(plan, results))
    # ③b 声明路径 vs 实际落盘（纯字符串）→ path_mismatch blocking。
    raw_gaps.extend(_declared_path_mismatch_gaps(plan, results))
    # ③c 拒收产物（file_acceptance rejected）→ 结构化 gap + draft_ack（能力4 残差）。
    # 与 delivered_files / synthesis「路径已核」同源；不扫盘上「缺席」散文。
    raw_gaps.extend(_artifact_rejected_gaps(artifacts))
    raw_gaps = _dedupe_gap_rows(raw_gaps)
    # ③d B1：空交接风暴 / cancel·0 产出 → blocking + draft_ack（强制 PARTIAL 缺口清单）。
    storm = _empty_handoff_storm_gap(plan, results, files_landed=files_landed)
    if storm is not None:
        raw_gaps.append(storm)
        from agentcore.runtime.closing_posture import note_empty_handoff_storm

        note_empty_handoff_storm()
    cancel_gap = _cancel_zero_output_checklist_gap(plan, results, files_landed=files_landed)
    if cancel_gap is not None:
        raw_gaps.append(cancel_gap)
        from agentcore.runtime.closing_posture import note_cancel_zero_output

        note_cancel_zero_output()
    # 用户面：零落盘按队员 soft 投影（本队员本波未交卷）；仅批次谓词时合并。
    projected = _project_user_gaps(raw_gaps, results)
    gaps = projected[:_MAX_GAPS]
    if len(projected) > _MAX_GAPS:
        from agentcore.runtime.context_cap import log_context_capped

        log_context_capped(
            site="delivery_gaps",
            original_count=len(projected),
            final_count=len(gaps),
            execution_id=execution_id,
        )
    # 刀1 / 方案 A：有落盘时 degraded_handoff 降为 warning 备注。
    gaps = _soften_landed_degraded_gaps(gaps, files_landed=files_landed)
    # 文献证据降档仅绑 research_report / 同等成文综述；非该形态丢弃误入的
    # evidence_deficit（parallel_brief 等不得因此离开 delivered）。
    from agentcore.runtime.runs.research_quality import plan_is_literature_report_delivery

    if not plan_is_literature_report_delivery(plan.nodes):
        gaps = [g for g in gaps if g.get("reason") != REASON_EVIDENCE_DEFICIT]

    blocking = [g for g in gaps if _is_blocking(g)]
    warnings = [g for g in gaps if not _is_blocking(g)]

    # 待用户操作：① 无执行环境 → 按会话 location 诚实分流（已在云≠再导入到云；
    #    wire kind 仍可 bind_local_folder；本机传统合法非默认）；
    # ② 整页 QA 预算 defer → 一键续派验收；
    # ③ 额度 SKIPPED 未跑节点 → 续跑入口。
    # 成篇未写完不再挂 continue_writing——改由对话框接着说。
    # ① 判定复用 code_execution_enabled_for 单一真相源（与 worker registry / 委派闸同一谓词）。
    actions: list[dict[str, str]] = []
    if any(g.get("reason") == REASON_QA_DEFERRED for g in blocking):
        actions.append(_website_verify_action(_infer_website_site(plan)))
    skipped_budget_roles = [
        str(g.get("role") or "").strip() or "未跑节点"
        for g in blocking
        if g.get("reason") == REASON_TURN_TOKEN_BUDGET
    ]
    # Dedup role labels while preserving order.
    seen_roles: set[str] = set()
    skipped_roles_unique: list[str] = []
    for role in skipped_budget_roles:
        if role not in seen_roles:
            seen_roles.add(role)
            skipped_roles_unique.append(role)
    if skipped_roles_unique:
        actions.append(_continue_skipped_runs_action(skipped_roles_unique))
    if backend is not None and blocking:
        from agentcore.runtime.delegate.exec_env_remediation import (
            cloud_exec_unavailable_delivery_action,
        )
        from agentcore.tools.builtin import code_execution_enabled_for

        needs_execution = (
            plan_suggests_code_verification(plan)
            or plan_mentions_binary_artifact(plan)
            or any(g.get("reason") == REASON_FILES_NOT_LANDED for g in blocking)
            or any("code_execute" in str(g.get("description") or "") for g in blocking)
        )
        if needs_execution and not code_execution_enabled_for(backend):
            actions.append(cloud_exec_unavailable_delivery_action(backend))

    # 云端已交付文件：即使用户面 state=delivered，也提示导出到本机（找不到文件夹）。
    # 与 bind_local_folder 可并存但语义不同（导出产物 ≠ 绑定执行环境）。
    if delivered and backend is not None and getattr(backend, "location", None) != "local":
        actions.append(
            {
                "kind": "export_to_local",
                "description": ("产物在云端工作区——导出到本机文件夹后即可 npm install / 本地运行"),
            }
        )

    # 已归位的产物：本回合早前 promote_product 搬走的路径此刻仍以旧路径出现在
    # worker 台账里（RunState 不会因搬家回写）。重映射后新卡不会复活已不存在的文件。
    from agentcore.runtime.delegate.promotion import apply_turn_promotions

    rejected = [a for a in artifacts if a.get("status") == "rejected"]
    if not delivered and not gaps and not rejected:
        # 无物质不发卡：必须留可诊断痕迹（仅失败态注册等于巡检瞎），载荷只记数量。
        logger.info(
            "delegate.delivery_status_empty",
            execution_id=execution_id,
            delivered_count=len(delivered),
            gaps_count=len(gaps),
            rejected_count=len(rejected),
        )
        return None

    if not blocking and not warnings:
        state = "delivered"
    elif not blocking and warnings:
        # 轻 B：仅 soft 自注（unverified_note）不降档；path_mismatch 已是 blocking。
        non_self_note = [g for g in warnings if g.get("reason") != REASON_UNVERIFIED_NOTE]
        state = "delivered" if not non_self_note and delivered else "notes"
    elif files_landed:
        # blocked = 纯失败无文件；有落盘（accepted 或 rejected）且有缺口 → partial。
        state = "partial"
    else:
        state = "blocked"

    summary = _build_summary(delivered, blocking, warnings)

    return apply_turn_promotions(
        {
            "execution_id": execution_id,
            "state": state,
            "summary": summary,
            "delivered_files": delivered,
            "gaps": gaps,
            "actions": actions,
            "artifacts": artifacts,
        },
        promotion_ledger,
    )


def _delivery_fingerprint(payload: dict[str, Any]) -> str:
    """Stable conclusion key: state + delivered paths + artifact rows + gaps."""
    delivered = payload.get("delivered_files") or []
    artifacts = payload.get("artifacts") or []
    gaps = payload.get("gaps") or []
    art_part = ",".join(
        f"{a.get('path')}:{a.get('status')}" for a in artifacts if isinstance(a, dict)
    )
    gap_part = ",".join(
        f"{g.get('reason')}:{g.get('description')}" for g in gaps if isinstance(g, dict)
    )
    del_part = ",".join(str(p) for p in delivered)
    return f"{payload.get('state')}|{del_part}|{art_part}|{gap_part}"


def _already_emitted_delivery(sink: Any, execution_id: str, fingerprint: str) -> bool:
    """True when this sink already emitted the same execution conclusion."""
    if not execution_id:
        return False
    try:
        by_exec = _emitted_delivery_fp.get(sink)
    except TypeError:
        return False
    if by_exec is None:
        _emitted_delivery_fp[sink] = {execution_id: fingerprint}
        return False
    if by_exec.get(execution_id) == fingerprint:
        return True
    by_exec[execution_id] = fingerprint
    return False


def maybe_emit_delivery_status(
    sink: Any,
    plan: RunPlan,
    results: dict[str, RunState],
    *,
    execution_id: str,
    backend: Any = None,
    criteria_gaps: list[str] | None = None,
    promotion_ledger: TurnPromotionLedger | None = None,
) -> None:
    """Emit ``delivery_status`` when the reconciliation has substance. Never raises."""
    try:
        payload = build_delivery_status(
            plan,
            results,
            execution_id=execution_id,
            backend=backend,
            criteria_gaps=criteria_gaps,
            promotion_ledger=promotion_ledger,
        )
        if payload is None:
            return
        gaps = payload.get("gaps") or []
        bind_delivery_verdict(
            DeliveryVerdict(
                state=str(payload["state"]),
                delivered_files=tuple(payload.get("delivered_files") or ()),
                execution_id=execution_id,
                requires_draft_ack=_gaps_require_draft_ack(gaps),
                gap_reasons=_gap_reasons_from(gaps),
            ),
            promotion_ledger=promotion_ledger,
        )
        from agentcore.runtime.closing_posture import (
            downgrade_verdict_for_unresolved_write_ownership,
            note_cloud_web_verify_gap_from_delivery,
            note_cutoff_delivery_gap_from_delivery,
            note_verify_budget_from_delivery,
        )

        # P0-B belt: latch already stamped in build; ensure delivered cannot stick.
        downgrade_verdict_for_unresolved_write_ownership(
            execution_id=execution_id,
            promotion_ledger=promotion_ledger,
        )
        note_cloud_web_verify_gap_from_delivery(gaps, criteria_gaps=criteria_gaps)
        note_verify_budget_from_delivery(gaps)
        # B′：token_budget / writing cutoff → CEO 综收软横幅 latch（真源=结构化 gaps）。
        note_cutoff_delivery_gap_from_delivery(gaps)
        from agentcore.runtime.delegate.promotion import note_delivery_reconciliation
        from agentcore.runtime.events import delivery_status

        fingerprint = _delivery_fingerprint(payload)
        if _already_emitted_delivery(sink, execution_id, fingerprint):
            return
        sink.emit(delivery_status(**payload))
        # 成品归位的 accepted 闸门读这一份（CEO 收口时刻的最新对账）。
        note_delivery_reconciliation(promotion_ledger, payload)
        artifacts = payload.get("artifacts") or []
        delivered_files = payload.get("delivered_files") or ()
        logger.info(
            "delegate.delivery_status_emitted",
            execution_id=execution_id,
            state=str(payload.get("state") or ""),
            artifacts_count=len(artifacts),
            accepted_count=len(delivered_files),
            rejected_count=sum(1 for a in artifacts if a.get("status") == "rejected"),
            gaps_count=len(gaps),
        )
    except Exception:  # noqa: BLE001 — wrap-up side channel must never break the drive
        logger.warning(
            "delegate.delivery_status_failed",
            execution_id=execution_id,
            exc_info=True,
        )


# 可用性短问（甲）：偏窄识别——能用/可用/好了吗/完成了吗；排除长指令与「打开浏览器验证」。
_AVAILABILITY_STATUS_RE = re.compile(
    r"^(?:"
    r"(?:现在|目前|这[个次回]?)?(?:已经|都)?"
    r"(?:能(?:不能)?用|可以(?:使用|用)?|可用|好了|完成了|搞定了|做好了)"
    r"(?:了|了吗|吗|了没|没|了没有|没有)?"
    r"|is\s+it\s+(?:done|ready|usable|working)\??"
    r"|can\s+(?:i|we)\s+use\s+it\??"
    r")$",
    re.IGNORECASE,
)


def is_availability_status_question(text: str) -> bool:
    """True for narrow「能不能用 / 好了吗 / 完成了吗」status asks (可用性诚实性 · 甲)."""
    compact = re.sub(r"\s+", "", (text or "").strip())
    if not compact or len(compact) > 24:
        return False
    # Drop punctuation commonly glued to short asks.
    compact = re.sub(r"[？?！!。．.，,、…]+$", "", compact)
    if not compact or len(compact) > 20:
        return False
    return _AVAILABILITY_STATUS_RE.match(compact) is not None


def availability_status_nudge_prompt() -> str:
    """CEO one-shot: short availability ask → card is the main answer."""
    return (
        "[系统提示] 可用性短问：用户在问能不能用 / 好了吗 / 完成了吗。"
        "本回合若已发出（或复用）交付状态卡，以该卡为主答——"
        "散文只写一句注释指路看卡，禁止另编口头可用性结论，"
        "禁止用「已完整可用」盖过 partial/blocked 卡。"
    )


def _payload_to_verdict(payload: dict[str, Any]) -> DeliveryVerdict | None:
    """Build a finish_guard verdict from a journal/wire delivery_status payload."""
    execution_id = str(payload.get("execution_id") or "").strip()
    state = str(payload.get("state") or "").strip()
    if not execution_id or state not in ("delivered", "partial", "blocked", "notes"):
        return None
    files = payload.get("delivered_files") or []
    if not isinstance(files, list):
        files = []
    return DeliveryVerdict(
        state=state,
        delivered_files=tuple(str(p) for p in files if p),
        execution_id=execution_id,
        requires_draft_ack=_gaps_require_draft_ack(payload.get("gaps") or []),
        gap_reasons=_gap_reasons_from(payload.get("gaps") or []),
    )


async def maybe_reinject_recent_delivery_for_availability_ask(
    sink: Any,
    *,
    conversation_id: str,
    user_message: str,
    exclude_turn_id: str | None = None,
    promotion_ledger: TurnPromotionLedger | None = None,
) -> bool:
    """On narrow availability short asks, re-emit the latest delivery_status onto this turn.

    Reuses the conversation's most recent durable delivery reconciliation (不另造第二套).
    Sets ``current_delivery_verdict`` and the shared ledger slot for finish_guard.
    Returns True when a card was re-emitted. Never raises.
    """
    if not is_availability_status_question(user_message):
        return False
    # Same-turn batch already stamped a verdict — no need to pull prior journal.
    if read_delivery_verdict(promotion_ledger=promotion_ledger) is not None:
        return False
    cid = (conversation_id or "").strip()
    if not cid:
        return False
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            payload = await repo.find_latest_delivery_status(
                conversation_id=cid,
                exclude_turn_id=exclude_turn_id,
            )
        if not isinstance(payload, dict):
            return False
        verdict = _payload_to_verdict(payload)
        if verdict is None:
            return False
        # Normalize wire fields for the event factory.
        raw_gaps = payload.get("gaps")
        raw_actions = payload.get("actions")
        raw_artifacts = payload.get("artifacts")
        raw_promoted = payload.get("promoted")
        gaps: list[Any] = raw_gaps if isinstance(raw_gaps, list) else []
        actions: list[Any] = raw_actions if isinstance(raw_actions, list) else []
        artifacts: list[Any] = raw_artifacts if isinstance(raw_artifacts, list) else []
        # 归位行随卡走：重发的是同一张卡（同 execution_id，fold 保最新），丢了 promoted
        # 就把旧路径的回查线索抹了，且本回合再归位时会按空台账重发、二次抹除。
        promoted: list[Any] = raw_promoted if isinstance(raw_promoted, list) else []
        files = list(verdict.delivered_files)
        summary = str(payload.get("summary") or "").strip() or (
            f"已交付 {len(files)} 个文件" if files else "无交付缺口"
        )
        bind_delivery_verdict(verdict, promotion_ledger=promotion_ledger)
        from agentcore.runtime.delegate.promotion import adopt_journaled_reconciliation
        from agentcore.runtime.events import delivery_status

        reinjected: dict[str, Any] = {
            "execution_id": verdict.execution_id,
            "state": verdict.state,
            "summary": summary,
            "delivered_files": files,
            "gaps": [g for g in gaps if isinstance(g, dict)],
            "actions": [a for a in actions if isinstance(a, dict)],
            "artifacts": [a for a in artifacts if isinstance(a, dict)],
            "promoted": [p for p in promoted if isinstance(p, dict)],
        }
        sink.emit(delivery_status(**reinjected))
        # 复用的对账同样是本回合的 accepted 真源（上一回合验收过、仍在工作间的产物可归位）；
        # 卡上已有的归位行一并接手，本回合再归位时才不会把它们抹掉。
        adopt_journaled_reconciliation(promotion_ledger, reinjected)
        return True
    except Exception:  # noqa: BLE001 — short-ask side channel must never break the turn
        logger.warning(
            "delegate.availability_delivery_reinject_failed",
            conversation_id=cid,
            exc_info=True,
        )
        return False
