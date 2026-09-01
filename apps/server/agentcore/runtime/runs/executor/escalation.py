"""Blocking escalate channel for a worker node (design section 4.2)."""

from __future__ import annotations

import asyncio
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.runtime.events import escalation_required, escalation_resolved
from agentcore.runtime.interaction import InteractionKind
from agentcore.runtime.runs.executor.env import AgentExecutorEnv
from agentcore.runtime.runs.executor.identities import ESCALATION_CONCURRENCY_CAP
from agentcore.tools.protocol import EscalationChannel, EscalationOutcome

logger = get_logger(__name__)


def build_escalation_channel(
    env: AgentExecutorEnv,
    run_id: str,
    agent_id: str,
    resolutions: dict[str, dict[str, Any]],
) -> EscalationChannel | None:
    """Wire one worker's ``escalate(blocking=true)`` to suspend for the user (设计 §4.2).

    ``None`` when no interaction bridge is wired (CEO / standalone / tests) — then the
    tool keeps its non-blocking behaviour. The returned channel carries ``armed`` (the
    live-user gate) and a ``request`` that owns the whole mechanism the tool stays clear
    of (引擎纯化): the per-turn concurrency cap, the suspend on the shared bridge, the
    ``escalation_required`` / ``escalation_resolved`` pair (单一发射者: emitted here, the
    awaiter, never the resolve route), and recording the disposition into ``resolutions``
    for the CEO-facing harvest.
    """
    bridge = env.interaction_bridge
    if bridge is None:
        return None

    async def _request(
        question: str,
        assumption: str,
        questions: list[dict[str, Any]],
        kind: str = "normal",
        awaiting: str = "user",
        *,
        browser_login: bool = False,
        ownership_paths: list[str] | None = None,
        lock_owner_run_id: str = "",
    ) -> EscalationOutcome:
        # Cap: count this conversation's already-parked blocking escalates. The check
        # and the suspend's create() run with no await between them (single loop), so
        # the count can't race (设计 §4.7). Over cap ⇒ degrade (proceed on assumption).
        # browser_login / write-lock ownership are always user-facing — never CEO.
        want_browser_login = bool(browser_login)
        own_paths = [
            p for p in (ownership_paths or []) if isinstance(p, str) and p.strip()
        ]
        ownership_conflict = bool(own_paths)
        who = (
            "user"
            if want_browser_login or ownership_conflict
            else (awaiting if awaiting in ("user", "ceo") else "user")
        )
        awaiting_ceo = who == "ceo"

        # D1: after ask_user soft-stop cancelled a parked worker, CEO may have
        # stashed a resolve_escalation answer — pick it up without re-suspending.
        if awaiting_ceo:
            from agentcore.runtime.coordination.session import resolve_coordination_session

            session = resolve_coordination_session(env.base_tool_context.execution_id)
            if session is not None:
                stashed = session.take_stashed_resolution(run_id)
                if stashed is not None:
                    answer = str(stashed.get("answer") or "").strip()
                    via_user = bool(stashed.get("via_user"))
                    esc_id = str(stashed.get("escalation_id") or new_id())
                    resolutions[question] = {"status": "resolved", "answer": answer}
                    env.sink.emit(
                        escalation_resolved(
                            run_id,
                            agent_id,
                            escalation_id=esc_id,
                            status="resolved",
                            answer=answer,
                            arbitrated_by="ceo",
                            via_user=via_user,
                        )
                    )
                    return EscalationOutcome(status="resolved", answer=answer)

        parked = sum(
            1
            for r in bridge.list_pending(env.conversation_id)
            if r.kind is InteractionKind.ESCALATION
        )
        if parked >= ESCALATION_CONCURRENCY_CAP:
            logger.info("worker.escalate.cap_degraded", run_id=run_id, parked=parked)
            return EscalationOutcome(status="degraded")
        escalation_id = new_id()
        esc_kind = kind if kind in ("normal", "scope", "dep") else "normal"

        if awaiting_ceo:
            from agentcore.runtime.coordination.bridge import (
                post_escalation_to_coordination,
            )
            from agentcore.runtime.coordination.session import resolve_coordination_session

            session = resolve_coordination_session(env.base_tool_context.execution_id)
            if session is not None and session.active:
                from agentcore.workspace.write_claims import ownership_escalation_hints

                desk_for_hints: str | None = None
                if session.live_plan is not None:
                    node = session.live_plan.by_id(run_id) if hasattr(session.live_plan, "by_id") else None
                    if node is not None:
                        tf = getattr(node, "target_folder_id", None)
                        desk_for_hints = (
                            str(tf or getattr(env, "session_folder_id", None) or "").strip()
                            or None
                        )
                if desk_for_hints is None:
                    desk_for_hints = (
                        str(getattr(env, "session_folder_id", None) or "").strip() or None
                    )
                hints = ownership_escalation_hints(
                    escalator_run_id=run_id,
                    question=question,
                    execution_id=env.base_tool_context.execution_id,
                    write_ancestors=env.ancestors_by_id.get(run_id, frozenset()),
                    write_coordinator=env.write_coordinator,
                    desk_id=desk_for_hints,
                )
                session.register_arbitration(
                    run_id,
                    escalation_id=escalation_id,
                    conversation_id=env.conversation_id,
                    question=question,
                    assumption=assumption,
                    kind=esc_kind,
                    ownership_paths=hints.get("ownership_paths"),
                    lock_owner_run_id=str(hints.get("lock_owner_run_id") or ""),
                    escalator_is_lock_owner_nested_child=hints.get(
                        "escalator_is_lock_owner_nested_child"
                    ),
                )
                post_escalation_to_coordination(
                    run_id=run_id,
                    role="",
                    kind=esc_kind,
                    question=question,
                    assumption=assumption,
                    blocking=True,
                    source="blocking_arbitrate",
                    execution_id=env.base_tool_context.execution_id,
                    escalation_id=escalation_id,
                    ownership_paths=hints.get("ownership_paths"),
                    lock_owner_run_id=str(hints.get("lock_owner_run_id") or ""),
                    escalator_is_lock_owner_nested_child=hints.get(
                        "escalator_is_lock_owner_nested_child"
                    ),
                    ownership_kind=hints.get("ownership_kind"),
                    owner_status=hints.get("owner_status"),
                )

        via_user = False
        suspend_payload: dict[str, Any] = {
            "escalation_id": escalation_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "question": question,
            "assumption": assumption,
            "questions": questions,
            "kind": esc_kind,
            "awaiting": who,
        }
        if want_browser_login:
            suspend_payload["browser_login"] = True
        if own_paths:
            suspend_payload["ownership_paths"] = own_paths
        lock_owner = (lock_owner_run_id or "").strip()
        if lock_owner:
            suspend_payload["lock_owner_run_id"] = lock_owner
        # Rides the request payload too, so an attach-time rebuild of the still-open card
        # (hot_interaction_reattach) states the same wait policy as the original frame.
        wait_ceiling = env.escalation_timeout
        if isinstance(wait_ceiling, (int, float)) and wait_ceiling > 0:
            suspend_payload["timeout_seconds"] = float(wait_ceiling)
        try:
            result = await bridge.suspend(
                escalation_id,
                env.conversation_id,
                kind=InteractionKind.ESCALATION,
                payload=suspend_payload,
                timeout=env.escalation_timeout,
                on_suspended=lambda: env.sink.emit(
                    escalation_required(
                        run_id,
                        agent_id,
                        escalation_id=escalation_id,
                        question=question,
                        assumption=assumption,
                        questions=questions,
                        kind=esc_kind,
                        awaiting=who,
                        browser_login=want_browser_login or None,
                        ownership_paths=own_paths or None,
                        lock_owner_run_id=lock_owner or None,
                        timeout_seconds=env.escalation_timeout,
                    )
                ),
            )
        except TimeoutError:
            status, answer = "timed_out", ""
        except asyncio.CancelledError:
            # ask_user soft-stop cancels the drive — keep journaled pending_arbitrations
            # so resume + resolve_escalation can stash/settle for a re-armed worker.
            raise
        else:
            # Assumed vs timed_out are distinct wire statuses (same worker fallback).
            if isinstance(result, dict) and result.get("use_assumption"):
                status, answer = "assumed", ""
            elif isinstance(result, dict):
                status, answer = "resolved", str(result.get("answer") or "").strip()
                via_user = bool(result.get("via_user"))
            else:
                status, answer = "resolved", str(result or "").strip()
        if awaiting_ceo:
            from agentcore.runtime.coordination.session import resolve_coordination_session

            session = resolve_coordination_session(env.base_tool_context.execution_id)
            if session is not None:
                session.clear_arbitration(run_id)
        resolutions[question] = {"status": status, "answer": answer}
        logger.info(
            "worker.escalate.settled",
            run_id=run_id,
            escalation_id=escalation_id,
            status=status,
            awaiting=who,
            timeout_s=env.escalation_timeout,
        )
        env.sink.emit(
            escalation_resolved(
                run_id,
                agent_id,
                escalation_id=escalation_id,
                status=status,
                answer=answer,
                arbitrated_by="ceo" if awaiting_ceo else "user",
                via_user=via_user if awaiting_ceo else None,
            )
        )
        return EscalationOutcome(
            status=status,
            answer=answer if status == "resolved" else None,
        )

    return EscalationChannel(armed=env.escalation_armed, request=_request)
