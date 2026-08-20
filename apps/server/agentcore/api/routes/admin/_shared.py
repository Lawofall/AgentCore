"""Shared projections, helpers and constants for the admin console routes.

Split out of the former single ``admin.py`` so each surface (overview / users /
usage / system / audit / conversations / observability) lives in its own module
while reusing one definition of the cross-surface projections (so e.g. the
overview dashboard and the observability page can never disagree on how a turn's
health rollup maps to the wire).
"""

from __future__ import annotations

from typing import Any

from agentcore.api.schemas import (
    AdminUserListItem,
    AdminUserResponse,
    ReplayRun,
    ReplaySpan,
    RunsPayload,
    TurnHealthWindow,
)
from agentcore.db.models import User

# 全站看板 windows: the 7-day trend length (matches /v1/usage/summary), shared by
# every surface that renders a「近 7 日」series so they span identical UTC days.
_TREND_DAYS = 7
# 复盘 span preview cap: a tool call's args/result are truncated to a triage-sized
# snippet (the full text lives in turn_journal / the client replay, not this ops view).
_SPAN_PREVIEW = 200


def _preview(text: str | None) -> str | None:
    """Truncate a tool arg/result to a triage-sized snippet (``None`` stays ``None``)."""
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= _SPAN_PREVIEW else text[:_SPAN_PREVIEW] + "…"


def _project_spans(entries: list[dict]) -> list[ReplaySpan]:
    """Project a turn's journal entries to the compact tool/LLM span list (会话复盘).

    Reads only the execution facts that triage a turn — ``llm_call`` (round /
    finish_reason / tokens) and ``tool_call`` (name / ok? / arg·result preview) — in
    emission (``seq``) order, skipping the heavy/display kinds (system prompt, team
    graph, full results). The full fidelity stays in turn_journal for client replay;
    this is the operator's at-a-glance "what did the turn actually do".
    """
    spans: list[ReplaySpan] = []
    for entry in entries:
        kind = entry.get("kind")
        payload = entry.get("payload") or {}
        if kind == "llm_call":
            usage = payload.get("usage") or {}
            spans.append(
                ReplaySpan(
                    kind="llm",
                    run_id=payload.get("run_id"),
                    round_idx=payload.get("round_idx"),
                    finish_reason=payload.get("finish_reason"),
                    input_tokens=int(usage.get("input", 0) or 0),
                    output_tokens=int(usage.get("output", 0) or 0),
                )
            )
        elif kind == "tool_call":
            spans.append(
                ReplaySpan(
                    kind="tool",
                    run_id=payload.get("run_id"),
                    name=payload.get("name"),
                    success=bool(payload.get("success", True)),
                    args_preview=_preview(payload.get("arguments")),
                    result_preview=_preview(payload.get("result")),
                )
            )
    return spans


def fold_replay_journal(
    entries: list[dict],
) -> tuple[list[ReplayRun], dict[str, Any] | None, RunsPayload | None]:
    """One journal → (ReplayRun[], ProjectedTurn | None, RunsPayload | None).

    Reuses the user-end / conformance pipeline only: ``runs_from_entries`` then
    ``project_turn(events)``. Does not synthesize a display event vector from the
    process lane (that would reopen the retired journal→live-stream heuristic).
    """
    from agentcore.conformance.projection import project_turn
    from agentcore.runtime.journal.fold import runs_from_entries

    payload = runs_from_entries(entries)
    if not payload:
        return [], None, None
    display = RunsPayload.model_validate(payload)
    events = payload.get("events") or []
    projected = project_turn(events) if events else None
    return _replay_runs_from_projected(projected, entries), projected, display


def _replay_runs_from_projected(
    projected: dict[str, Any] | None, entries: list[dict]
) -> list[ReplayRun]:
    """Lift the existing lightweight ReplayRun list from a ProjectedTurn.

    ``message_final`` supplies verbatim worker text (deltas are synthesized for
    the client fold). Empty when there is no team surface.
    """
    from agentcore.runtime.facts import FactKind

    if not projected:
        return []
    projected_runs = projected.get("runs") or []
    if not projected_runs:
        return []

    finals: dict[str, str] = {}
    for entry in entries:
        if entry.get("kind") != FactKind.MESSAGE_FINAL.value:
            continue
        p = entry.get("payload") or {}
        rid = p.get("run_id")
        if rid:
            finals[str(rid)] = p.get("content") or ""

    agents = {a["id"]: a for a in (projected.get("agents") or [])}
    out: list[ReplayRun] = []
    for r in projected_runs:
        rid = str(r.get("id") or "")
        agent_id = str(r.get("agentId") or "")
        agent = agents.get(agent_id) or {}
        content = finals.get(rid)
        if content is None:
            folded = agent.get("output") or ""
            content = folded or None
        elif content == "":
            content = None
        role = r.get("role") or agent.get("role") or None
        debrief = r.get("debrief")
        if debrief is not None and not isinstance(debrief, dict):
            debrief = None
        out.append(
            ReplayRun(
                run_id=rid,
                agent_id=agent_id,
                role=role if isinstance(role, str) else None,
                kind=str(r.get("kind") or "agent"),
                task=str(r.get("task") or ""),
                status=str(r.get("status") or "pending"),
                parent_run_id=r.get("parentRunId"),
                depends_on=[str(d) for d in (r.get("dependsOn") or [])],
                content=content,
                debrief=debrief,
                output_summary=r.get("outputSummary"),
                error=r.get("error"),
            )
        )
    return out


def _project_runs(entries: list[dict]) -> list[ReplayRun]:
    """Project a turn's journal to the lightweight multi-agent run list (会话复盘).

    Reuses the existing display fold — ``runs_from_entries`` rebuilds wire events,
    ``project_turn`` folds the team tree — then lifts ``message_final`` content for
    full worker text. Returns ``[]`` for plain chat (no team surface).
    """
    runs, _, _ = fold_replay_journal(entries)
    return runs


def _health_window(agg: dict) -> TurnHealthWindow:
    """Map a turn_metrics health rollup → the wire schema, deriving the rates.

    The repository returns raw counts (turns / errors / delegated); the rates
    (errors-per-turn, delegated-per-turn) are computed here so the schema carries
    ready-to-render fractions and a zero-turn window is a clean 0.0 (no /0).
    """
    turns = agg["turns"]
    delegated = agg["delegated"]
    return TurnHealthWindow(
        turns=turns,
        errors=agg["errors"],
        error_rate=(agg["errors"] / turns) if turns else 0.0,
        avg_duration_ms=agg["avg_duration_ms"],
        p95_duration_ms=agg["p95_duration_ms"],
        avg_rounds=agg["avg_rounds"],
        delegated_turns=delegated,
        delegated_rate=(delegated / turns) if turns else 0.0,
        input_tokens=agg["input_tokens"],
        output_tokens=agg["output_tokens"],
        # 协作质量 (学·度量 §2.5): 首计划存活率 over delegated turns + raw window sums.
        first_plan_survival_rate=(
            (agg["first_plan_survived"] / delegated) if delegated else 0.0
        ),
        scope_signals=agg["scope_signals"],
        revises=agg["revises"],
        escalations=agg["escalations"],
    )


def _admin_user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        email_verified_at=getattr(user, "email_verified_at", None),
        role=user.role,
        status=user.status,
        is_unlimited=user.is_unlimited,
        quota_daily_tokens=user.quota_daily_tokens,
        quota_monthly_cost_cny=user.quota_monthly_cost_cny,
        quota_daily_cost_cny=user.quota_daily_cost_cny,
        quota_daily_requests=user.quota_daily_requests,
        created_at=user.created_at,
        registration_ip=getattr(user, "registration_ip", None),
        deleted_at=user.deleted_at,
    )


def _admin_user_list_item(user: User, cost_total: int) -> AdminUserListItem:
    """A roster row = the account record + its all-time cumulative spend (nano-CNY)."""
    return AdminUserListItem(**_admin_user_response(user).model_dump(), cost_total=cost_total)
