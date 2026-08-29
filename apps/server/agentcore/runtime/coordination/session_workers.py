"""In-flight worker registry, cancel resolution, verify coalesce, and arbitration.

Split from ``session.py`` — pure move. Queue / timeout / snapshot stay on
their mixins; this mixin only tracks who is running and how to address them.
"""

# mypy: disable-error-code="misc"

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.coordination.session_types import CancelResolution, _WorkerSpend

if TYPE_CHECKING:
    from agentcore.runtime.coordination.session import CoordinationSession

logger = get_logger("agentcore.runtime.coordination.session")


class SessionWorkersMixin:
    """Running-worker book, cancel_worker resolution, busy stamps, verify cache."""

    file_ownership: Any | None

    def ensure_file_ownership(self: CoordinationSession) -> Any:
        """Lazy-init the session ownership ledger (one book for dispatch + write)."""
        if self.file_ownership is None:
            from agentcore.workspace.write_claims import WriteCoordinator

            self.file_ownership = WriteCoordinator()
        return self.file_ownership

    def register_arbitration(
        self: CoordinationSession,
        run_id: str,
        *,
        escalation_id: str,
        conversation_id: str,
        question: str = "",
        assumption: str = "",
        kind: str = "normal",
        ownership_paths: list[str] | None = None,
        lock_owner_run_id: str = "",
        escalator_is_lock_owner_nested_child: bool | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "run_id": run_id,
            "escalation_id": escalation_id,
            "conversation_id": conversation_id,
            "question": question,
            "assumption": assumption,
            "kind": kind,
        }
        if ownership_paths:
            payload["ownership_paths"] = list(ownership_paths)
        if lock_owner_run_id:
            payload["lock_owner_run_id"] = lock_owner_run_id
        if escalator_is_lock_owner_nested_child is not None:
            payload["escalator_is_lock_owner_nested_child"] = bool(
                escalator_is_lock_owner_nested_child
            )
        self.pending_arbitrations[run_id] = payload

    def get_arbitration(self: CoordinationSession, run_id: str) -> dict[str, Any] | None:
        return self.pending_arbitrations.get(run_id)

    def clear_arbitration(self: CoordinationSession, run_id: str) -> None:
        self.pending_arbitrations.pop(run_id, None)

    def stash_resolution(
        self: CoordinationSession,
        run_id: str,
        *,
        answer: str,
        via_user: bool = False,
        escalation_id: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "run_id": run_id,
            "answer": answer,
            "via_user": via_user,
        }
        if escalation_id:
            payload["escalation_id"] = escalation_id
        elif run_id in self.pending_arbitrations:
            eid = self.pending_arbitrations[run_id].get("escalation_id")
            if eid:
                payload["escalation_id"] = eid
        self.resolved_arbitrations[run_id] = payload
        self.pending_arbitrations.pop(run_id, None)

    def take_stashed_resolution(
        self: CoordinationSession, run_id: str
    ) -> dict[str, Any] | None:
        return self.resolved_arbitrations.pop(run_id, None)

    def mark_worker_completed(self: CoordinationSession, run_id: str) -> None:
        self.completed_run_ids.add(run_id)
        self.disarm_worker_timeout(run_id)
        # Ended bypass on the write ledger (separate from progress completed_run_ids).
        try:
            if self.file_ownership is not None:
                self.file_ownership.mark_ended(run_id)
        except Exception:  # noqa: BLE001 — never break completion
            pass
        self._handoff_ownership_on_complete(run_id)

    def _handoff_ownership_on_complete(self: CoordinationSession, run_id: str) -> None:
        """交接式写权：完成后把独占下游 artifact 路径交给唯一依赖方。"""
        rid = (run_id or "").strip()
        if not rid or self.file_ownership is None or self.live_plan is None:
            return
        try:
            from agentcore.runtime.coordination.append_guard import (
                handoff_owned_paths_on_complete,
            )

            moved = handoff_owned_paths_on_complete(
                self.live_plan,
                self.ensure_file_ownership(),
                rid,
                completed_run_ids=self.completed_run_ids,
                birth_desk_id=self.birth_desk_id,
            )
        except Exception:  # noqa: BLE001 — never break completion
            return
        if not moved:
            return
        try:
            from agentcore.core.logging import get_logger

            get_logger("agentcore.runtime.coordination.session").info(
                "file_ownership.completion_handoff",
                run_id=rid,
                execution_id=self.execution_id,
                transfers=[{"path": path, "new_owner": new_owner} for path, new_owner in moved],
            )
        except Exception:  # noqa: BLE001
            pass

    def take_progress_delta(self: CoordinationSession) -> set[str]:
        """Completed run_ids not yet named in a CEO progress block; advances cursor."""
        delta = set(self.completed_run_ids) - self.progress_reported_completed
        self.progress_reported_completed |= delta
        return delta

    def request_cancel(self: CoordinationSession, run_id: str) -> None:
        self.cancel_ids.add(run_id)

    def running_workers(self: CoordinationSession) -> list[tuple[str, str]]:
        """(full run_id, role) for every in-flight worker, sorted by run_id.

        Backs ``cancel_worker`` error listings so the CEO sees exactly which
        workers it can still cancel (and their full run_ids to copy).
        """
        return sorted(self._running_workers.items())

    def mark_worker_busy(
        self: CoordinationSession,
        run_id: str,
        kind: str,
        *,
        rounds_used: int | None = None,
        rounds_limit: int | None = None,
        tokens_spent: int | None = None,
    ) -> None:
        """Stamp that ``run_id`` is inside an LLM stream, tool call, verify, or CEO arbitration.

        Optional spend kwargs piggyback engine-already-tracked numbers onto the
        same busy channel (once per LLM/tool/round — not per token). Omit a
        kwarg to leave that field unchanged. Spend survives ``clear_worker_busy``.
        Unknown ``kind`` falls back to ``llm`` so a typo cannot silently drop
        the inflight bit; ``arbitrate`` is an allowed kind (not that fallback).
        """
        rid = (run_id or "").strip()
        if not rid or rid not in self._running_workers:
            return
        label = kind if kind in ("llm", "tool", "verify", "arbitrate") else "llm"
        self._busy_workers[rid] = label
        self._merge_worker_spend(
            rid,
            rounds_used=rounds_used,
            rounds_limit=rounds_limit,
            tokens_spent=tokens_spent,
        )

    def _merge_worker_spend(
        self: CoordinationSession,
        rid: str,
        *,
        rounds_used: int | None,
        rounds_limit: int | None,
        tokens_spent: int | None,
    ) -> None:
        if rounds_used is None and rounds_limit is None and tokens_spent is None:
            return
        prev = self._worker_spend.get(rid)
        used = int(rounds_used) if rounds_used is not None else (
            prev.rounds_used if prev is not None else None
        )
        limit = int(rounds_limit) if rounds_limit is not None else (
            prev.rounds_limit if prev is not None else None
        )
        if tokens_spent is not None:
            prior = 0
            if prev is not None and prev.tokens_spent is not None:
                prior = prev.tokens_spent
            spent: int | None = max(int(tokens_spent), prior)
        else:
            spent = prev.tokens_spent if prev is not None else None
        self._worker_spend[rid] = _WorkerSpend(used, limit, spent)

    def clear_worker_busy(self: CoordinationSession, run_id: str) -> None:
        self._busy_workers.pop((run_id or "").strip(), None)

    def has_inflight_work(self: CoordinationSession) -> bool:
        """True when any worker holds a short LLM/tool call (not verify / arbitrate)."""
        return any(kind in ("llm", "tool") for kind in self._busy_workers.values())

    def has_verify_busy(self: CoordinationSession) -> bool:
        """True when any registered worker is inside a bounded verify."""
        return any(kind == "verify" for kind in self._busy_workers.values())

    def worker_budget_facts(self: CoordinationSession, run_id: str) -> list[str]:
        """Engine-already-tracked budget numbers for one in-flight worker.

        Live spend (pass-local used/limit + tokens_spent) when the executor has
        stamped via ``note_coord_worker_busy``; otherwise the plan's static
        ceilings. Facts only — no runaway / quality heuristic. Omit a field
        when neither the live stamp nor the plan has it.
        """
        bits: list[str] = []
        from agentcore.runtime.runs.timeout_hard import get_hard_timeout

        guard = get_hard_timeout(run_id)
        if guard is not None:
            bits.append(f"超时阈值 {int(guard.threshold_s)}s")
            phase = getattr(guard.phase, "value", "") or ""
            if phase and phase not in ("armed", "disarmed"):
                bits.append(f"超时态 {phase}")
        spec = None
        live = self.live_plan
        if live is not None:
            for node in getattr(live, "nodes", None) or []:
                if getattr(node, "run_id", None) == run_id:
                    spec = node
                    break
        spend = self._worker_spend.get(run_id)
        live_used = spend.rounds_used if spend is not None else None
        live_limit = spend.rounds_limit if spend is not None else None
        live_tokens = spend.tokens_spent if spend is not None else None
        ceiling = getattr(spec, "token_ceiling", None) if spec is not None else None
        spec_rounds = getattr(spec, "max_rounds", None) if spec is not None else None
        if spec is not None and guard is None:
            timeout_s = getattr(getattr(spec, "policy", None), "timeout_s", None)
            if timeout_s:
                bits.append(f"超时阈值 {int(timeout_s)}s")
        if live_used is not None and live_limit is not None and live_limit > 0:
            bits.append(f"已用 {int(live_used)}/{int(live_limit)} 轮")
        elif spec_rounds:
            bits.append(f"轮次上限 {int(spec_rounds)}")
        if live_tokens is not None and ceiling:
            bits.append(f"已花 {int(live_tokens)}/{int(ceiling)}")
        elif live_tokens is not None:
            bits.append(f"已花 {int(live_tokens)}")
        elif ceiling:
            bits.append(f"token 顶 {int(ceiling)}")
        return bits

    def worker_progress_summary(self: CoordinationSession) -> str:
        """Human lines for idle-patrol / idle-yield: role / elapsed / busy / budgets."""
        now = time.monotonic()
        lines: list[str] = []
        busy_label = {
            "llm": "LLM 调用中",
            "tool": "工具执行中",
            "verify": "有界验证中（可用 cancel_worker 打断）",
            "arbitrate": "等待主管仲裁",
        }
        for run_id, role in self.running_workers():
            started = self._worker_started_at.get(run_id)
            elapsed = int(now - started) if started is not None else 0
            status = busy_label.get(self._busy_workers.get(run_id, ""), "轮间/无进行中调用")
            bits = [f"已运行 {elapsed}s", status, *self.worker_budget_facts(run_id)]
            lines.append(f"  - 【{role}】run_id={run_id} " + " · ".join(bits))
        done = len(self.completed_run_ids)
        total = self.total_workers
        head = f"队员进展（已完成 {done}/{total}）："
        if not lines:
            return f"{head}无在跑队员。"
        return head + "\n" + "\n".join(lines)

    def invalidate_verify_cache(
        self: CoordinationSession, *, reason: str = "landed"
    ) -> int:
        """Drop cached verify results after the workspace changed.

        Clears ``_verify_cache`` and bumps ``_verify_generation`` so in-flight
        producers still finish for awaiters but do **not** re-enter the cache
        (avoids cancel storms while preventing stale greens).
        Returns how many cache entries were dropped.
        """
        dropped = len(self._verify_cache)
        self._verify_cache.clear()
        self._verify_generation += 1
        if dropped:
            with contextlib.suppress(Exception):
                logger.info(
                    "coordination.verify_cache_invalidated",
                    execution_id=self.execution_id,
                    reason=reason,
                    dropped=dropped,
                    generation=self._verify_generation,
                )
        return dropped

    async def coalesce_verify(
        self: CoordinationSession,
        fingerprint: str,
        runner: Any,
    ) -> tuple[Any, str]:
        """Run or join a sibling verify for ``fingerprint``.

        Returns ``(tool_result, source)`` where ``source`` is ``run`` | ``inflight``
        | ``cache``. Completed results (success or failure / budget) are cached for
        the rest of this execution so overlapping sibling ``test_run`` calls do not
        double-burn the minute-level budget. A land that bumps generation prevents
        a late producer from re-caching a pre-write result.
        """
        from dataclasses import replace

        key = (fingerprint or "").strip()
        if not key:
            result = await runner()
            return result, "run"

        cached = self._verify_cache.get(key)
        if cached is not None:
            return replace(cached), "cache"

        existing = self._verify_inflight.get(key)
        if existing is not None:
            shared = await existing
            return replace(shared), "inflight"

        generation = self._verify_generation
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._verify_inflight[key] = fut
        try:
            result = await runner()
            snap = replace(result)
            # Only cache when the workspace generation is unchanged since start.
            if self._verify_generation == generation:
                self._verify_cache[key] = snap
            if not fut.done():
                fut.set_result(snap)
            return result, "run"
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._verify_inflight.pop(key, None)

    def resolve_cancel_target(self: CoordinationSession, raw: str) -> CancelResolution:
        """Resolve a CEO-supplied ``cancel_worker`` arg to a live worker's full run_id.

        The CEO only sees role / short names in coordination events, but the
        scheduler matches the engine-minted full run_id **exactly** — so a short
        name silently never cancels. Resolve tolerantly against in-flight workers:

        1. exact full run_id hit,
        2. else a unique ``_{raw}`` suffix match (short name = the run_id tail),
        3. else a unique role-name match.

        Multiple matches → ``ambiguous`` (candidates listed); none → ``not_found``.
        """
        target = (raw or "").strip()
        if not target:
            return CancelResolution(run_id=None, reason="not_found")
        if target in self._running_workers:
            return CancelResolution(run_id=target, reason="exact")
        suffix = f"_{target}"
        suffix_hits = sorted(rid for rid in self._running_workers if rid.endswith(suffix))
        if len(suffix_hits) == 1:
            return CancelResolution(run_id=suffix_hits[0], reason="suffix")
        role_hits = sorted(rid for rid, role in self._running_workers.items() if role == target)
        if len(role_hits) == 1:
            return CancelResolution(run_id=role_hits[0], reason="role")
        candidates = tuple(sorted(set(suffix_hits) | set(role_hits)))
        return CancelResolution(
            run_id=None,
            reason="ambiguous" if candidates else "not_found",
            candidates=candidates,
        )

    def _ended_run_ids(self: CoordinationSession) -> set[str]:
        """All session-terminal worker ids (any terminal phase counts as ended).

        ``completed_run_ids`` is the primary pool (host marks COMPLETED / FAILED /
        CANCELLED / SKIPPED here). ``vacated_run_ids`` / ``failed_run_ids`` are
        unioned defensively so vacated seats still resolve if a path only stamped
        those sets.
        """
        return set(self.completed_run_ids) | set(self.vacated_run_ids) | set(
            self.failed_run_ids
        )

    def resolve_ended_worker(self: CoordinationSession, raw: str) -> CancelResolution:
        """Resolve ``raw`` to a session worker that already finished.

        Used by ``cancel_worker`` for idempotent success when the target is no
        longer in ``_running_workers`` but is confirmed ended for this session.
        Terminal phases: COMPLETED / FAILED / SKIPPED / CANCELLED (and handoff
        ownership on complete). Matching mirrors :meth:`resolve_cancel_target`
        against :meth:`_ended_run_ids` (+ live_plan roles). Ambiguous / unknown →
        ``not_found`` / ``ambiguous``.
        """
        target = (raw or "").strip()
        if not target:
            return CancelResolution(run_id=None, reason="not_found")
        done = self._ended_run_ids()
        if target in done:
            return CancelResolution(run_id=target, reason="exact")
        suffix = f"_{target}"
        suffix_hits = sorted(rid for rid in done if rid.endswith(suffix))
        if len(suffix_hits) == 1:
            return CancelResolution(run_id=suffix_hits[0], reason="suffix")
        role_by_id: dict[str, str] = {}
        live = self.live_plan
        if live is not None:
            for node in getattr(live, "nodes", ()) or ():
                rid = getattr(node, "run_id", "") or ""
                if rid in done:
                    role_by_id[rid] = (getattr(node, "role", None) or rid).strip() or rid
        role_hits = sorted(rid for rid, role in role_by_id.items() if role == target)
        if len(role_hits) == 1:
            return CancelResolution(run_id=role_hits[0], reason="role")
        candidates = tuple(sorted(set(suffix_hits) | set(role_hits)))
        return CancelResolution(
            run_id=None,
            reason="ambiguous" if candidates else "not_found",
            candidates=candidates,
        )

    def resolve_pending_worker(self: CoordinationSession, raw: str) -> CancelResolution:
        """Resolve ``raw`` to a live_plan node that has not started and has not ended.

        Pending = on current ``live_plan``, not in ``_running_workers``, not in
        :meth:`_ended_run_ids`. Used by ``cancel_worker`` to withdraw a queued node
        (mark skipped / vacated) before Wave dispatches it. Matching mirrors
        :meth:`resolve_cancel_target` (exact / unique suffix / unique role).
        Ambiguous / unknown / no live_plan → ``not_found`` / ``ambiguous``.
        """
        target = (raw or "").strip()
        if not target:
            return CancelResolution(run_id=None, reason="not_found")
        live = self.live_plan
        if live is None:
            return CancelResolution(run_id=None, reason="not_found")
        nodes = list(getattr(live, "nodes", ()) or ())
        if not nodes:
            return CancelResolution(run_id=None, reason="not_found")
        ended = self._ended_run_ids()
        running = set(self._running_workers)
        pending: dict[str, str] = {}
        for node in nodes:
            rid = (getattr(node, "run_id", "") or "").strip()
            if not rid or rid in ended or rid in running:
                continue
            pending[rid] = (getattr(node, "role", None) or rid).strip() or rid
        if not pending:
            return CancelResolution(run_id=None, reason="not_found")
        if target in pending:
            return CancelResolution(run_id=target, reason="exact")
        suffix = f"_{target}"
        suffix_hits = sorted(rid for rid in pending if rid.endswith(suffix))
        if len(suffix_hits) == 1:
            return CancelResolution(run_id=suffix_hits[0], reason="suffix")
        role_hits = sorted(rid for rid, role in pending.items() if role == target)
        if len(role_hits) == 1:
            return CancelResolution(run_id=role_hits[0], reason="role")
        candidates = tuple(sorted(set(suffix_hits) | set(role_hits)))
        return CancelResolution(
            run_id=None,
            reason="ambiguous" if candidates else "not_found",
            candidates=candidates,
        )

    def vacate_pending_worker(self: CoordinationSession, run_id: str) -> None:
        """Formally withdraw a queued (not-yet-running) plan node.

        Stamps the seat as session-terminal SKIPPED (completed + vacated) and adds
        ``run_id`` to ``cancel_ids`` so Wave will not dispatch it (and will cancel
        if a race already launched). Does not touch other workers — never retargets.
        """
        rid = (run_id or "").strip()
        if not rid:
            return
        self.mark_worker_completed(rid)
        self.vacated_run_ids.add(rid)
        self.request_cancel(rid)

    def suggest_cancel_by_plan_role(
        self: CoordinationSession, raw: str
    ) -> tuple[str, str] | None:
        """Hint-only: unique running worker sharing the live_plan role of ``raw``.

        Used when ``cancel_worker`` truly cannot resolve ``raw`` (not running, not
        ended). Looks up ``raw`` on ``live_plan`` (exact / unique suffix), then if
        that node's role has exactly one in-flight worker, returns
        ``(run_id, role)`` so the tool can name it — never auto-cancels.
        """
        target = (raw or "").strip()
        if not target:
            return None
        live = self.live_plan
        if live is None:
            return None
        nodes = list(getattr(live, "nodes", ()) or ())
        if not nodes:
            return None
        role: str | None = None
        exact = next(
            (
                n
                for n in nodes
                if (getattr(n, "run_id", "") or "").strip() == target
            ),
            None,
        )
        if exact is not None:
            role = (getattr(exact, "role", None) or "").strip() or None
        else:
            suffix = f"_{target}"
            suffix_nodes = [
                n
                for n in nodes
                if (getattr(n, "run_id", "") or "").endswith(suffix)
            ]
            if len(suffix_nodes) == 1:
                role = (getattr(suffix_nodes[0], "role", None) or "").strip() or None
        if not role:
            return None
        role_hits = sorted(
            rid for rid, r in self._running_workers.items() if r == role
        )
        if len(role_hits) != 1:
            return None
        return role_hits[0], role

    def cancel_run_ids(self: CoordinationSession) -> frozenset[str]:
        return frozenset(self.cancel_ids)

    def update_draft(self: CoordinationSession, draft: str) -> None:
        self.draft = draft

    def stash_interjection(
        self: CoordinationSession, interjection_id: str, payload: dict[str, Any]
    ) -> None:
        """Hold enqueue material for ``queue_user_message`` (process-local)."""
        self.pending_interjections[interjection_id] = dict(payload)

    def take_interjection(
        self: CoordinationSession, interjection_id: str
    ) -> dict[str, Any] | None:
        return self.pending_interjections.pop(interjection_id, None)

    def get_interjection(
        self: CoordinationSession, interjection_id: str
    ) -> dict[str, Any] | None:
        return self.pending_interjections.get(interjection_id)
