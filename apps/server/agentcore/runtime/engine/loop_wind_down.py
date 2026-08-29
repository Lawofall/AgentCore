"""Worker wind-down / delivery-idle / timeout-grace tool-surface narrowing.

Split from ``loop.py`` — pure move. The ReAct round sequencer stays on the loop;
this object owns the worker cutoff surface (enter, arm, breach, delivery-idle
narrow) and the allowlist the loop reads via :meth:`effective_allowed`.
"""

from __future__ import annotations

from collections.abc import Callable
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
    """Worker-only cutoff surface: token/timeout/retrieval wind-down + delivery-idle.

    Constructed once per ``react_loop`` after the :class:`LoopController` exists.
    ``refresh_tool_defs`` is rebound by the loop so allowlist changes re-project
    the OpenAI tool schema the model sees.
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
        refresh_tool_defs: Callable[[], None],
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
        self.refresh_tool_defs = refresh_tool_defs
        # B·收尾窗口：预算软顶 / 超时预警后收窄到落盘+诊断+handoff（不改硬顶语义）。
        self.wind_down_active = False
        self.wind_down_reason = ""
        self.wind_down_effective_allowed: list[str] | None = None
        self.wind_down_whitelist: frozenset[str] | None = None
        self.wind_down_breach_count = 0
        self.wind_down_breach_pending_nudge = False
        self.wind_down_breach_nudge_text = ""
        # delivery_idle 工具收窄（factory 对交文件已关；显式构造仍可能走此路径）。
        # 与 token/timeout wind_down 解耦。
        self.delivery_idle_narrow_active = False

    def effective_allowed(self) -> list[str] | None:
        if self.wind_down_effective_allowed is not None:
            return self.wind_down_effective_allowed
        return self.live_allowed

    def enter_wind_down(self, reason: str, instruction: str | None = None, *, tokens: int) -> None:
        if self.wind_down_active or self.role != "worker":
            return
        from agentcore.runtime.runs.cutoff import (
            narrow_tools_for_wind_down,
            wind_down_allowed_tools,
            wind_down_instruction_timeout,
            wind_down_instruction_token,
            worker_keeps_file_read_in_wind_down,
        )

        self.wind_down_active = True
        self.wind_down_reason = reason
        available = set(self.tools.names)
        keep_file_read = worker_keeps_file_read_in_wind_down(
            available=available, allowed=self.live_allowed
        )
        self.wind_down_whitelist = wind_down_allowed_tools(keep_file_read=keep_file_read)
        narrowed = narrow_tools_for_wind_down(
            available,
            allowed=self.live_allowed,
            keep_file_read=keep_file_read,
        )
        self.wind_down_effective_allowed = narrowed
        self.refresh_tool_defs()
        if instruction is None:
            if reason == "token_budget":
                instruction = wind_down_instruction_token()
            elif reason == "worker_timeout":
                instruction = wind_down_instruction_timeout()
            else:
                # retrieval_budget / other: keep caller-supplied or build a short default.
                instruction = (
                    "[系统提示] 检索预算已用尽。本轮起进入收尾窗口：仅允许落盘"
                    "与 handoff，请基于已有证据交卷；禁止继续 web_search / read_url。"
                )
        self.messages.append(LLMMessage(role="user", content=instruction))
        from agentcore.runtime.tool_failures import sync_tool_failure_constraint_in_system

        sync_tool_failure_constraint_in_system(
            self.messages, self.controller.outstanding_tool_failures()
        )
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
            allowed_tools=narrowed,
            keep_file_read=keep_file_read,
        )
        from agentcore.runtime.runs.run_phase_emit import emit_run_phase

        emit_run_phase(self.sink, self.run_id, self.agent_id, "winding_down")

    def apply_delivery_idle_narrow(self) -> None:
        """Narrow to write/诊断/handoff/必要读 after delivery-idle ladder.

        Factory never arms files-expected delivery_idle; this path remains for
        explicit LoopController construction (recon does not set narrow).
        May reuse :func:`narrow_tools_for_wind_down`. Does **not** emit
        ``engine.wind_down_enter`` / winding_down phase (budget wind_down stays
        independent). If budget wind_down already active, surface is already
        narrowed — no-op on allowlist. Collaboration keeps note tools.
        """
        if self.delivery_idle_narrow_active or self.role != "worker":
            return
        # Defense: report posts must never strip search even if a pending latch leaked.
        if self.controller is not None and self.controller.delivery_idle_report:
            return
        self.delivery_idle_narrow_active = True
        if self.wind_down_active:
            return
        from agentcore.runtime.runs.cutoff import (
            narrow_tools_for_wind_down,
            worker_keeps_file_read_in_wind_down,
        )

        available = set(self.tools.names)
        keep_file_read = worker_keeps_file_read_in_wind_down(
            available=available, allowed=self.live_allowed
        )
        narrowed = narrow_tools_for_wind_down(
            available,
            allowed=self.live_allowed,
            keep_file_read=keep_file_read,
        )
        self.live_allowed = narrowed
        self.refresh_tool_defs()
        logger.info(
            "engine.delivery_idle_narrow_apply",
            run_id=self.run_id,
            role=self.role,
            allowed_tools=narrowed,
            keep_file_read=keep_file_read,
        )

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
        """Wind-down breach: non-whitelist tool → nudge+handoff-only, or local synth."""
        skip_tool_exec = False
        self.wind_down_breach_pending_nudge = False
        self.wind_down_breach_nudge_text = ""
        directive: LoopDirective | None = None
        if self.wind_down_active and self.role == "worker":
            from agentcore.runtime.engine.directive import Continue, Return
            from agentcore.runtime.runs.cutoff import (
                WIND_DOWN_ALLOWED_TOOLS,
                narrow_tools_for_wind_down_breach,
                should_force_local_after_wind_down_breach,
                wind_down_breach_nudge,
                wind_down_breach_tool_names,
                worker_keeps_file_read_in_wind_down,
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
                keep_file_read = keep_landing and worker_keeps_file_read_in_wind_down(
                    available=set(self.tools.names),
                    allowed=list(effective_whitelist),
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

                def _journal_wind_down_deny(
                    tc: Any,
                    name: str,
                    *,
                    _keep_landing: bool = keep_landing,
                ) -> None:
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
                    deny = f"工具 '{name}' 不在收尾窗口白名单，未执行。" + (
                        "请落盘后调用 handoff 交卷。"
                        if _keep_landing
                        else "请立即调用 handoff 交卷。"
                    )
                    self.sink.emit(tool_use_start(tc.id, name, args, run_id=self.run_id or ""))
                    # 收尾窗口 / 白名单 / 落盘 / handoff are all engine words
                    # aimed at the model — ``deny`` stays on ``result`` and
                    # the user face is curated by code only.
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
                    self.wind_down_effective_allowed = narrow_tools_for_wind_down_breach(
                        set(self.tools.names),
                        keep_landing=keep_landing,
                        keep_file_read=keep_file_read,
                        allowed=list(effective_whitelist),
                    )
                    breach_nudge = wind_down_breach_nudge(keep_landing=keep_landing)
                    self.refresh_tool_defs()
                    if not kept:
                        self.messages.append(
                            LLMMessage(
                                role="assistant",
                                content=outcome.content or None,
                                tool_calls=outcome.tool_calls or None,
                                reasoning_content=outcome.reasoning or None,
                            )
                        )
                        for tc in outcome.tool_calls or []:
                            name = tc.function.name or ""
                            deny = f"工具 '{name}' 不在收尾窗口白名单，未执行。" + (
                                "请落盘后调用 handoff 交卷。"
                                if keep_landing
                                else "请立即调用 handoff 交卷。"
                            )
                            self.messages.append(
                                LLMMessage(
                                    role="tool",
                                    content=deny,
                                    tool_call_id=tc.id,
                                )
                            )
                        self.messages.append(LLMMessage(role="user", content=breach_nudge))
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
                        # Nudge after tools if the round continues (below).
                        skip_tool_exec = False
                        # Mark so post-tool path can inject nudge once.
                        self.wind_down_breach_pending_nudge = True
                        # Stash nudge text for the post-tool inject path.
                        self.wind_down_breach_nudge_text = breach_nudge
        return WindDownBreachResult(
            skip_tool_exec=skip_tool_exec,
            outcome=outcome,
            directive=directive,
        )

    def inject_pending_breach_nudge(self, directive: LoopDirective) -> None:
        if not self.wind_down_breach_pending_nudge:
            return
        from agentcore.runtime.engine.directive import Continue
        from agentcore.runtime.runs.cutoff import WIND_DOWN_BREACH_NUDGE

        if isinstance(directive, Continue):
            self.messages.append(
                LLMMessage(
                    role="user",
                    content=(self.wind_down_breach_nudge_text or WIND_DOWN_BREACH_NUDGE),
                )
            )
        self.wind_down_breach_pending_nudge = False
        self.wind_down_breach_nudge_text = ""
