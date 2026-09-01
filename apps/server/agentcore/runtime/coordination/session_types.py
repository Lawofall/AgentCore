"""Coordination types, telemetry budget helpers, and the enter-coordination gate.

Split from ``session.py`` — pure move. Live session methods stay on mixins;
the process registry stays on ``session``. Public import path is still
``agentcore.runtime.coordination.session``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, NamedTuple

# Cap for coordinating-budget *telemetry counters* (progress + decision pools).
# Pools are observational only (批次 4)：计数与日志保留，不闸唤醒 / 不假限流。
# Necessary vs routine classification still drives *merge* batching (accumulate), not a hard stop.
DEFAULT_COORDINATION_BUDGET = 8
MAX_COORDINATION_BUDGET = 16
# 决策池保底切分口径（遥测分账用）：即便总额很小，也给必要决策类至少这么多记账槽。
_MIN_DECISION_BUDGET = 3

# Interjection stash keys safe to persist in the coordination journal snapshot.
# ``llm_credentials`` must never enter durable state (re-resolved on resume).
_INTERJECTION_SNAPSHOT_KEYS = frozenset(
    {
        "content",
        "user_id",
        "conversation_id",
        "attachments",
        "agent_mentions",
        "requires_tools",
        "x_client_platform",
        "llm_supports_tools",
    }
)


def coordination_budget_for_batch(node_count: int) -> int:
    """Scale telemetry budget with batch size: ``max(8, nodes+4)``, capped at 16."""
    return min(MAX_COORDINATION_BUDGET, max(DEFAULT_COORDINATION_BUDGET, node_count + 4))


def split_coordination_budget(total: int) -> tuple[int, int]:
    """Split a total coordination budget into ``(progress, decision)`` telemetry pools.

    决策池取 ``max(_MIN_DECISION_BUDGET, total // 3)``（不超过总额），其余归进度池；两池之和
    恒等于 ``total``。仅作唤醒分类计数 / 日志口径，不闸 CEO 唤醒。
    """
    total = max(0, int(total))
    decision = min(total, max(_MIN_DECISION_BUDGET, total // 3))
    progress = total - decision
    return progress, decision


# 编译期常量（前缀缓存 / 默认值友好）：默认与上限总额各自切分出的两池额度。
DEFAULT_PROGRESS_BUDGET, DEFAULT_DECISION_BUDGET = split_coordination_budget(
    DEFAULT_COORDINATION_BUDGET
)
MAX_PROGRESS_BUDGET, MAX_DECISION_BUDGET = split_coordination_budget(MAX_COORDINATION_BUDGET)


def _budget_pools_from_dict(data: dict[str, Any]) -> tuple[int, int] | None:
    """Read the two-pool budget from a snapshot dict.

    只认 ``progress_budget_remaining`` / ``decision_budget_remaining``；缺两池键则拒绝
    （开发期不兼容旧单池 ``budget_remaining`` 快照）。
    """
    if "progress_budget_remaining" not in data and "decision_budget_remaining" not in data:
        return None
    progress = int(data.get("progress_budget_remaining", DEFAULT_PROGRESS_BUDGET))
    decision = int(data.get("decision_budget_remaining", DEFAULT_DECISION_BUDGET))
    return max(0, progress), max(0, decision)


def _durable_terminal_run_ids(entries: list[dict[str, Any]] | None) -> set[str]:
    """``run_id`` values that already have a durable close fact in ``entries``."""
    from agentcore.runtime.terminal import RUN_CLOSE_EVENT_TYPES

    ids: set[str] = set()
    for entry in entries or []:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind not in RUN_CLOSE_EVENT_TYPES:
            continue
        rid = str((entry.get("payload") or {}).get("run_id") or "").strip()
        if rid:
            ids.add(rid)
    return ids


class CoordinationEventKind(StrEnum):
    WORKER_COMPLETED = "worker_completed"
    ESCALATION = "escalation"
    TIMEOUT = "timeout"
    ALL_COMPLETED = "all_completed"
    BOUNDARY_YIELD = "boundary_yield"
    # Mid-flight user message injected into the live coordination window (CEO routes).
    USER_INTERJECTION = "user_interjection"
    # Background drive cancelled (process kill / soft-stop). Wake host without
    # implying every worker finished — distinct from ALL_COMPLETED.
    DRIVE_CANCELLED = "drive_cancelled"


@dataclass(frozen=True, slots=True)
class CoordinationEvent:
    kind: CoordinationEventKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelResolution:
    """Outcome of resolving a CEO-supplied ``cancel_worker`` arg to a full run_id.

    The CEO only ever sees role / short names in coordination events, but the
    scheduler cancels by exact engine-minted run_id — so a short name must be
    resolved back against the in-flight worker registry first.

    - ``run_id`` — the resolved full run_id, or ``None`` when unresolved.
    - ``reason`` — how it resolved (``exact`` / ``suffix`` / ``role``) or why it
      failed (``ambiguous`` / ``not_found``).
    - ``candidates`` — colliding full run_ids when ``ambiguous`` (empty otherwise).
    """

    run_id: str | None
    reason: str
    candidates: tuple[str, ...] = ()


@dataclass
class CoordinationSnapshot:
    """Durable slice restored on ask_user resume / process restart."""

    execution_id: str
    draft: str = ""
    conversation_id: str = ""
    completed_run_ids: list[str] = field(default_factory=list)
    # 两池遥测计数（进度池 + 决策池）。快照只序列化分池键，无合计双轨。
    progress_budget_remaining: int = DEFAULT_PROGRESS_BUDGET
    decision_budget_remaining: int = DEFAULT_DECISION_BUDGET
    total_workers: int = 0
    active: bool = True
    cancel_run_ids: list[str] = field(default_factory=list)
    # CEO cancel_worker stamps (wording). Missing on old snapshots → empty.
    ceo_cancel_worker_ids: list[str] = field(default_factory=list)
    ceo_cancel_started_ids: list[str] = field(default_factory=list)
    pending_events: list[dict[str, Any]] = field(default_factory=list)
    # D1: blocking escalate awaiting CEO — survives ask_user soft-stop so resume can
    # resolve_escalation (or re-armed workers pick up a stashed answer).
    pending_arbitrations: list[dict[str, Any]] = field(default_factory=list)
    resolved_arbitrations: list[dict[str, Any]] = field(default_factory=list)
    # 批次 4 快照扩容：活计划 / 插话队列（无凭据）/ 注入与收口标记。
    live_plan: dict[str, Any] | None = None
    pending_interjections: list[dict[str, Any]] = field(default_factory=list)
    all_completed_injected: bool = False
    harvest_scheduled: bool = False
    terminal_posted: bool = False
    drive_cancelled: bool = False
    settled_via: str | None = None
    turn_attached: bool = True
    user_stopped: bool = False
    saw_first_completion: bool = False
    # C3: ownership ledger snapshot — v3 nested ``{_v, owners, written, …}``
    # (desk×path keys); v≤2 lazy-migrated on restore.
    file_ownership: dict[str, Any] = field(default_factory=dict)

    @property
    def budget_remaining(self) -> int:
        """两池合计（便利读；不参与序列化）。"""
        return self.progress_budget_remaining + self.decision_budget_remaining

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "draft": self.draft,
            "conversation_id": self.conversation_id,
            "completed_run_ids": list(self.completed_run_ids),
            "progress_budget_remaining": self.progress_budget_remaining,
            "decision_budget_remaining": self.decision_budget_remaining,
            "total_workers": self.total_workers,
            "active": self.active,
            "cancel_run_ids": list(self.cancel_run_ids),
            "ceo_cancel_worker_ids": list(self.ceo_cancel_worker_ids),
            "ceo_cancel_started_ids": list(self.ceo_cancel_started_ids),
            "pending_events": list(self.pending_events),
            "pending_arbitrations": list(self.pending_arbitrations),
            "resolved_arbitrations": list(self.resolved_arbitrations),
            "live_plan": self.live_plan,
            "pending_interjections": list(self.pending_interjections),
            "all_completed_injected": self.all_completed_injected,
            "harvest_scheduled": self.harvest_scheduled,
            "terminal_posted": self.terminal_posted,
            "drive_cancelled": self.drive_cancelled,
            "settled_via": self.settled_via,
            "turn_attached": self.turn_attached,
            "user_stopped": self.user_stopped,
            "saw_first_completion": self.saw_first_completion,
            "file_ownership": dict(self.file_ownership),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CoordinationSnapshot | None:
        if not data or not isinstance(data, dict):
            return None
        execution_id = str(data.get("execution_id") or "").strip()
        if not execution_id:
            return None
        pools = _budget_pools_from_dict(data)
        if pools is None:
            return None
        progress, decision = pools
        live_plan = data.get("live_plan")
        if live_plan is not None and not isinstance(live_plan, dict):
            live_plan = None
        settled_via = data.get("settled_via")
        if settled_via is not None:
            settled_via = str(settled_via).strip() or None
        if settled_via == "harvest":
            settled_via = "detached"
        raw_own = data.get("file_ownership")
        file_ownership: dict[str, Any] = {}
        if isinstance(raw_own, dict) and (
            raw_own.get("_v") in (2, 3) or isinstance(raw_own.get("owners"), dict)
        ):
            file_ownership = dict(raw_own)
        return cls(
            execution_id=execution_id,
            draft=str(data.get("draft") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
            completed_run_ids=[str(x) for x in (data.get("completed_run_ids") or [])],
            progress_budget_remaining=progress,
            decision_budget_remaining=decision,
            total_workers=int(data.get("total_workers") or 0),
            active=bool(data.get("active", True)),
            cancel_run_ids=[str(x) for x in (data.get("cancel_run_ids") or [])],
            ceo_cancel_worker_ids=[
                str(x) for x in (data.get("ceo_cancel_worker_ids") or [])
            ],
            ceo_cancel_started_ids=[
                str(x) for x in (data.get("ceo_cancel_started_ids") or [])
            ],
            pending_events=list(data.get("pending_events") or []),
            pending_arbitrations=list(data.get("pending_arbitrations") or []),
            resolved_arbitrations=list(data.get("resolved_arbitrations") or []),
            live_plan=live_plan,
            pending_interjections=[
                dict(x) for x in (data.get("pending_interjections") or []) if isinstance(x, dict)
            ],
            all_completed_injected=bool(data.get("all_completed_injected", False)),
            harvest_scheduled=bool(data.get("harvest_scheduled", False)),
            terminal_posted=bool(data.get("terminal_posted", False)),
            drive_cancelled=bool(data.get("drive_cancelled", False)),
            settled_via=settled_via,
            turn_attached=bool(data.get("turn_attached", True)),
            user_stopped=bool(data.get("user_stopped", False)),
            saw_first_completion=bool(data.get("saw_first_completion", False)),
            file_ownership=file_ownership,
        )


def should_enter_coordination(
    *,
    coordinate: bool,
    worker_count: int,
    depth: int,
    has_checkpoint: bool = False,
    checkpoint_enabled: bool = False,
) -> bool:
    """Gate: ≥1 worker + root CEO; opt out with ``coordinate=False``.

    Callers default ``coordinate`` to True when the LLM omits the arg; only an
    explicit false falls back to classic blocking. Nested lead still never
    enters. Solo (1 worker) enters so mid-flight interjections and
    ``cancel_worker`` stay reachable while the worker runs.

    Adjacent ≥2 gates (kickoff plan-preview, team_synthesis_preview, cold-start
    explore roster) are independent and stay multi-worker-only — solo keeps its
    zero-friction kickoff appearance.

    When the batch contains ``checkpoint_after`` nodes **and** the turn's checkpoint
    gate is open, stay on classic blocking drive so durable plan_review cards fire.
    Gate-off (evals / ``approvals_enabled=False``) leaves coordination unchanged.

    **Invariant B**: CEO arbitration (``resolve_escalation`` / ``awaiting=ceo``)
    is available iff a coordination session is active. Classic blocking escalate
    (no live session — e.g. ``coordinate=false`` / nested lead / ``checkpoint_after``)
    therefore hangs on the **user**, never the CEO — otherwise worker↔CEO deadlock
    (CEO blocked inside ``delegate``, worker waiting for ``resolve_escalation``).
    Solo-in-coordination has a free CEO, so Invariant B holds the same way as
    multi-worker coordination.
    """
    if coordinate is False:
        return False
    if depth != 0:
        return False
    if has_checkpoint and checkpoint_enabled:
        return False
    return worker_count >= 1


class _WorkerSpend(NamedTuple):
    """Process-local live spend for one in-flight worker. Not snapshotted."""

    rounds_used: int | None = None
    rounds_limit: int | None = None
    tokens_spent: int | None = None
