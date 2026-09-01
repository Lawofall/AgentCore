"""Mid-drive redirect: cancel + hot continue / cold ``_redir`` handoff."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.team_synthesis import maybe_emit_team_synthesis_preview
from agentcore.runtime.events import run_progress
from agentcore.runtime.runs.redirect_queue import RunRedirectRequest, take_redirects
from agentcore.runtime.runs.stop_queue import take_stops
from agentcore.runtime.runs.types import RunSpec, RunState

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.session import RunSession

type DelegateTool = Any

logger = get_logger(__name__)


@dataclass
class RedirectController:
    """Mutable redirect / cancel state owned by one ``drive`` call."""

    tool: DelegateTool
    plan: RunPlan
    execution_id: str
    worker_gate: Any
    session: Any  # coordination session or None
    total: int
    cancel_ids: set[str] = field(default_factory=set)
    stop_ids: set[str] = field(default_factory=set)
    redirect_feedback: dict[str, RunRedirectRequest] = field(default_factory=dict)
    hot_revision_states: dict[str, RunState] = field(default_factory=dict)
    author_sessions: dict[str, RunSession] = field(default_factory=dict)
    _coord_seen: set[str] = field(default_factory=set)

    def cancel_run_ids(self) -> frozenset[str]:
        for redir in take_redirects(self.execution_id):
            self.cancel_ids.add(redir.run_id)
            self.redirect_feedback[redir.run_id] = redir
            self.stop_ids.discard(redir.run_id)
            logger.info(
                "delegate.run_redirect_accepted",
                execution_id=self.execution_id,
                run_id=redir.run_id,
                feedback_preview=redir.feedback[:120],
            )
        # User「只停这项工作」: same cancel set, never redirect_feedback (no hot/cold).
        for stop in take_stops(self.execution_id):
            if stop.run_id:
                targets = (stop.run_id,)
            else:
                targets = tuple(n.run_id for n in self.plan.nodes)
                self.redirect_feedback.clear()
            for rid in targets:
                self.cancel_ids.add(rid)
                self.stop_ids.add(rid)
                self.redirect_feedback.pop(rid, None)
            logger.info(
                "delegate.run_stop_accepted",
                execution_id=self.execution_id,
                run_id=stop.run_id,
                target_count=len(targets),
            )
        # Coordination cancel_worker merges into the same cancel set.
        if self.session is not None:
            self.cancel_ids.update(self.session.cancel_run_ids())
        return frozenset(self.cancel_ids)

    def stop_run_ids(self) -> frozenset[str]:
        """Subset of ``cancel_ids`` that came from user run-stop (not redirect)."""
        return frozenset(self.stop_ids)

    def timeout_run_ids(self) -> frozenset[str]:
        """Subset of ``cancel_ids`` the hard-timeout guard force-cancelled.

        Coordination merges timeout kills into the same cancel channel as
        ``cancel_worker`` / redirect, so Wave can only tell them apart by asking the
        session — otherwise a worker killed on the timeout ceiling is reported as
        「已改方向」.
        """
        if self.session is None:
            return frozenset()
        return frozenset(
            rid for rid in self.cancel_ids if self.session.was_timeout_force_cancelled(rid)
        )

    def cold_fallback(self, original: RunSpec, redir: RunRedirectRequest) -> str:
        """Append a same-role handoff node (``_redir`` + replaces_run_id + steer)."""
        # Unique handoff id if the same author cold-falls more than once this drive.
        base = f"{original.run_id}_redir"
        new_id = base
        n = 2
        while self.plan.by_id(new_id) is not None:
            new_id = f"{base}{n}"
            n += 1
        new_spec = RunSpec(
            run_id=new_id,
            task=original.task,
            kind=original.kind,
            agent_id=new_id,
            agent_name=original.agent_name,
            role=original.role,
            system_prompt_supplement=original.system_prompt_supplement,
            tools=original.tools,
            model=original.model,
            thinking=original.thinking,
            deliverable=original.deliverable,
            stance=original.stance,
            group=original.group,
            round=original.round,
            depends_on=original.depends_on,
            parent_run_id=original.parent_run_id,
            depth=original.depth,
            retrieval_budget=original.retrieval_budget,
            token_ceiling=original.token_ceiling,
            policy=original.policy,
            sibling_summary=original.sibling_summary,
            replaces_run_id=original.run_id,
            steer=redir.feedback,
        )
        self.plan.add(new_spec)
        return new_id

    async def try_hot_continue(
        self,
        original: RunSpec,
        state: RunState,
        redir: RunRedirectRequest,
    ) -> bool:
        """Salvage → continue_run. True on successful hot path; False → caller cold-falls.

        Continuation ids follow the same 唤回闸 as CEO ``continue_from``:
        ``{run_id}_rev{recall_count+1}`` with ``continues_run_id`` = session root on the wire.
        A second redirect on the same author continues from the author session so
        numbering increments (``_rev2``, …) instead of minting a duplicate ``_rev1``.
        """
        from agentcore.runtime.runs import RunPhase, RunSession, continue_run
        from agentcore.runtime.runs.constants import DEFAULT_RECALL_LIMIT
        from agentcore.runtime.runs.salvage import is_continuable_transcript
        from agentcore.runtime.runs.types import ContextBlock

        existing = self.author_sessions.get(original.run_id)
        if existing is None and self.tool._session_store is not None:
            existing = self.tool._session_store.get(original.run_id)
        if existing is not None and existing.transcript:
            session = RunSession(
                run_id=original.run_id,
                spec=existing.spec,
                transcript=list(existing.transcript),
                content=existing.content or "",
                recall_count=existing.recall_count,
                partial=existing.partial,
            )
        elif is_continuable_transcript(state.transcript):
            session = RunSession(
                run_id=original.run_id,
                spec=original,
                transcript=list(state.transcript),
                content=state.content or "",
                recall_count=0,
                partial=True,
            )
        else:
            return False
        if session.recall_count >= DEFAULT_RECALL_LIMIT:
            logger.info(
                "delegate.run_redirect_hot_capped",
                execution_id=self.execution_id,
                run_id=original.run_id,
                recall_count=session.recall_count,
            )
            return False
        continuation_run_id = f"{original.run_id}_rev{session.recall_count + 1}"
        context_blocks = [
            ContextBlock(
                channel="continuation",
                heading="本次改方向（用户立即改此人）",
                body=redir.feedback,
            )
        ]
        try:
            rev_state = await continue_run(
                session=session,
                feedback=redir.feedback,
                continuation_run_id=continuation_run_id,
                llm=self.tool._llm,
                tools=self.tool._tools,
                sink=self.tool._sink,
                base_tool_context=self.tool._base_tool_context,
                execution_id=self.execution_id,
                profile_set=self.tool._profile_set,
                approval_gate=self.worker_gate,
                context_blocks=context_blocks,
                parent_run_id=original.parent_run_id,
            )
        except Exception:  # noqa: BLE001 — hot fail → cold fallback
            logger.exception(
                "delegate.run_redirect_hot_failed",
                execution_id=self.execution_id,
                run_id=original.run_id,
            )
            return False
        if rev_state.phase is not RunPhase.COMPLETED or not (rev_state.content or "").strip():
            logger.info(
                "delegate.run_redirect_hot_empty",
                execution_id=self.execution_id,
                run_id=original.run_id,
                phase=rev_state.phase.value,
            )
            return False
        committed = RunSession(
            run_id=original.run_id,
            spec=original,
            transcript=list(rev_state.transcript) or list(session.transcript),
            content=rev_state.content,
            recall_count=session.recall_count + 1,
            partial=False,
        )
        self.author_sessions[original.run_id] = committed
        if self.tool._session_store is not None:
            self.tool._session_store.put(committed)
        if self.tool._session_saver is not None:
            await self.tool._session_saver(committed)
        self.hot_revision_states[continuation_run_id] = rev_state
        # 用户点的「立即改此人」——记进总数，同时标成用户促成，别算到队友互检头上。
        self.tool.note_continuation(continuation_run_id, by_user=True)
        logger.info(
            "delegate.run_redirect_hot",
            execution_id=self.execution_id,
            cancelled_run_id=original.run_id,
            continuation_run_id=continuation_run_id,
            recall_count=committed.recall_count,
            feedback_preview=redir.feedback[:120],
        )
        return True

    def _emit_progress(self, completed: dict[str, RunState]) -> int:
        from agentcore.runtime.runs import RunPhase

        done = sum(1 for s in completed.values() if s.phase is RunPhase.COMPLETED)
        # Count successful hot revisions toward progress (they are not plan nodes).
        done += sum(
            1 for s in self.hot_revision_states.values() if s.phase is RunPhase.COMPLETED
        )
        self.tool._sink.emit(run_progress(done, self.total))
        maybe_emit_team_synthesis_preview(
            self.tool._sink, self.plan, completed, execution_id=self.execution_id
        )
        return done

    async def apply_pending_redirects(self, completed: dict[str, RunState]) -> None:
        """Apply redirects for CANCELLED authors (hot prefer, cold fallback).

        Loop: a hot continue may enqueue the next「立即改此人」before returning;
        without a re-drain that steer would sit until wave exit and be mis-classified
        as ignored (especially solo workers with no sibling completion to re-enter
        on_progress).
        """
        from agentcore.runtime.runs import RunPhase

        while True:
            for redir in take_redirects(self.execution_id):
                self.cancel_ids.add(redir.run_id)
                self.redirect_feedback[redir.run_id] = redir
            applied = False
            for run_id, redir in list(self.redirect_feedback.items()):
                state = completed.get(run_id)
                if state is None or state.phase is not RunPhase.CANCELLED:
                    continue
                original = self.plan.by_id(run_id)
                self.redirect_feedback.pop(run_id)
                if original is None:
                    continue
                applied = True
                hot_ok = await self.try_hot_continue(original, state, redir)
                if hot_ok:
                    continue
                new_id = self.cold_fallback(original, redir)
                self.total = len(self.plan.nodes)
                logger.info(
                    "delegate.run_redirect_cold",
                    execution_id=self.execution_id,
                    cancelled_run_id=run_id,
                    new_run_id=new_id,
                    feedback_preview=redir.feedback[:120],
                )
            if not applied:
                break

    async def on_progress(self, completed: dict[str, RunState]) -> None:
        """WaveScheduler on_progress: sessions, progress events, redirects."""
        # 单个 run 完成即登记现场，使同批「depends_on X + continue_from X」成立。
        from agentcore.runtime.delegate.continuation import register_completed_session

        newly_registered: list = []
        for rid, st in completed.items():
            sess = register_completed_session(
                self.tool, self.plan, rid, st, author_sessions=self.author_sessions
            )
            if sess is not None:
                newly_registered.append(sess)
        if self.tool._session_saver is not None:
            for sess in newly_registered:
                await self.tool._session_saver(sess)

        self._emit_progress(completed)

        # Phase 2: post worker_completed into the coordination queue (background drive).
        if self.session is not None:
            from agentcore.runtime.coordination.host import post_worker_progress

            self._coord_seen = post_worker_progress(
                self.session,
                self.plan,
                dict(completed),
                sink=self.tool._sink,
                execution_id=self.execution_id,
                previously=self._coord_seen,
            )

        await self.apply_pending_redirects(completed)
        # Refresh progress after hot/cold follow-ups (revision nodes / new _redir).
        self._emit_progress(completed)

    async def drain_post_wave(
        self,
        results: dict[str, RunState],
        *,
        executor: Callable[..., Awaitable[RunState]],
        max_parallel: int,
        on_skipped: Callable[..., None],
    ) -> dict[str, RunState]:
        """Post-wave redirect drain + optional cold handoff scheduler pass."""
        from agentcore.runtime.runs import RunPhase, WaveScheduler

        # Fold successful hot-redirect revisions into the result map (usage / CEO format /
        # session roster). They are not plan nodes — continue_run already emitted their wire.
        results.update(self.hot_revision_states)

        # Post-wave drain: a redirect that landed while the last hot continue was running
        # (or after the final on_progress) still targets a CANCELLED author — apply it
        # here so a second「立即改此人」is not mis-classified as ignored. Prefer hot;
        # cold appends a ``_redir`` and runs one more scheduler pass for that handoff only.
        post_wave_cold = False
        for redir in take_redirects(self.execution_id):
            self.redirect_feedback[redir.run_id] = redir
        for run_id, redir in list(self.redirect_feedback.items()):
            state = results.get(run_id)
            if state is None or state.phase is not RunPhase.CANCELLED:
                continue
            original = self.plan.by_id(run_id)
            self.redirect_feedback.pop(run_id)
            if original is None:
                continue
            hot_ok = await self.try_hot_continue(original, state, redir)
            if hot_ok:
                continue
            new_id = self.cold_fallback(original, redir)
            post_wave_cold = True
            logger.info(
                "delegate.run_redirect_cold",
                execution_id=self.execution_id,
                cancelled_run_id=run_id,
                new_run_id=new_id,
                feedback_preview=redir.feedback[:120],
            )
        if post_wave_cold:
            from agentcore.llm.turn_auth_dead import credential_source_from_llm
            from agentcore.runtime.turn.token_budget import resolve_wave_budget_hooks

            should_stop = resolve_wave_budget_hooks(
                credential_source=credential_source_from_llm(
                    getattr(self.tool, "_llm", None)
                ),
            )
            more = await WaveScheduler(max_parallel).run(
                self.plan,
                executor,
                seed_completed=results,
                cancel_run_ids=self.cancel_run_ids,
                stop_run_ids=self.stop_run_ids,
                timeout_run_ids=self.timeout_run_ids,
                on_progress=self.on_progress,
                on_boundary=None,
                on_skipped=on_skipped,
                should_stop=should_stop,
            )
            results.update(more)
        results.update(self.hot_revision_states)
        return results

    def audit_ignored_redirects(self) -> None:
        """Audit redirects whose target was already terminal and not CANCELLED."""
        # 跑一半改方向 · 忽略路径 (run_redirect Step 4): a redirect whose target was already
        # terminal *and not CANCELLED* (completed/failed) when drained never became a
        # cancel+hot/cold apply — record each once so the run detail can surface「改方向未生效」
        # and offer an explicit accept. Audit-only (no new SSE event).
        ignored_redirects: dict[str, RunRedirectRequest] = dict(self.redirect_feedback)
        for redir in take_redirects(self.execution_id):
            ignored_redirects.setdefault(redir.run_id, redir)
        if ignored_redirects:
            from agentcore.runtime.audit.hooks import on_run_redirect_ignored

            for run_id, redir in ignored_redirects.items():
                logger.info(
                    "delegate.run_redirect_ignored",
                    execution_id=self.execution_id,
                    run_id=run_id,
                    feedback_preview=redir.feedback[:120],
                )
                on_run_redirect_ignored(
                    run_id=run_id,
                    feedback=redir.feedback,
                    execution_id=self.execution_id,
                )
