"""Convergence governance: deterministic stuck detection + graded intervention.

This runs *outside* the model — no extra LLM calls — between ReAct rounds. It
catches the three canonical mechanical loop patterns that a model does not
recognize about itself, over a sliding window of recent tool attempts:

  * repeated identical tool call  — same tool + same normalized args
  * A-B-A-B alternation           — oscillating between two calls
  * repeated identical failure    — same tool failing the same way

When a pattern trips, the controller recommends a *graded* intervention: first a
nudge (a reflection message anchored to the concrete detected fact, never
open-ended self-doubt), then a hard finalize (force a tool-free answer).

Design grounding (see 规划/收敛治理-loop_controller.md): hard round caps are a
tripwire, not a convergence mechanism; detection must be enforced in code, not
via prompt; and an injected reflection must be anchored to an external signal
(the observed repetition) or it diverges into self-flagellation / sycophancy.

Thin facade — implementation split by axis (under ``runtime/loop_controller/``):

* ``.types`` — shared types / constants / fingerprint
* ``.stuck`` — stuck / intervention detector
* ``.circuit`` — tool-failure circuit breaker
* ``.write_reject`` — same-path write-reject streak 策略机

Public import paths stay stable via re-exports below
(``agentcore.runtime.loop_controller`` / ``.<leaf>``; no flat root shims).
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping
from typing import Any

from .circuit import ToolCircuitBreakerMixin
from .stuck import StuckInterventionMixin
from .types import (
    _LANDED_SUMMARY_ECHO_STOP_STEER,
    _PERMANENT_RETIRE_STEER,
    _VALIDATION_PATH_STOP_STEER,
    DEFAULT_EMPTY_THRESHOLD,
    DEFAULT_EXEC_ENV_TIMEOUT_RETIRE,
    DEFAULT_PATH_WRITE_REJECT_STREAK,
    DEFAULT_THRESHOLD,
    DEFAULT_TOOL_FAILURE_DISABLE,
    DEFAULT_TOOL_FAILURE_WARN,
    DEFAULT_UNPRODUCTIVE_THRESHOLD,
    DEFAULT_VALIDATION_PATH_STREAK,
    DEFAULT_WINDOW,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_PERMISSION,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_VALIDATION,
    EXEC_ENV_TIMEOUT_FAMILY,
    EXEC_ENV_TIMEOUT_RETIRE_STEER,
    FORCE_SEGMENTED_NARROW_TOOLS,
    LANDING_TOOLS,
    MEMORY_TOOLS,
    ORCHESTRATION_TOOLS,
    PATH_SEGMENT_FORCE_TOOLS,
    PROGRESS_TOOLS,
    CircuitBreak,
    Intervention,
    StuckReason,
    StuckSignal,
    ToolAttempt,
    classify_segmented_write_reject,
    delivery_idle_narrow_prompt,
    delivery_idle_nudge_prompt,
    fingerprint_tool_call,
    is_exec_env_timeout,
    resolve_error_class,
)
from .write_reject import WriteRejectStreakMixin

__all__ = [
    "DEFAULT_EMPTY_THRESHOLD",
    "DEFAULT_EXEC_ENV_TIMEOUT_RETIRE",
    "DEFAULT_PATH_WRITE_REJECT_STREAK",
    "DEFAULT_THRESHOLD",
    "DEFAULT_TOOL_FAILURE_DISABLE",
    "DEFAULT_TOOL_FAILURE_WARN",
    "DEFAULT_UNPRODUCTIVE_THRESHOLD",
    "DEFAULT_VALIDATION_PATH_STREAK",
    "DEFAULT_WINDOW",
    "ERROR_CLASS_PERMANENT",
    "ERROR_CLASS_PERMISSION",
    "ERROR_CLASS_TRANSIENT",
    "ERROR_CLASS_VALIDATION",
    "EXEC_ENV_TIMEOUT_FAMILY",
    "EXEC_ENV_TIMEOUT_RETIRE_STEER",
    "FORCE_SEGMENTED_NARROW_TOOLS",
    "LANDING_TOOLS",
    "MEMORY_TOOLS",
    "ORCHESTRATION_TOOLS",
    "PATH_SEGMENT_FORCE_TOOLS",
    "PROGRESS_TOOLS",
    "CircuitBreak",
    "Intervention",
    "LoopController",
    "StuckReason",
    "StuckSignal",
    "ToolAttempt",
    "classify_segmented_write_reject",
    "delivery_idle_narrow_prompt",
    "delivery_idle_nudge_prompt",
    "fingerprint_tool_call",
    "is_exec_env_timeout",
    "resolve_error_class",
]


class LoopController(
    StuckInterventionMixin,
    ToolCircuitBreakerMixin,
    WriteRejectStreakMixin,
):
    """Sliding-window stuck detector with a two-strike intervention policy.

    One instance per ReAct run — the window and the "already nudged" flag are
    per-run state and must not be shared across concurrent runs.
    """

    def __init__(
        self,
        *,
        window: int = DEFAULT_WINDOW,
        threshold: int = DEFAULT_THRESHOLD,
        empty_threshold: int = DEFAULT_EMPTY_THRESHOLD,
        tool_failure_warn: int = DEFAULT_TOOL_FAILURE_WARN,
        tool_failure_disable: int = DEFAULT_TOOL_FAILURE_DISABLE,
        path_write_reject_streak: int = DEFAULT_PATH_WRITE_REJECT_STREAK,
        validation_path_streak: int = DEFAULT_VALIDATION_PATH_STREAK,
        unproductive_threshold: int = DEFAULT_UNPRODUCTIVE_THRESHOLD,
        convergence_finalize_rounds: int = 0,
        convergence_spin_rounds: int = DEFAULT_THRESHOLD,
        form_prose: bool = False,
        # Idle bars (nudge → optional tool narrow). Factory never arms
        # files-expected delivery_idle; recon uses nudge only. Explicit
        # construction may still set these. Orthogonal to token/timeout
        # wind_down. ≤0 disables each step.
        delivery_idle_nudge_rounds: int = 0,
        delivery_idle_narrow_rounds: int = 0,
        # True → nudge prompt is recon (conclude/handoff), not write-disk pressure.
        delivery_idle_recon: bool = False,
        # Compat: report-landing copy. Factory never sets this.
        delivery_idle_report: bool = False,
        investigation_tools: frozenset[str] = frozenset(),
        product_landing_artifacts: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._window = window
        self._threshold = threshold
        self._empty_threshold = max(1, empty_threshold)
        self._tool_failure_warn = max(1, tool_failure_warn)
        self._tool_failure_disable = max(self._tool_failure_warn, tool_failure_disable)
        self._path_write_reject_streak = max(1, path_write_reject_streak)
        self._validation_path_streak = max(1, validation_path_streak)
        self._unproductive_threshold = max(1, unproductive_threshold)
        self._recent: deque[ToolAttempt] = deque(maxlen=window)
        self._nudged = False
        self._investigation_tools = investigation_tools
        # ``investigation_calls`` = cumulative read-only calls (run-scoped, incl. failures);
        # ``investigation_rounds`` = rounds with ≥1 *successful* investigation call (all-fail
        # rounds gather no intel → do not spend breadth budget). Safety net / team_gate
        # trigger on ROUNDS; a parallel batch still counts once. Calls still feed diagnostics.
        self._investigation_calls = 0
        self._investigation_rounds = 0
        # Local file peeks only (file_list / file_read / grep) — team_gate local-edit path.
        self._local_recon_calls = 0
        # Over-investigation safety net (收敛治理, 保险丝): absolute round ceiling plus
        # progress-aware spinning on repeated same-target reads. ``finalize_rounds <= 0``
        # disables the absolute cap; ``spin_rounds <= 0`` disables spinning detection.
        self._convergence_finalize_rounds = max(0, convergence_finalize_rounds)
        self._convergence_spin_rounds = max(0, convergence_spin_rounds)
        # Idle-round counter (factory: recon-idle; explicit delivery_idle
        # construction still uses the same clock). Soft nudge/narrow read it.
        # Landing *attempt* resets; success latches done.
        self._form_prose = bool(form_prose)
        self._delivery_idle_nudge_rounds = max(0, int(delivery_idle_nudge_rounds))
        self._delivery_idle_narrow_rounds = max(0, int(delivery_idle_narrow_rounds))
        self._delivery_idle_recon = bool(delivery_idle_recon)
        self._delivery_idle_report = bool(delivery_idle_report)
        # Declared deliverable.artifacts — dossier intermediates count as product
        # only when they match (delivery-idle landing latch). Empty = no whitelist.
        self._product_landing_artifacts: tuple[str, ...] = tuple(
            a for a in (product_landing_artifacts or ()) if a
        )
        self._idle_investigation_rounds = 0
        self._delivery_idle_nudged = False
        self._delivery_idle_narrowed = False
        self._delivery_idle_narrow_apply_pending = False
        self._landing_succeeded = False
        self._prev_investigation_fps: frozenset[str] = frozenset()
        self._same_target_investigation_streak = 0
        # B2 empty-response sub-policy: a separate consecutive-empty-round counter.
        self._consecutive_empty = 0
        # B2 tool circuit breaker: cumulative per-tool failure counts (run-scoped,
        # never cleared by the nudge window reset) + one-shot latches so each tool
        # fires its warn / disable transition at most once. ``_tool_parse_failures``
        # tracks the parse-only subset so steers can be typed without changing thresholds.
        # ``_tool_last_error`` / ``_tool_succeeded_after_fail`` enrich the same tally for
        # honest finalize injection (not a parallel counter).
        self._tool_failures: Counter[str] = Counter()
        self._tool_parse_failures: Counter[str] = Counter()
        self._tool_last_error: dict[str, str] = {}
        self._tool_succeeded_after_fail: dict[str, bool] = {}
        # Last counted failure was a liveness hang (outer/channel timeout meta).
        self._tool_liveness_last: dict[str, bool] = {}
        # Sticky: local workspace channel dead → allow disabling LANDING_TOOLS too.
        self._workspace_channel_dead: bool = False
        # Consecutive sandbox wall-clock timeouts across code_execute/test_run.
        self._exec_env_timeout_hits: int = 0
        self._tool_warned: set[str] = set()
        self._tool_disabled: set[str] = set()
        # Write/landing tools that hit disable threshold but stay enabled (强制分段).
        self._tool_segmented_forced: set[str] = set()
        # Same-path consecutive classified write rejects: path → (class, streak).
        # Trips the same ``force_segmented`` latch (not a parallel breaker).
        self._path_write_rejects: dict[str, tuple[str, int]] = {}
        # One-shot: record() saw streak ≥ threshold; consumed by tool_circuit_breaker.
        self._pending_path_force_segmented: bool = False
        # Orchestration / memory tools kept alive despite parse-only disable-threshold hits.
        self._tool_parse_kept: set[str] = set()
        # One-shot hard-stop steer from a tool that retires a family (e.g. browser
        # egress_unavailable). Consumed by :meth:`tool_circuit_breaker`.
        self._pending_retire_message: str | None = None
        # Validation same-fingerprint streak → path-stop steer (tool stays available).
        # Re-hit of an already-stopped fp → thrash latch + one-shot mid-loop hard stop
        # (no second steer; aligns with is_thrashing / ceiling DEGRADED).
        self._validation_fp_streak: tuple[str, str, int] | None = None  # fp, tool, n
        self._validation_stopped_fps: set[str] = set()
        self._pending_validation_stop: str | None = None
        self._validation_thrash_latched: bool = False
        self._pending_validation_hard_stop: bool = False
        # B2 no-output early stop: consecutive unproductive rounds (all tools failed,
        # no content). Reset by any productive round (content OR a tool success).
        self._consecutive_unproductive = 0
        # Post-delegate synthesis mode (优化六): after delegate returns, steer the CEO away
        # from repeating investigation work the team already did.
        self._post_delegate: bool = False
        self._post_delegate_investigation_count: int = 0
        # Soft team-gate nudge (协作优先阶段 3): at most once per run, captain-only.
        self._team_gate_fired: bool = False
        # Names this gate newly added to disabled_tools (for post-delegate restore).
        self._team_gate_stripped_tools: frozenset[str] = frozenset()
        # 闸后长文直答再催：每 run 一次。
        self._team_gate_direct_reject_fired: bool = False
        # Soft audit-gate nudge (协作优先阶段 3 返工环): at most once per run, captain-only.
        self._audit_gate_fired: bool = False
        # 成篇硬门：research_report / deliverable 结构信号 — nudge 后仍不可直接 end_turn。
        self._audit_hard_required: bool = False
        self._audit_includes_review: bool = False
        # Soft debate-commitment nudge: user picked a debate form on kickoff; at most once.
        self._debate_gate_fired: bool = False
        self._debate_executed: bool = False
        # Turn-token ceiling wrap-up steer (策略 A Step 2): at most once per run, captain-only.
        self._turn_token_budget_gate_fired: bool = False
        self._delegate_count: int = 0
        self._first_batch_substantial: bool = False

    def mark_post_delegate(
        self,
        *,
        node_count: int = 0,
        has_deps: bool = False,
        audit_hard: bool = False,
        includes_review: bool = False,
    ) -> None:
        """Mark that a delegate call just returned — CEO is now in synthesis mode.

        ``node_count`` / ``has_deps`` describe this batch so the audit gate can tell
        a substantial first batch (nodes ≥3 or any depends_on) from a light one.
        ``audit_hard`` / ``includes_review`` stamp成篇硬门（research_report playbook）.
        """
        self._post_delegate = True
        self._post_delegate_investigation_count = 0
        self._delegate_count += 1
        if self._delegate_count == 1:
            self._first_batch_substantial = node_count >= 3 or has_deps
            if audit_hard:
                self._audit_hard_required = True
            if includes_review:
                self._audit_includes_review = True
        elif includes_review or self._audit_hard_required:
            # 第二批起视为独立审校已派（或再次带审校角色）→ 硬门满足。
            self._audit_includes_review = True

    @property
    def has_delegated(self) -> bool:
        """True once a ``delegate`` call has returned in this run."""
        return self._post_delegate

    @property
    def delegate_count(self) -> int:
        """How many successful ``delegate`` returns this run has seen."""
        return self._delegate_count

    @property
    def first_batch_substantial(self) -> bool:
        """True if the first delegate batch was substantial (nodes ≥3 or has deps)."""
        return self._first_batch_substantial

    @property
    def audit_hard_required(self) -> bool:
        """True when long-form / research_report batches require audit before end_turn."""
        return self._audit_hard_required

    @property
    def audit_includes_review(self) -> bool:
        """True when an independent review wave already ran (playbook or follow-up)."""
        return self._audit_includes_review

    def mark_audit_satisfied(self) -> None:
        """Latch that independent review has been dispatched / included."""
        self._audit_includes_review = True

    @property
    def team_gate_fired(self) -> bool:
        """True after the soft team-gate nudge has been injected (latched)."""
        return self._team_gate_fired

    def mark_team_gate_fired(self) -> None:
        """Latch the one-shot team-gate so it cannot fire again this run."""
        self._team_gate_fired = True

    def record_team_gate_stripped(self, names: frozenset[str] | set[str]) -> None:
        """Remember tools this team_gate newly stripped (not pre-disabled ones)."""
        self._team_gate_stripped_tools = frozenset(names)

    @property
    def team_gate_stripped_tools(self) -> frozenset[str]:
        """Tools recorded as newly stripped by the last team_gate fire."""
        return self._team_gate_stripped_tools

    def take_team_gate_stripped(self) -> frozenset[str]:
        """Consume the team_gate strip set (post-delegate restore)."""
        names = self._team_gate_stripped_tools
        self._team_gate_stripped_tools = frozenset()
        return names

    @property
    def team_gate_direct_reject_fired(self) -> bool:
        """True after the post-gate long-answer reject has fired."""
        return self._team_gate_direct_reject_fired

    def mark_team_gate_direct_reject_fired(self) -> None:
        """Latch the one-shot team-gate direct-answer reject."""
        self._team_gate_direct_reject_fired = True

    @property
    def audit_gate_fired(self) -> bool:
        """True after the soft audit-gate nudge has been injected (latched)."""
        return self._audit_gate_fired

    def mark_audit_gate_fired(self) -> None:
        """Latch the one-shot audit-gate so it cannot fire again this run."""
        self._audit_gate_fired = True

    @property
    def debate_gate_fired(self) -> bool:
        """True after the soft debate-commitment nudge has been injected (latched)."""
        return self._debate_gate_fired

    def mark_debate_gate_fired(self) -> None:
        """Latch the one-shot debate-commitment gate so it cannot fire again this run."""
        self._debate_gate_fired = True

    @property
    def debate_executed(self) -> bool:
        """True once a successful ``debate`` tool return has been noted this run."""
        return self._debate_executed

    def mark_debate_executed(self) -> None:
        """Record that ``debate`` completed successfully (suppresses the commitment nudge)."""
        self._debate_executed = True

    @property
    def turn_token_budget_gate_fired(self) -> bool:
        """True after the turn-token wrap-up steer has been injected (latched)."""
        return self._turn_token_budget_gate_fired

    def mark_turn_token_budget_gate_fired(self) -> None:
        """Latch the one-shot turn-token wrap-up steer so it cannot fire again this run."""
        self._turn_token_budget_gate_fired = True

    def export_seed(self) -> dict[str, bool | int | list[str]]:
        """JSON-safe snapshot of the cross-suspension latches (turn_paused.controller).

        Includes validation path-stop fingerprints + thrash latch so write_pass /
        light_repair / resume restarts do not forget an already-empty-spun path.
        """
        return {
            "post_delegate": self._post_delegate,
            "delegate_count": self._delegate_count,
            "team_gate_fired": self._team_gate_fired,
            "team_gate_direct_reject_fired": self._team_gate_direct_reject_fired,
            "audit_gate_fired": self._audit_gate_fired,
            "first_batch_substantial": self._first_batch_substantial,
            "audit_hard_required": self._audit_hard_required,
            "audit_includes_review": self._audit_includes_review,
            "debate_gate_fired": self._debate_gate_fired,
            "debate_executed": self._debate_executed,
            "turn_token_budget_gate_fired": self._turn_token_budget_gate_fired,
            "validation_stopped_fps": sorted(self._validation_stopped_fps),
            "validation_thrash_latched": self._validation_thrash_latched,
        }

    def apply_seed(self, seed: Mapping[str, Any]) -> None:
        """Restore cross-suspension latches from a prior :meth:`export_seed` snapshot."""
        self._post_delegate = bool(seed.get("post_delegate", False))
        self._delegate_count = int(seed.get("delegate_count", 0) or 0)
        self._team_gate_fired = bool(seed.get("team_gate_fired", False))
        self._team_gate_direct_reject_fired = bool(
            seed.get("team_gate_direct_reject_fired", False)
        )
        self._audit_gate_fired = bool(seed.get("audit_gate_fired", False))
        self._first_batch_substantial = bool(seed.get("first_batch_substantial", False))
        self._audit_hard_required = bool(seed.get("audit_hard_required", False))
        self._audit_includes_review = bool(seed.get("audit_includes_review", False))
        self._debate_gate_fired = bool(seed.get("debate_gate_fired", False))
        self._debate_executed = bool(seed.get("debate_executed", False))
        self._turn_token_budget_gate_fired = bool(
            seed.get("turn_token_budget_gate_fired", False)
        )
        fps = seed.get("validation_stopped_fps")
        if isinstance(fps, (list, tuple, set, frozenset)):
            self._validation_stopped_fps = {str(x) for x in fps if str(x).strip()}
        self._validation_thrash_latched = bool(
            seed.get("validation_thrash_latched", False)
        )

    def post_delegate_check(self, tool_names: set[str]) -> str | None:
        """Check if CEO is doing investigation work after delegating.

        Returns a reminder message if needed, None otherwise.
        """
        if not self._post_delegate:
            return None
        investigation_used = tool_names & self._investigation_tools
        if not investigation_used:
            return None
        self._post_delegate_investigation_count += 1
        if self._post_delegate_investigation_count == 1:
            return (
                "[系统提示] 你已将此工作委派给团队。请直接基于团队的产出写综述，"
                "不要重复调查。如需验证某个具体细节可读 worker 产出的文件，"
                "但不要展开新的调研。"
            )
        if self._post_delegate_investigation_count == 2:
            return (
                "[系统提示] 你仍在做已委派给团队的调查工作。请立即停止调研，"
                "基于团队已有产出写综述收尾。"
            )
        return None  # 第三次由 convergence_action 处理

    def _is_product_landing_success(self, attempt: ToolAttempt) -> bool:
        """Successful landing that counts as product under the files zero-write gate."""
        if not attempt.success or attempt.tool_name not in LANDING_TOOLS:
            return False
        path = (attempt.meta or {}).get("path")
        if path is None or (isinstance(path, str) and not path.strip()):
            return True
        from agentcore.runtime.runs.landing_product import is_product_landing_path

        return is_product_landing_path(str(path), self._product_landing_artifacts)

    def record(self, attempts: list[ToolAttempt]) -> None:
        """Append one round's tool attempts (in call order) to the window.

        Also bumps the run-scoped per-tool cumulative failure tally that drives the
        circuit breaker — independent of the sliding window (which the nudge reset
        clears), since "this tool keeps failing" is a whole-run signal.
        """
        round_investigated = False
        round_investigation_success = False
        round_progress = any(
            attempt.success and attempt.tool_name in PROGRESS_TOOLS for attempt in attempts
        )
        # Soft delivery_idle: any successful landing-tool write latches success /
        # clears the idle clock (dossier notes under research/reviews/debate count
        # as product). Missing meta.path stays compatible (counts as product).
        # Failed landing intent still resets the idle clock.
        delivery_idle_tracking = (
            self._delivery_idle_nudge_rounds > 0 or self._delivery_idle_narrow_rounds > 0
        )
        files_product_gate = delivery_idle_tracking and not self._form_prose
        if files_product_gate:
            landing_success = any(
                self._is_product_landing_success(a) for a in attempts
            )
            landing_attempt = any(
                a.tool_name in LANDING_TOOLS
                and (
                    not a.success
                    or self._is_product_landing_success(a)
                    or not (a.meta or {}).get("path")
                )
                for a in attempts
            )
        else:
            landing_success = any(
                attempt.success and attempt.tool_name in LANDING_TOOLS
                for attempt in attempts
            )
            landing_attempt = any(
                attempt.tool_name in LANDING_TOOLS for attempt in attempts
            )
        if landing_success:
            self._landing_succeeded = True
            self._idle_investigation_rounds = 0
            self._delivery_idle_nudged = False
            # Keep narrow latch: tools stay narrowed once applied this run.
        if round_progress:
            self._same_target_investigation_streak = 0
            self._prev_investigation_fps = frozenset()

        from agentcore.runtime.tool_failures import cap_error_summary

        inv_fps: set[str] = set()
        for attempt in attempts:
            self._recent.append(attempt)
            error_class = resolve_error_class(attempt)
            meta = attempt.meta or {}
            # ``policy_failure`` (upstream block / permission) and ``contract_failure``
            # (self-correctable 参数/路径拒绝，含 path-not-found) are honest failures for
            # the model but must not feed the run-scoped circuit breaker: they still ride
            # the sliding window above (REPEATED_FAILURE / round recording) and count toward
            # per-round unproductive detection, only the cumulative warn/disable tally
            # skips them. Path thrash stays constrained by validation fingerprint streak /
            # same-path file_read cheap-hit — not by disabling the tool.
            # Permanent failures skip the incremental tally too — retire below leaps
            # straight to disable on first hit (no warn=2 / disable=3 window).
            counts_toward_breaker = (
                not attempt.success
                and not attempt.policy_failure
                and not attempt.contract_failure
                and error_class != ERROR_CLASS_PERMANENT
                and error_class != ERROR_CLASS_PERMISSION
            )
            if counts_toward_breaker:
                name = attempt.tool_name
                self._tool_failures[name] += 1
                if attempt.parse_failure:
                    self._tool_parse_failures[name] += 1
                summary = (attempt.error_summary or "").strip()
                if summary:
                    self._tool_last_error[name] = cap_error_summary(summary)
                # A later failure re-opens the gap until a subsequent success.
                self._tool_succeeded_after_fail[name] = False
                self._tool_liveness_last[name] = bool(meta.get("liveness_timeout"))
            elif (
                not attempt.success
                and error_class == ERROR_CLASS_PERMANENT
                and attempt.tool_name
            ):
                # Still stamp last-error / liveness for finalize + steer typing.
                summary = (attempt.error_summary or "").strip()
                if summary and attempt.tool_name not in self._tool_last_error:
                    self._tool_last_error[attempt.tool_name] = cap_error_summary(summary)
                self._tool_succeeded_after_fail[attempt.tool_name] = False
                self._tool_liveness_last[attempt.tool_name] = bool(
                    meta.get("liveness_timeout")
                )
            # Explicit hard-stop retire (browser egress / workspace channel dead /
            # permanent class / access-permission) must apply even when
            # ``contract_failure`` — otherwise tip thrashing never disables the tool.
            # Same-path file_read ceiling is path-scoped only (no retire_tools).
            if not attempt.success:
                retire_list: list[str] = []
                retire = meta.get("retire_tools")
                if isinstance(retire, (list, tuple, set, frozenset)) and retire:
                    retire_list = [str(s).strip() for s in retire if str(s).strip()]
                elif error_class == ERROR_CLASS_PERMANENT and attempt.tool_name:
                    # First permanent failure (liveness / stamped permanent without
                    # an explicit family) retires the tool itself.
                    retire_list = [attempt.tool_name]
                elif (
                    error_class == ERROR_CLASS_PERMISSION
                    and meta.get("permission_kind") == "access"
                    and attempt.tool_name
                ):
                    # Access permission (e.g. grep 无权限): retire so re-call denies.
                    # Allowlist denials stay policy-only (already denied by allowlist).
                    retire_list = [attempt.tool_name]
                if meta.get("workspace_channel_dead") or (
                    meta.get("liveness_timeout")
                    and meta.get("timeout_layer") == "channel"
                ):
                    was_dead = self._workspace_channel_dead
                    self._workspace_channel_dead = True
                    if not was_dead:
                        # A2: force a short user-visible honest sentence (not only
                        # tool error / soft steer). Best-effort; never raises.
                        from agentcore.runtime.coordination.channel_dead_notice import (
                            mark_and_emit_channel_dead_user_notice,
                        )

                        eid = meta.get("execution_id")
                        mark_and_emit_channel_dead_user_notice(
                            execution_id=str(eid).strip() if eid else None
                        )
                if retire_list:
                    summary = (attempt.error_summary or "").strip()
                    for sname in retire_list:
                        self._tool_failures[sname] = max(
                            int(self._tool_failures.get(sname, 0)),
                            self._tool_failure_disable,
                        )
                        if summary and sname not in self._tool_last_error:
                            self._tool_last_error[sname] = cap_error_summary(summary)
                        self._tool_succeeded_after_fail[sname] = False
                        if meta.get("liveness_timeout"):
                            self._tool_liveness_last[sname] = True
                    retire_msg = meta.get("retire_message")
                    if isinstance(retire_msg, str) and retire_msg.strip():
                        self._pending_retire_message = retire_msg.strip()
                    elif error_class == ERROR_CLASS_PERMANENT and not self._pending_retire_message:
                        names = "、".join(f"`{n}`" for n in retire_list)
                        self._pending_retire_message = (
                            f"工具 {names} {_PERMANENT_RETIRE_STEER}"
                        )
                    if any(n in EXEC_ENV_TIMEOUT_FAMILY for n in retire_list):
                        from agentcore.runtime.coordination.exec_env_dead_notice import (
                            mark_and_emit_exec_env_dead_user_notice,
                        )

                        eid = meta.get("execution_id")
                        reason = meta.get("code")
                        mark_and_emit_exec_env_dead_user_notice(
                            execution_id=str(eid).strip() if eid else None,
                            reason_code=str(reason).strip() if reason else None,
                        )
            # Exec-env idle hangs / probe fails: retire code_execute+test_run
            # after N consecutive hits (disaster wall is not this path).
            if is_exec_env_timeout(attempt):
                self._exec_env_timeout_hits += 1
                summary = (attempt.error_summary or "").strip()
                name = attempt.tool_name
                if summary:
                    self._tool_last_error[name] = cap_error_summary(summary)
                self._tool_succeeded_after_fail[name] = False
                if (
                    self._exec_env_timeout_hits >= DEFAULT_EXEC_ENV_TIMEOUT_RETIRE
                    and not EXEC_ENV_TIMEOUT_FAMILY.issubset(self._tool_disabled)
                ):
                    for sname in EXEC_ENV_TIMEOUT_FAMILY:
                        self._tool_failures[sname] = max(
                            int(self._tool_failures.get(sname, 0)),
                            self._tool_failure_disable,
                        )
                        self._tool_succeeded_after_fail[sname] = False
                    self._pending_retire_message = EXEC_ENV_TIMEOUT_RETIRE_STEER
                    from agentcore.runtime.coordination.exec_env_dead_notice import (
                        mark_and_emit_exec_env_dead_user_notice,
                    )

                    eid = (attempt.meta or {}).get("execution_id")
                    reason = (attempt.meta or {}).get("code")
                    mark_and_emit_exec_env_dead_user_notice(
                        execution_id=str(eid).strip() if eid else None,
                        reason_code=str(reason).strip() if reason else None,
                    )
            elif attempt.success and attempt.tool_name in EXEC_ENV_TIMEOUT_FAMILY:
                self._exec_env_timeout_hits = 0
            if attempt.success and self._tool_failures.get(attempt.tool_name, 0) > 0:
                self._tool_succeeded_after_fail[attempt.tool_name] = True
            # Validation same-fingerprint streak → path stop (tool stays available).
            # Already-stopped fp re-hit → thrash latch + mid-loop hard stop (no re-steer).
            if not attempt.success and error_class == ERROR_CLASS_VALIDATION:
                fp = attempt.fingerprint
                tool = attempt.tool_name
                prev = self._validation_fp_streak
                streak = prev[2] + 1 if prev is not None and prev[0] == fp else 1
                self._validation_fp_streak = (fp, tool, streak)
                if fp in self._validation_stopped_fps:
                    self._validation_thrash_latched = True
                    self._pending_validation_hard_stop = True
                else:
                    from agentcore.runtime.engine.write_args_clear import (
                        is_landed_echo_rejection,
                    )

                    landed_echo = tool in LANDING_TOOLS and is_landed_echo_rejection(
                        attempt.error_summary
                    )
                    # 摘要回灌：首次拒写即 path-stop（点名 file_read），少烧一轮空转；
                    # 其它 validation 仍按 validation_path_streak（默认 2）。
                    need = 1 if landed_echo else self._validation_path_streak
                    if streak >= need:
                        self._validation_stopped_fps.add(fp)
                        steer = (
                            _LANDED_SUMMARY_ECHO_STOP_STEER
                            if landed_echo
                            else _VALIDATION_PATH_STOP_STEER
                        )
                        path_meta = attempt.meta.get("path") if attempt.meta else None
                        path_s = (
                            path_meta.strip().replace("\\", "/")
                            if isinstance(path_meta, str) and path_meta.strip()
                            else ""
                        )
                        if landed_echo and path_s:
                            self._pending_validation_stop = (
                                f"工具 `{tool}` path=`{path_s}` {steer}"
                            )
                        else:
                            self._pending_validation_stop = f"工具 `{tool}` {steer}"
            elif attempt.success or error_class != ERROR_CLASS_VALIDATION:
                # Break validation streak on success or a different error class.
                if self._validation_fp_streak is not None and (
                    attempt.success
                    or attempt.fingerprint != self._validation_fp_streak[0]
                ):
                    self._validation_fp_streak = None
            # Same-path classified write rejects → early force_segmented (合流熔断出口).
            # contract_failure skips the cumulative tally above; this streak is the
            # dedicated early path for prose-append / code-integrity hard rejects.
            self._note_path_write_reject(attempt)
            # Over-investigation bookkeeping (收敛治理): tally read-only investigation
            # breadth. Calls count every attempt (incl. failures) for diagnostics;
            # rounds only advance when ≥1 investigation call succeeded — an all-fail
            # round (e.g. hallucinated paths) gathered no intel and must not spend budget.
            if attempt.tool_name in self._investigation_tools:
                self._investigation_calls += 1
                round_investigated = True
                if attempt.success:
                    round_investigation_success = True
                inv_fps.add(attempt.fingerprint)
                if attempt.tool_name in {"file_list", "file_read", "grep"}:
                    self._local_recon_calls += 1
        # Rounds, not raw calls, drive the safety net: a parallel batch of N reads in one
        # round bumps this once, so fanning out can't guillotine the worker.
        if round_investigated:
            if round_investigation_success:
                self._investigation_rounds += 1
            if not round_progress:
                current = frozenset(inv_fps)
                if (
                    current
                    and self._prev_investigation_fps
                    and current <= self._prev_investigation_fps
                ):
                    self._same_target_investigation_streak += 1
                else:
                    self._same_target_investigation_streak = 0
                self._prev_investigation_fps = current

        # Soft delivery_idle: investigation-only round with no landing attempt
        # bumps the streak; landing intent/success resets.
        # Non-investigation rounds (ask / progress / exec) clear the idle clock.
        # (Historical: dossier notes once counted as non-product idle; they now latch
        # as product via landing_product — dossier_note_only stays unreachable.)
        idle_tracking = delivery_idle_tracking and not self._landing_succeeded
        if idle_tracking:
            tool_names = {a.tool_name for a in attempts if a.tool_name}
            investigation_only = bool(tool_names) and tool_names <= self._investigation_tools
            dossier_note_only = False
            if files_product_gate and tool_names and not investigation_only:
                dossier_note_only = all(
                    a.tool_name in self._investigation_tools
                    or (
                        a.tool_name in LANDING_TOOLS
                        and a.success
                        and (a.meta or {}).get("path")
                        and not self._is_product_landing_success(a)
                    )
                    for a in attempts
                    if a.tool_name
                )
            if landing_attempt or landing_success:
                self._idle_investigation_rounds = 0
                if not self._delivery_idle_narrowed:
                    self._delivery_idle_nudged = False
            elif investigation_only or dossier_note_only:
                self._idle_investigation_rounds += 1
            elif tool_names:
                # Mixed / non-investigation activity — not pure read-idle.
                self._idle_investigation_rounds = 0
                if not self._delivery_idle_narrowed:
                    self._delivery_idle_nudged = False
