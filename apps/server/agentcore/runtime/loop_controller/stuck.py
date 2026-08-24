"""Stuck / intervention detector (sliding window + graded nudge/finalize).

Split from ``loop_controller`` — pure move. Consumed only as a mixin by
:class:`~agentcore.runtime.loop_controller.LoopController`.
"""

from __future__ import annotations

from collections import Counter, deque

from .types import (
    Intervention,
    StuckReason,
    StuckSignal,
    ToolAttempt,
)


class StuckInterventionMixin:
    """Sliding-window stuck patterns + empty/unproductive/delivery-idle ladders."""

    # Declared on LoopController.__init__; listed for type-checkers.
    _window: int
    _threshold: int
    _empty_threshold: int
    _unproductive_threshold: int
    _recent: deque[ToolAttempt]
    _nudged: bool
    _consecutive_empty: int
    _consecutive_unproductive: int
    _investigation_tools: frozenset[str]
    _investigation_calls: int
    _investigation_rounds: int
    _local_recon_calls: int
    _convergence_finalize_rounds: int
    _convergence_spin_rounds: int
    _same_target_investigation_streak: int
    _form_prose: bool
    _landing_succeeded: bool
    _delivery_idle_nudge_rounds: int
    _delivery_idle_narrow_rounds: int
    _delivery_idle_recon: bool
    _delivery_idle_report: bool
    _idle_investigation_rounds: int
    _delivery_idle_nudged: bool
    _delivery_idle_narrowed: bool
    _delivery_idle_narrow_apply_pending: bool
    _validation_thrash_latched: bool

    def note_empty_round(self, is_empty: bool) -> None:
        """Track consecutive empty-response rounds (B2).

        An empty round = the model produced no content and called no tool. A
        non-empty round (real answer OR a tool call) resets the streak — so only
        *consecutive* empties escalate toward a degraded finish.
        """
        self._consecutive_empty = self._consecutive_empty + 1 if is_empty else 0

    def empty_response_action(self, *, finish_reason: str | None = None) -> Intervention:
        """Decide what to do after an empty round (B2 degraded ladder).

        ``finish_reason == "length"`` (protocol-proven truncation with empty body +
        no tools) skips the default one-shot Continue and finalizes immediately —
        retrying will not grow the output budget. Ordinary silent empties still
        ``CONTINUE`` once, then ``FINALIZE`` once the consecutive-empty streak hits
        the threshold (the turn ends as degraded rather than blank).
        """
        if finish_reason == "length":
            return Intervention.FINALIZE
        if self._consecutive_empty >= self._empty_threshold:
            return Intervention.FINALIZE
        return Intervention.CONTINUE

    def note_round_productivity(
        self,
        *,
        had_tool_calls: bool,
        all_failed: bool,
        had_content: bool,
        all_parse_failures: bool = False,
    ) -> None:
        """Track consecutive *unproductive* rounds (B2 无产出早停).

        An unproductive round = the model called ≥1 tool, every call failed, and it
        produced no content. Any productive round — content this round, a tool
        success, or a no-tool round (handled by the empty/degraded path) — resets
        the streak, so only a sustained all-failing-no-output run escalates.

        Pure protocol failures（仅 ``args_parse_failed`` / ``parse_failure``）不计入
        streak（既不递增也不重置），避免因纯协议失败触发 UNPRODUCTIVE。
        """
        if all_parse_failures:
            return
        unproductive = had_tool_calls and all_failed and not had_content
        self._consecutive_unproductive = self._consecutive_unproductive + 1 if unproductive else 0

    def unproductive_early_stop(self) -> bool:
        """True once the consecutive-unproductive streak hits the threshold."""
        return self._consecutive_unproductive >= self._unproductive_threshold

    @property
    def investigation_tool_names(self) -> frozenset[str]:
        """Read-only investigation tool names classified for this run."""
        return self._investigation_tools

    @property
    def investigation_calls(self) -> int:
        """Cumulative read-only investigation calls this run (finalize-log diagnostic)."""
        return self._investigation_calls

    @property
    def local_recon_calls(self) -> int:
        """Cumulative local peek calls (file_list / file_read / grep) this run."""
        return self._local_recon_calls

    @property
    def investigation_rounds(self) -> int:
        """Rounds with ≥1 *successful* investigation call (all-fail rounds do not count)."""
        return self._investigation_rounds

    @property
    def form_prose(self) -> bool:
        """True when deliverable.form=prose (reflection must not urge write tools)."""
        return self._form_prose

    @property
    def landing_succeeded(self) -> bool:
        """True once a product landing write succeeded."""
        return self._landing_succeeded

    @property
    def delivery_idle_nudge_rounds(self) -> int:
        """Configured soft nudge threshold (0 = off). Product factory always 0."""
        return self._delivery_idle_nudge_rounds

    @property
    def delivery_idle_narrow_rounds(self) -> int:
        """Configured tool-narrow threshold (0 = off). Factory never arms files-expected."""
        return self._delivery_idle_narrow_rounds

    @property
    def delivery_idle_recon(self) -> bool:
        """True when soft nudge uses recon (conclude) copy, not write-disk copy."""
        return self._delivery_idle_recon

    @property
    def delivery_idle_report(self) -> bool:
        """Compat flag for report-landing copy; factory never sets this."""
        return self._delivery_idle_report

    @property
    def delivery_idle_rounds(self) -> int:
        """Consecutive investigation-only rounds with no landing (delivery-idle clock)."""
        return self._idle_investigation_rounds

    @property
    def delivery_idle_nudged(self) -> bool:
        """True after the soft delivery-idle nudge was injected."""
        return self._delivery_idle_nudged

    @property
    def delivery_idle_narrowed(self) -> bool:
        """True after the delivery-idle narrow steer was latched."""
        return self._delivery_idle_narrowed

    def delivery_idle_nudge_due(self) -> bool:
        """True when idle rounds hit the soft nudge bar (one-shot)."""
        bar = self._delivery_idle_nudge_rounds
        if bar <= 0 or self._landing_succeeded or self._delivery_idle_nudged:
            return False
        if self._delivery_idle_narrowed:
            return False
        return self._idle_investigation_rounds >= bar

    def mark_delivery_idle_nudged(self) -> None:
        """Latch the one-shot delivery-idle soft nudge."""
        self._delivery_idle_nudged = True

    def delivery_idle_narrow_due(self) -> bool:
        """True when idle rounds hit the tool-narrow bar (one-shot; not FINALIZE)."""
        bar = self._delivery_idle_narrow_rounds
        if bar <= 0 or self._landing_succeeded or self._delivery_idle_narrowed:
            return False
        return self._idle_investigation_rounds >= bar

    def mark_delivery_idle_narrowed(self) -> None:
        """Latch narrow steer + pending allowlist apply for the react loop."""
        self._delivery_idle_narrowed = True
        self._delivery_idle_nudged = True
        self._delivery_idle_narrow_apply_pending = True

    def take_delivery_idle_narrow_apply(self) -> bool:
        """Consume one-shot pending tool-surface narrow (loop applies whitelist)."""
        if not self._delivery_idle_narrow_apply_pending:
            return False
        self._delivery_idle_narrow_apply_pending = False
        return True

    def convergence_action(self) -> Intervention:
        """Over-investigation finalize: same-target spinning; leftover absolute cap.

        Spinning = consecutive investigation-only rounds re-reading the same targets
        (same tool+args fingerprints, or a subset of the prior round). Reading new
        files each round does not trip spinning. The absolute ``finalize_rounds``
        knob is leftover API (disabled at ``<= 0``); the product factory always
        passes 0 — many distinct-target reads are not a runaway.
        """
        if (
            self._convergence_spin_rounds > 0
            and self._same_target_investigation_streak >= self._convergence_spin_rounds
        ):
            return Intervention.FINALIZE
        if self._convergence_finalize_rounds <= 0:
            return Intervention.CONTINUE
        if self._investigation_rounds >= self._convergence_finalize_rounds:
            return Intervention.FINALIZE
        return Intervention.CONTINUE

    @property
    def same_target_investigation_streak(self) -> int:
        """Consecutive investigation-only rounds re-reading the same targets."""
        return self._same_target_investigation_streak

    def is_thrashing(self) -> bool:
        """Read-only run-health verdict for a HARD-CEILING termination boundary.

        When a hard ceiling (token backstop / max rounds) forces the run to stop —
        as opposed to the model choosing to finish — this routes the finalize: a
        *thrashing* run (sustained all-failing-no-output rounds, over-investigation
        spinning, leftover absolute-cap if constructed, or a validation fingerprint
        re-hit after path-stop
        steer) should finish DEGRADED and surface an observable signal, while an
        *on-track* run (made real progress, just ran out of budget) should finalize
        normally and deliver.

        Distinct from the per-round governance triggers (which stop the loop
        mid-run): those already fired earlier if they were going to, so at a natural
        max-rounds exit this is usually ``False`` (= deliver). It matters most for the
        token backstop, which can break the loop at any round. No side effects.
        """
        if self._validation_thrash_latched:
            return True
        if self.unproductive_early_stop():
            return True
        return self.convergence_action() is Intervention.FINALIZE

    def detect(self) -> StuckSignal | None:
        """Return the strongest stuck signal in the window, or ``None``.

        Priority: repeated failure (most actionable) > repeated non-investigation
        success > A-B-A-B.
        Successful identical investigation calls are not a stuck pattern (re-read / paging).
        """
        if len(self._recent) < self._threshold:
            return None

        fail_counts = Counter(a.fingerprint for a in self._recent if not a.success)
        for attempt in reversed(self._recent):
            if not attempt.success and fail_counts[attempt.fingerprint] >= self._threshold:
                return StuckSignal(
                    StuckReason.REPEATED_FAILURE,
                    attempt.tool_name,
                    fail_counts[attempt.fingerprint],
                )

        # Identical successful non-investigation calls (compute / code_execute) — not
        # read paging. Investigation tools stay exempt (re-read / paging is legitimate).
        success_counts = Counter(
            a.fingerprint
            for a in self._recent
            if a.success and a.tool_name not in self._investigation_tools
        )
        for attempt in reversed(self._recent):
            if (
                attempt.success
                and attempt.tool_name not in self._investigation_tools
                and success_counts[attempt.fingerprint] >= self._threshold
            ):
                return StuckSignal(
                    StuckReason.REPEATED_CALL,
                    attempt.tool_name,
                    success_counts[attempt.fingerprint],
                )

        if len(self._recent) >= 4:
            w, x, y, z = (
                self._recent[-4],
                self._recent[-3],
                self._recent[-2],
                self._recent[-1],
            )
            if (
                w.fingerprint == y.fingerprint
                and x.fingerprint == z.fingerprint
                and w.fingerprint != x.fingerprint
            ):
                return StuckSignal(StuckReason.ALTERNATING, z.tool_name, 2)

        return None

    def decide(self, signal: StuckSignal | None) -> Intervention:
        """Map a signal to an action via a two-strike ladder.

        First trip → ``NUDGE`` and clear the window, giving the model a clean
        slate to recover (so stale repeats don't immediately re-trigger). A
        subsequent trip → ``FINALIZE``.
        """
        if signal is None:
            return Intervention.CONTINUE
        if not self._nudged:
            self._nudged = True
            self._recent.clear()
            return Intervention.NUDGE
        return Intervention.FINALIZE
