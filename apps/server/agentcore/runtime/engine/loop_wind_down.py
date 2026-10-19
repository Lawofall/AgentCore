"""Worker wind-down / timeout-grace execute-layer deny.

Split from ``loop.py`` — pure move. The ReAct round sequencer stays on the loop;
this object owns the worker cutoff surface (enter, arm, breach). Opening
``tools[]`` stays frozen; wind-down denies at execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.events import EventSink, tool_use_end, tool_use_start
from agentcore.runtime.loop_controller import LoopController
from agentcore.tools.registry import ToolRegistry

from .directive import LoopDirective
from .outcome import RoundOutcome
from .tool_failure_face import tool_failure_fields

logger = get_logger(__name__)


@dataclass
class WindDownBreachResult:
    """Outcome of one tool-round wind-down breach check."""

    skip_tool_exec: bool
    outcome: RoundOutcome
    directive: LoopDirective | None


class LoopWindDown:
    """Worker-only cutoff surface: token/timeout/retrieval wind-down.

    Constructed once per ``react_loop`` after the :class:`LoopController` exists.
    Opening ``tools[]`` stays frozen; ``wind_down_whitelist`` is execute-deny.
    """

    def __init__(
        self,
        *,
        role: str,
        run_id: str,
        agent_id: str,
        tools: ToolRegistry,
        sink: EventSink,
        messages: list[LLMMessage],
        token_budget: int,
        files_expected: bool,
        live_allowed: list[str] | None,
        controller: LoopController,
    ) -> None:
        self.role = role
        self.run_id = run_id
        self.agent_id = agent_id
        self.tools = tools
        self.sink = sink
        self.messages = messages
        self.token_budget = token_budget
        self.files_expected = files_expected
        self.live_allowed = live_allowed
        self.controller = controller
        # B·收尾窗口：预算软顶 / 超时预警后执行层收窄到落盘+诊断+handoff（不改硬顶语义、不改表）。
        self.wind_down_active = False
        self.wind_down_reason = ""
        self.wind_down_whitelist: frozenset[str] | None = None
        self.wind_down_breach_count = 0

    def effective_allowed(self) -> list[str] | None:
        return self.live_allowed

    def enter_wind_down(self, reason: str, instruction: str | None = None, *, tokens: int) -> None:
        if self.wind_down_active or self.role != "worker":
            return
        from agentcore.runtime.runs.cutoff import (
            wind_down_allowed_tools,
            worker_keeps_file_read_in_wind_down,
        )

        del instruction  # Tool table is the contract; no [系统提示].
        self.wind_down_active = True
        self.wind_down_reason = reason
        available = set(self.tools.names)
        keep_file_read = worker_keeps_file_read_in_wind_down(
            available=available, allowed=self.live_allowed
        )
        self.wind_down_whitelist = wind_down_allowed_tools(keep_file_read=keep_file_read)
        from agentcore.config import settings as _settings

        logger.info(
            "engine.wind_down_enter",
            reason=reason,
            run_id=self.run_id,
            role=self.role,
            tokens=tokens,
            token_budget=self.token_budget,
            wind_down_reserve=(
                int(_settings.engine_worker_token_wind_down_reserve or 0)
                if reason == "token_budget"
                else None
            ),
            allowed_tools=sorted(self.wind_down_whitelist),
            keep_file_read=keep_file_read,
        )
        from agentcore.runtime.runs.run_phase_emit import emit_run_phase

        emit_run_phase(self.sink, self.run_id, self.agent_id, "winding_down")

    def consume_timeout_wind_down_pending(self) -> bool:
        """Consume timeout wind-down from hard-timeout guard and/or coordination session.

        Independent of token wind-down: a timeout pending must not be swallowed
        when the token soft-top already narrowed tools.
        """
        if self.role != "worker" or not self.run_id:
            return False
        from agentcore.runtime.runs.timeout_hard import get_hard_timeout

        guard = get_hard_timeout(self.run_id)
        if guard is not None and guard.consume_wind_down():
            # Keep coordination session mirrors in sync when present.
            from agentcore.runtime.coordination.session import active_coordination

            session = active_coordination()
            if session is not None:
                session._timeout_wind_down_pending.discard(self.run_id)
                session._timeout_wind_down_entered.add(self.run_id)
            return True
        from agentcore.runtime.coordination.session import active_coordination

        session = active_coordination()
        return bool(session is not None and session.consume_timeout_wind_down(self.run_id))

    def maybe_arm_wind_down(self, *, tokens: int) -> None:
        """Budget soft-top or timeout warn → wind-down (handoff/persist).

        Token and timeout reasons are independent: timeout pending is consumed
        even when token wind-down is already active (marks entered for stamp).
        """
        if self.role != "worker":
            return
        from agentcore.config import settings
        from agentcore.runtime.runs.cutoff import should_enter_token_wind_down

        reserve = int(settings.engine_worker_token_wind_down_reserve or 0)
        if not self.wind_down_active and should_enter_token_wind_down(
            tokens, self.token_budget, reserve
        ):
            self.enter_wind_down("token_budget", tokens=tokens)
        timeout_pending = self.consume_timeout_wind_down_pending()
        if timeout_pending and not self.wind_down_active:
            self.enter_wind_down("worker_timeout", tokens=tokens)

    def enforce_hard_timeout_entry(self, *, tokens: int) -> str | None:
        """Round-boundary hard-timeout gate. Returns break reason or None.

        TIMEOUT → grant one grace wind-down round; after grace → force cancel
        (reuse cancel channel). No mid-stream preemption.
        """
        if self.role != "worker" or not self.run_id:
            return None
        from agentcore.runtime.runs.timeout_hard import (
            HardTimeoutPhase,
            get_hard_timeout,
        )

        guard = get_hard_timeout(self.run_id)
        if guard is None:
            return None
        if guard.allows_grace_round():
            guard.begin_grace_round()
            if not self.wind_down_active:
                self.enter_wind_down("worker_timeout", tokens=tokens)
            return None
        if guard.blocks_new_work():
            guard.request_force_cancel(reason="post_grace")
            logger.warning(
                "engine.timeout_force_cancel",
                run_id=self.run_id,
                phase=guard.phase.value,
            )
            return "worker_timeout"
        if guard.phase is HardTimeoutPhase.GRACE and not self.wind_down_active:
            # About to run the granted grace round — ensure tools are narrowed.
            self.enter_wind_down("worker_timeout", tokens=tokens)
        return None

    def apply_tool_breach(self, outcome: RoundOutcome, *, tokens: int) -> WindDownBreachResult:
        """Wind-down breach: non-whitelist tool → narrow surface, or local synth."""
        skip_tool_exec = False
        directive: LoopDirective | None = None
        if self.wind_down_active and self.role == "worker":
            from agentcore.runtime.engine.directive import Continue, Return
            from agentcore.runtime.runs.cutoff import (
                WIND_DOWN_ALLOWED_TOOLS,
                should_force_local_after_wind_down_breach,
                wind_down_breach_tool_names,
                wind_down_deny_output,
            )

            effective_whitelist = self.wind_down_whitelist or WIND_DOWN_ALLOWED_TOOLS
            breached = wind_down_breach_tool_names(
                [(tc.function.name or "") for tc in (outcome.tool_calls or [])],
                allowed=effective_whitelist,
            )
            if breached:
                force_local = should_force_local_after_wind_down_breach(
                    prior_breaches=self.wind_down_breach_count,
                    tokens=tokens,
                    token_budget=self.token_budget,
                    wind_down_reason=self.wind_down_reason,
                )
                # Pending landing obligation → keep write tools; only strip retrieval.
                keep_landing = (
                    self.files_expected
                    and self.controller is not None
                    and not self.controller.landing_succeeded
                )
                logger.warning(
                    "engine.wind_down_breach",
                    run_id=self.run_id,
                    breached_tools=breached,
                    prior_breaches=self.wind_down_breach_count,
                    force_local=force_local,
                    keep_landing=keep_landing,
                    tokens=tokens,
                    token_budget=self.token_budget,
                )
                self.wind_down_breach_count += 1

                def _journal_wind_down_deny(tc: Any, name: str) -> None:
                    """Emit durable tool_use_start/end so wind_down 拒执行
                    is journal-queryable."""
                    import json as _json

                    raw_args = ""
                    try:
                        raw_args = tc.function.arguments or ""
                    except Exception:  # noqa: BLE001
                        raw_args = ""
                    try:
                        args = _json.loads(raw_args) if raw_args else {}
                        if not isinstance(args, dict):
                            args = {}
                    except Exception:  # noqa: BLE001
                        args = {}
                    deny = wind_down_deny_output(name)
                    self.sink.emit(tool_use_start(tc.id, name, args, run_id=self.run_id or ""))
                    # Deny stays on ``result``; the user face is curated by code only.
                    self.sink.emit(
                        tool_use_end(
                            tc.id,
                            name,
                            success=False,
                            output=deny,
                            failure=tool_failure_fields(code="wind_down_deny"),
                            run_id=self.run_id or "",
                        )
                    )

                if force_local:
                    # Still journal denied calls before local-synth close.
                    for tc in outcome.tool_calls or []:
                        name = tc.function.name or ""
                        if name and name not in effective_whitelist:
                            _journal_wind_down_deny(tc, name)
                    directive = Return()
                    outcome = RoundOutcome(
                        content=outcome.content,
                        reasoning=outcome.reasoning,
                        usage=outcome.usage,
                    )
                    skip_tool_exec = True
                else:
                    kept = [
                        tc
                        for tc in (outcome.tool_calls or [])
                        if (tc.function.name or "") in effective_whitelist
                    ]
                    denied = [
                        tc
                        for tc in (outcome.tool_calls or [])
                        if (tc.function.name or "") not in effective_whitelist
                    ]
                    for tc in denied:
                        _journal_wind_down_deny(tc, tc.function.name or "")
                    if not kept:
                        self.messages.append(
                            LLMMessage(
                                role="assistant",
                                content=outcome.content or None,
                                tool_calls=outcome.tool_calls or None,
                                reasoning_content=outcome.reasoning or None,
                                thinking_blocks=outcome.thinking_blocks,
                            )
                        )
                        for tc in outcome.tool_calls or []:
                            name = tc.function.name or ""
                            deny = wind_down_deny_output(name)
                            self.messages.append(
                                LLMMessage(
                                    role="tool",
                                    content=deny,
                                    tool_call_id=tc.id,
                                )
                            )
                        outcome = RoundOutcome(
                            content=outcome.content,
                            reasoning=outcome.reasoning,
                            usage=outcome.usage,
                        )
                        directive = Continue()
                        skip_tool_exec = True
                    else:
                        outcome = RoundOutcome(
                            content=outcome.content,
                            reasoning=outcome.reasoning,
                            usage=outcome.usage,
                            tool_calls=kept,
                        )
                        skip_tool_exec = False
        return WindDownBreachResult(
            skip_tool_exec=skip_tool_exec,
            outcome=outcome,
            directive=directive,
        )
