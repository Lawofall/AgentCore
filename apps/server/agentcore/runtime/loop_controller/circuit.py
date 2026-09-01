"""Tool-failure circuit breaker (run-scoped warn / disable / force_segmented).

Split from ``loop_controller`` — pure move. Consumed only as a mixin by
:class:`~agentcore.runtime.loop_controller.LoopController`.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .types import (
    CIRCUIT_TALLY_KEEP_AVAILABLE,
    LANDING_TOOLS,
    MEMORY_TOOLS,
    ORCHESTRATION_TOOLS,
    PATH_SEGMENT_FORCE_TOOLS,
    CircuitBreak,
)


class ToolCircuitBreakerMixin:
    """Cumulative per-tool failure tally + one-shot warn/disable transitions."""

    # Declared on LoopController.__init__; listed for type-checkers.
    _tool_failures: Counter[str]
    _tool_parse_failures: Counter[str]
    _tool_last_error: dict[str, str]
    _tool_succeeded_after_fail: dict[str, bool]
    _tool_liveness_last: dict[str, bool]
    _workspace_channel_dead: bool
    _tool_warned: set[str]
    _tool_force_retire: set[str]

    @property
    def workspace_channel_dead(self) -> bool:
        """Presence latch: local desk fulfiller gone this run (landing retired)."""
        return self._workspace_channel_dead
    _tool_disabled: set[str]
    _tool_segmented_forced: set[str]
    _tool_parse_kept: set[str]
    _tool_failure_warn: int
    _tool_failure_disable: int
    _pending_path_force_segmented: bool
    _pending_retire_message: str | None
    _pending_validation_stop: str | None
    _pending_validation_hard_stop: bool
    _validation_thrash_latched: bool

    def tool_circuit_breaker(self) -> CircuitBreak:
        """Tools whose cumulative failures crossed a threshold (call after ``record``).

        Returns the tools that *newly* hit the warn / disable threshold this round
        (each transition fires once per tool per run). The engine injects the
        :meth:`CircuitBreak.message` and removes any ``disabled`` tools from the
        toolset for the remaining rounds. A tool that leaps straight to the disable
        count is only disabled (no redundant warn).

        Landing / write tools (``LANDING_TOOLS``) are never circuit-disabled **except**
        when the local desk fulfiller is gone (``_workspace_channel_dead``):
        then pens are disabled with the rest of the workspace IO family. Otherwise
        hitting the disable threshold yields ``force_segmented`` instead (keep the
        pen, force skeleton + section writes). Orchestration tools (``ORCHESTRATION_TOOLS``)
        and memory tools (``MEMORY_TOOLS``) are never disabled on **parse-only**
        failures either (keep the dispatcher / remember; typed JSON-format steer).
        ``CIRCUIT_TALLY_KEEP_AVAILABLE`` (``run`` / 打开网页族) 不因累计失败
        警告或卸工具。``run`` 族亦不因探测失败 / 干等 / 环境死卸工具；网页等
        显式 ``_tool_force_retire``（``retire_tools``）仍卸。

        Same-path consecutive classified write rejects (prose-append lock only)
        also enter ``force_segmented`` via the same latch — early strategy
        upgrade, not a second breaker.
        """
        newly_warned: list[str] = []
        newly_disabled: list[str] = []
        newly_force_segmented: list[str] = []
        for name, count in self._tool_failures.items():
            if (
                name in self._tool_disabled
                or name in self._tool_segmented_forced
                or name in self._tool_parse_kept
            ):
                continue
            if (
                name in CIRCUIT_TALLY_KEEP_AVAILABLE
                and name not in self._tool_force_retire
            ):
                continue
            if count >= self._tool_failure_disable:
                parse_only_tool = (
                    self._tool_failures[name] > 0
                    and self._tool_parse_failures.get(name, 0) == self._tool_failures[name]
                )
                if name in LANDING_TOOLS and not self._workspace_channel_dead:
                    self._tool_segmented_forced.add(name)
                    self._tool_warned.discard(name)
                    newly_force_segmented.append(name)
                    continue
                if name in LANDING_TOOLS and self._workspace_channel_dead:
                    # Channel dead: writing cannot succeed — disable pens with family.
                    self._tool_disabled.add(name)
                    self._tool_warned.discard(name)
                    newly_disabled.append(name)
                    continue
                if name in ORCHESTRATION_TOOLS and parse_only_tool:
                    # Keep delegate/ask_user available; one-shot format steer via warn path.
                    self._tool_parse_kept.add(name)
                    if name not in self._tool_warned:
                        self._tool_warned.add(name)
                        newly_warned.append(name)
                    continue
                if name in MEMORY_TOOLS and parse_only_tool:
                    # Keep remember available; memory-facing format steer via warn path.
                    self._tool_parse_kept.add(name)
                    if name not in self._tool_warned:
                        self._tool_warned.add(name)
                        newly_warned.append(name)
                    continue
                self._tool_disabled.add(name)
                self._tool_warned.discard(name)
                newly_disabled.append(name)
            elif count >= self._tool_failure_warn and name not in self._tool_warned:
                self._tool_warned.add(name)
                newly_warned.append(name)
        if self._pending_path_force_segmented:
            self._pending_path_force_segmented = False
            for name in sorted(PATH_SEGMENT_FORCE_TOOLS):
                if name in self._tool_disabled or name in self._tool_segmented_forced:
                    continue
                self._tool_segmented_forced.add(name)
                self._tool_warned.discard(name)
                newly_force_segmented.append(name)
        tripped = (*newly_warned, *newly_disabled, *newly_force_segmented)
        parse_only = frozenset(
            name
            for name in tripped
            if self._tool_failures.get(name, 0) > 0
            and self._tool_parse_failures.get(name, 0) == self._tool_failures[name]
        )
        retire_message = None
        if newly_disabled and self._pending_retire_message:
            retire_message = self._pending_retire_message
            self._pending_retire_message = None
        elif newly_force_segmented and self._pending_retire_message:
            # Landing tools convert permanent retire → force_segmented; drop the
            # pending hard-stop copy so it cannot leak onto a later unrelated disable.
            self._pending_retire_message = None
        validation_stop = None
        if self._pending_validation_stop:
            validation_stop = self._pending_validation_stop
            self._pending_validation_stop = None
        return CircuitBreak(
            warned=tuple(newly_warned),
            disabled=tuple(newly_disabled),
            parse_only=parse_only,
            force_segmented=frozenset(newly_force_segmented),
            retire_message=retire_message,
            liveness_warned=frozenset(
                n for n in newly_warned if self._tool_liveness_last.get(n)
            ),
            validation_stop=validation_stop,
        )

    def tool_failure_count(self, tool_name: str) -> int:
        """Cumulative failure count for one tool in this run (circuit breaker input)."""
        return int(self._tool_failures.get(tool_name, 0))

    def tool_failure_facts(self) -> list[Any]:
        """Per-tool failure facts for tools that failed at least once this run.

        Returns :class:`~agentcore.runtime.tool_failures.ToolFailureFact` instances
        (typed as Any here to keep this module import-light at class body time).
        """
        from agentcore.runtime.tool_failures import ToolFailureFact

        facts: list[ToolFailureFact] = []
        for name, count in sorted(self._tool_failures.items()):
            if count <= 0:
                continue
            facts.append(
                ToolFailureFact(
                    tool_name=name,
                    failure_count=int(count),
                    last_error=self._tool_last_error.get(name, ""),
                    succeeded_after=bool(self._tool_succeeded_after_fail.get(name, False)),
                )
            )
        return facts

    def outstanding_tool_failures(self) -> list[Any]:
        """Failures not cancelled by a later success of the same tool."""
        return [f for f in self.tool_failure_facts() if f.outstanding]

    def take_validation_hard_stop(self) -> bool:
        """Consume a one-shot mid-loop hard stop after a stopped validation fp re-hit.

        Distinct from the first-trip ``validation_stop`` steer: re-hitting an already
        path-stopped fingerprint latches thrashing and requests Finalize this round
        so the run does not burn out ``max_rounds``. Ceiling routing still uses
        :meth:`is_thrashing` (sticky latch; not consumed here).
        """
        if not self._pending_validation_hard_stop:
            return False
        self._pending_validation_hard_stop = False
        return True

    @property
    def validation_thrash_latched(self) -> bool:
        """True after a stopped validation fingerprint was re-hit (sticky)."""
        return self._validation_thrash_latched
