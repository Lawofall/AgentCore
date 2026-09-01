"""Unresolved write-ownership honesty latch + ledger reconcile + soft banner.

案 20260804-ghost-owner-nested-lookup · P0-B
Structured only: claim-denied paths still held by another owner → latch + verdict
downgrade. Soft banner uses existing posture-A closed set. **禁止**扫「定稿|闭环」正文。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .core import (
    claims_posture_a,
    is_formal_complete_tier,
)

_turn_unresolved_write_ownership: ContextVar[bool] = ContextVar(
    "turn_unresolved_write_ownership", default=False
)
_turn_write_ownership_refused_runs: ContextVar[frozenset[str]] = ContextVar(
    "turn_write_ownership_refused_runs", default=frozenset()
)

_WRITE_OWNERSHIP_HONESTY_BANNER = (
    "【写权说明】本回合有未解写权冲突——下文若宣称完整交付 / 全员收卷 / "
    "已完整可用，可能不准确。请按部分完成收口并点名未移交或未落盘路径。\n\n"
)


def note_unresolved_write_ownership(*, run_id: str | None = None) -> None:
    """Latch turn-scoped unresolved write-collision / ownership-conflict evidence."""
    _turn_unresolved_write_ownership.set(True)
    rid = (run_id or "").strip()
    if rid:
        prev = _turn_write_ownership_refused_runs.get()
        _turn_write_ownership_refused_runs.set(prev | {rid})


def clear_unresolved_write_ownership() -> None:
    """Reset at turn entry (fresh arm / resume wire)."""
    _turn_unresolved_write_ownership.set(False)
    _turn_write_ownership_refused_runs.set(frozenset())


def turn_has_unresolved_write_ownership() -> bool:
    """True when this turn noted unresolved write-ownership conflict."""
    return bool(_turn_unresolved_write_ownership.get())


def collect_unresolved_write_ownership_paths(
    *,
    execution_id: str | None = None,
    run_ids: set[str] | frozenset[str] | list[str] | None = None,
    coordinator: Any = None,
) -> tuple[str, ...]:
    """Paths still owned by someone other than a run that was refused on claim.

    Uses public ledger APIs only (``denied_paths_for`` / ``owner_of``). Empty when
    the book has no lingering conflict (e.g. after structured transfer).
    """
    coord = coordinator
    if coord is None:
        try:
            from agentcore.workspace.write_claims import resolve_write_coordinator

            coord = resolve_write_coordinator(execution_id=execution_id)
        except Exception:  # noqa: BLE001 — honesty side channel must never raise
            return ()
    if coord is None:
        return ()
    ids = {
        str(rid).strip()
        for rid in (run_ids or ())
        if rid is not None and str(rid).strip()
    }
    if not ids:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    try:
        for rid in ids:
            for path in coord.denied_paths_for(rid):
                key = str(path or "").strip()
                if not key or key in seen:
                    continue
                owner = coord.owner_of(key)
                if owner and owner != rid:
                    seen.add(key)
                    out.append(key)
    except Exception:  # noqa: BLE001
        return ()
    return tuple(out)


def reconcile_unresolved_write_ownership_latch(
    *,
    execution_id: str | None = None,
    run_ids: set[str] | frozenset[str] | list[str] | None = None,
    coordinator: Any = None,
) -> tuple[str, ...]:
    """Recompute latch from the ledger; clear when scanned refusals are all resolved."""
    refused = set(_turn_write_ownership_refused_runs.get())
    scanned = {
        str(rid).strip()
        for rid in (run_ids or ())
        if rid is not None and str(rid).strip()
    } | refused
    if not scanned and coordinator is None and not (execution_id or "").strip():
        # Cannot recompute — leave sticky latch alone for finish_guard belt.
        return ()
    paths = collect_unresolved_write_ownership_paths(
        execution_id=execution_id,
        run_ids=scanned or None,
        coordinator=coordinator,
    )
    if paths:
        note_unresolved_write_ownership()
        return paths
    if scanned:
        # Live scan found nothing lingering — drop sticky collision latch.
        clear_unresolved_write_ownership()
    return ()


def note_unresolved_write_ownership_from_ledger(
    *,
    execution_id: str | None = None,
    run_ids: set[str] | frozenset[str] | list[str] | None = None,
    coordinator: Any = None,
) -> tuple[str, ...]:
    """Stamp or clear latch from ledger scan. Returns still-unresolved paths.

    Write occupancy is the tool call (CAS + disk serial). Ledger owners are not
    a blocking delivery gap.
    """
    _ = execution_id, run_ids, coordinator
    return ()


def run_ids_for_write_ownership_scan(
    *,
    plan: Any = None,
    results: dict[str, Any] | None = None,
    session: Any = None,
) -> set[str]:
    """Collect run_ids that may have claim-denials (plan / results / session workers)."""
    ids: set[str] = set()
    nodes = getattr(plan, "nodes", None) if plan is not None else None
    if nodes:
        for node in nodes:
            rid = (getattr(node, "run_id", None) or "").strip()
            if rid:
                ids.add(rid)
    if results:
        for rid in results:
            key = str(rid or "").strip()
            if key:
                ids.add(key)
    if session is not None:
        live = getattr(session, "live_plan", None)
        live_nodes = getattr(live, "nodes", None) if live is not None else None
        if live_nodes:
            for node in live_nodes:
                rid = (getattr(node, "run_id", None) or "").strip()
                if rid:
                    ids.add(rid)
        for rid in getattr(session, "completed_run_ids", ()) or ():
            key = str(rid or "").strip()
            if key:
                ids.add(key)
        running = getattr(session, "running_workers", None)
        if callable(running):
            for rid, _role in running():
                key = str(rid or "").strip()
                if key:
                    ids.add(key)
    return ids


def downgrade_verdict_for_unresolved_write_ownership(
    *,
    execution_id: str | None = None,
    run_ids: set[str] | frozenset[str] | list[str] | None = None,
    coordinator: Any = None,
    promotion_ledger: Any = None,
) -> None:
    """Internal honesty: unresolved write ownership → cannot stay ``delivered``.

    Reconciles sticky collision latch against the live ledger when possible.
    Does not scan user/synthesis prose for 「定稿|闭环」. Soft banner is separate.
    """
    reconcile_unresolved_write_ownership_latch(
        execution_id=execution_id,
        run_ids=run_ids,
        coordinator=coordinator,
    )
    if not turn_has_unresolved_write_ownership():
        return
    from agentcore.runtime.delegate.delivery_status import (
        DeliveryVerdict,
        bind_delivery_verdict,
        read_delivery_verdict,
    )

    verdict = read_delivery_verdict(promotion_ledger=promotion_ledger)
    eid = (execution_id or "").strip() or "write_ownership_conflict"
    if verdict is None:
        bind_delivery_verdict(
            DeliveryVerdict(
                state="partial",
                delivered_files=(),
                execution_id=eid,
            ),
            promotion_ledger=promotion_ledger,
        )
        return
    if not is_formal_complete_tier(verdict.state):
        return
    bind_delivery_verdict(
        DeliveryVerdict(
            state="partial",
            delivered_files=verdict.delivered_files,
            execution_id=verdict.execution_id,
            requires_draft_ack=verdict.requires_draft_ack,
            gap_reasons=getattr(verdict, "gap_reasons", ()),
            missing_declared=getattr(verdict, "missing_declared", ()),
            absent_claimed=getattr(verdict, "absent_claimed", ()),
        ),
        promotion_ledger=promotion_ledger,
    )


def apply_write_ownership_honesty_for_session(session: Any) -> tuple[str, ...]:
    """No-op: run-lifetime write locks are gone; harvest must not latch them."""
    _ = session
    return ()


def enforce_write_ownership_honesty(content: str) -> str:
    """Prefix soft banner when unresolved write ownership meets posture-A claims.

    Soft only — never discards/rejects the turn; does not expand 「定稿」词表.
    """
    text = content or ""
    if not turn_has_unresolved_write_ownership():
        return text
    if not claims_posture_a(text):
        return text
    stripped = text.lstrip()
    if stripped.startswith("【写权说明】") or stripped.startswith("【收口说明】"):
        return text
    if stripped.startswith("【落盘说明】") or stripped.startswith("【验证说明】"):
        return text
    return _WRITE_OWNERSHIP_HONESTY_BANNER + text
