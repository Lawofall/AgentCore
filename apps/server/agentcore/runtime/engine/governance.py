"""ReAct loop convergence governance: investigation classification, circuit breaker, nudges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.types import ToolApproval, ToolFace
from agentcore.llm.provider.protocol import LLMMessage, ToolCall
from agentcore.runtime.events import FinishReason
from agentcore.runtime.loop_controller import (
    Intervention,
    LoopController,
    ToolAttempt,
)
from agentcore.tools.registry import ToolRegistry

from .constants import FINALIZE_COORDINATION_TOOLS, FINALIZE_PERSIST_TOOLS
from .directive import Continue, Finalize, LoopDirective, Return
from .outcome import RoundOutcome

logger = get_logger(__name__)

# Local file peeks (file_list / glob / read / grep) — diagnostics / eval probe only.
LOCAL_RECON_TOOLS = frozenset({"file_list", "glob", "read", "grep"})


def coordination_injection_has_all_completed(messages: list[LLMMessage]) -> bool:
    """True when a coordination inject batch includes the all_completed event."""
    return any(
        m.role == "user" and m.content and "all_completed" in m.content for m in messages
    )


def should_turn_token_budget_gate(controller: LoopController, *, role: str) -> bool:
    """Whether the turn-token wrap-up steer should fire (ceiling hit, captain, one-shot)."""
    if role != "captain" or controller.turn_token_budget_gate_fired:
        return False
    from agentcore.runtime.turn.token_budget import is_turn_token_ceiling_hit

    return is_turn_token_ceiling_hit()


def maybe_inject_turn_token_budget_gate(
    controller: LoopController,
    *,
    messages: list[LLMMessage],
    run_id: str,
    round_idx: int,
    role: str,
) -> bool:
    """Retired: execute-layer reject copy is enough. Never injects."""
    del controller, messages, run_id, round_idx, role
    return False


# Successful returns that enter post-delegate synthesis mode (G5: live/resume symmetric).
_POST_DELEGATE_TOOLS = frozenset({"delegate", "debate"})


def note_delegate_batches(
    controller: LoopController,
    tool_calls: list[ToolCall],
    attempts: list[ToolAttempt],
) -> None:
    """Inform the controller of each successful delegate/debate batch's shape (post-return)."""
    for tc, attempt in zip(tool_calls, attempts, strict=False):
        if attempt.tool_name not in _POST_DELEGATE_TOOLS or not attempt.success:
            continue
        if attempt.tool_name == "debate":
            controller.mark_debate_executed()
        nodes = int(attempt.meta.get("batch_nodes") or 0)
        has_deps = bool(attempt.meta.get("batch_has_deps"))
        if nodes == 0:
            args = ""
            if tc is not None and getattr(tc, "function", None) is not None:
                args = tc.function.arguments or ""
            from agentcore.runtime.delegate.batch_shape import (
                batch_shape_from_arguments,
            )

            nodes, has_deps = batch_shape_from_arguments(args)
        controller.mark_post_delegate(
            node_count=nodes,
            has_deps=has_deps,
            audit_hard=bool(attempt.meta.get("audit_hard")),
            includes_review=bool(attempt.meta.get("batch_includes_review")),
        )


def classify_investigation_tools(
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
) -> frozenset[str]:
    """Classify read-only info-gathering tools for over-investigation backstop."""
    available_names = (
        set(allowed_tool_names) if allowed_tool_names is not None else set(tools.names)
    )
    schema_by_name = {schema.name: schema for schema in tools.list_all()}
    investigation_tools: set[str] = set()
    for name in available_names:
        schema = schema_by_name.get(name)
        if schema is None:
            continue
        if schema.approval is ToolApproval.NEVER and schema.face in (
            ToolFace.FILE,
            ToolFace.SEARCH,
            ToolFace.WEB,
            ToolFace.EXECUTION,
        ):
            investigation_tools.add(name)
    return frozenset(investigation_tools)


def create_loop_controller(
    investigation_tools: frozenset[str],
    *,
    seed: Mapping[str, Any] | None = None,
    files_expected: bool = False,
    report_delivery: bool = False,
    short_write_posture: bool = False,
    tighten_verify_exec_thrash: bool = False,
    max_rounds: int | None = None,
    expects_landing: bool = False,
    product_landing_artifacts: list[str] | tuple[str, ...] | None = None,
) -> LoopController:
    """Build per-run convergence controller from engine settings.

    ``seed`` restores cross-suspension latches (gates + validation path-stop /
    thrash; see :meth:`LoopController.apply_seed`); omit on a fresh turn.

    Zero-write / prose_idle mid-loop warn→FINALIZE is **retired** (always off).
    Files-expected idle inject (nudge / narrow / report) is **gone**. Recon-idle
    nudge is **gone**. Absolute investigation-round finalize is **retired**:
    factory always passes ``convergence_finalize_rounds=0``. Same-target spin
    (``engine_convergence_spin_rounds``) stays.
    ``report_delivery`` stays for call-site compatibility and does not drive
    idle. Orthogonal to token/timeout wind_down and never stamps DEGRADED /
    FAILED for read-idle.
    Delivery pressure otherwise stays on round/token hard ceilings + convergence
    spin. 真纯丙后不再有「白名单缺写盘 → 补写工具」半成品路径.

    ``short_write_posture`` / ``max_rounds`` remain accepted for call-site
    compatibility (formerly pulled B2 reflection cadence earlier; inject retired).

    ``tighten_verify_exec_thrash`` (repair verify short posture): lower
    unproductive + tool-failure disable thresholds so same-fail / no-output
    ``code_execute`` ladders reach nudge→finalize sooner — still the same
    LoopController paths, not a parallel fuse.
    """
    _ = (short_write_posture, max_rounds, report_delivery, files_expected)
    tool_failure_warn = settings.engine_tool_failure_warn
    tool_failure_disable = settings.engine_tool_failure_disable
    unproductive_threshold = settings.engine_unproductive_threshold
    if tighten_verify_exec_thrash:
        # Same ladders, earlier trip for verify-only short posture.
        tool_failure_disable = min(int(tool_failure_disable), 2)
        unproductive_threshold = min(int(unproductive_threshold), 2)

    controller = LoopController(
        tool_failure_warn=tool_failure_warn,
        tool_failure_disable=tool_failure_disable,
        unproductive_threshold=unproductive_threshold,
        convergence_finalize_rounds=0,
        convergence_spin_rounds=settings.engine_convergence_spin_rounds,
        expects_landing=expects_landing,
        investigation_tools=investigation_tools,
        product_landing_artifacts=product_landing_artifacts,
    )
    if seed:
        controller.apply_seed(seed)
    return controller


def resolve_openai_tool_defs(
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
) -> list[dict[str, Any]] | None:
    """Resolve the opening OpenAI tool table.

    ``disabled_tools`` is execute-deny only (circuit / channel-dead / web retire)
    and must not shrink ``tools[]`` mid-chain. Kept on the signature for call-site
    parity with ``react_loop`` / finalize.
    """
    _ = disabled_tools
    if allowed_tool_names is None:
        candidates = tools.names if tools.count > 0 else []
    else:
        candidates = list(allowed_tool_names)
    if not candidates:
        return None
    return tools.get_openai_definitions(candidates) or None


def finalize_allows_persist(
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    *,
    files_expected: bool = False,
    expects_landing: bool = False,
    workspace_channel_dead: bool = False,
) -> bool:
    """True when finalize should keep write+handoff (pinned landing / wind_down).

    Not-landing or writes absent from registry → coordination only (finalize
    does not urge writes). ``files_expected`` → execute persist when ``write``
    is registered. 真纯丙后执行层默认 unrestricted，不再依赖「名单缺写盘补写」。

    ``workspace_channel_dead`` / sticky session·channel dead → never retain persist
    (execute-layer deny; tool table stays the opening set).
    """
    if workspace_channel_dead or is_workspace_channel_sticky_dead():
        return False
    if not expects_landing or "write" not in tools.names:
        return False
    if files_expected:
        return True
    if allowed_tool_names is not None:
        return "write" in allowed_tool_names
    return True


def finalize_tool_allowlist(*, persist: bool) -> frozenset[str]:
    """Names that may still execute on a forced-finalize round."""
    if persist:
        return FINALIZE_COORDINATION_TOOLS | FINALIZE_PERSIST_TOOLS
    return FINALIZE_COORDINATION_TOOLS


def resolve_finalize_coordination_tools(
    tools: ToolRegistry,
    allowed_tool_names: list[str] | None,
    disabled_tools: set[str],
    *,
    files_expected: bool = False,
    expects_landing: bool = False,
    workspace_channel_dead: bool = False,
) -> list[dict[str, Any]] | None:
    """Execute-allowlist projection for a forced-finalize round (not the wire table).

    Wire ``tools[]`` is ``resolve_openai_tool_defs``. Default execute set =
    coordination only. When landing is in play, also execute ``write`` +
    ``handoff`` — never strip persist then claim a report-only wrap.
    """
    if allowed_tool_names is None:
        candidates = list(tools.names) if tools.count > 0 else []
    else:
        candidates = list(allowed_tool_names)
    persist = finalize_allows_persist(
        tools,
        allowed_tool_names,
        files_expected=files_expected,
        expects_landing=expects_landing,
        workspace_channel_dead=workspace_channel_dead,
    )
    allow = finalize_tool_allowlist(persist=persist)
    # ``allow`` is the sole gate: when persist is on it re-includes write.
    selected = [
        name for name in candidates if name in allow and name not in disabled_tools
    ]
    if persist:
        # Guarantee landing tools when registered (mirrors narrow_tools_for_wind_down
        # always keeping handoff even if the caller allow-list omitted it).
        for name in sorted(FINALIZE_PERSIST_TOOLS):
            if (
                name not in selected
                and name not in disabled_tools
                and name in tools.names
            ):
                selected.append(name)
    if not selected:
        return None
    return tools.get_openai_definitions(selected) or None


def is_workspace_channel_sticky_dead(tool_context: Any | None = None) -> bool:
    """True when this desk's files are unreachable (fulfiller gone, not a timeout).

    Live hub presence wins: a reconnecting or live desktop clears a stale session
    stamp. Session flag covers CEO / teammates whose backend is not a local channel.
    """
    from agentcore.runtime.coordination.session import active_coordination
    from agentcore.workspace.presence import local_workspace_files_reachable

    session = active_coordination()
    user_id = None
    backend = None
    if tool_context is not None:
        raw_uid = getattr(tool_context, "user_id", None)
        user_id = str(raw_uid).strip() if raw_uid else None
        backend = getattr(tool_context, "backend", None)
    reachable = local_workspace_files_reachable(user_id=user_id, backend=backend)
    if reachable is True:
        return False
    if reachable is False:
        return True
    return bool(session is not None and getattr(session, "workspace_channel_dead", False))


def is_exec_env_sticky_dead(tool_context: Any | None = None) -> bool:
    """True when the coordination session has latched ``exec_env_dead``.

    Nested workers often bind a child ``execution_id`` with no session of its
    own. Fall back to the conversation's registered session so teammates still
    inherit the latch. No backend-channel twin (unlike workspace channel-dead).
    """
    from agentcore.runtime.coordination.session import (
        registered_coordination_for_conversation,
        resolve_coordination_session,
    )

    eid = None
    cid = None
    if tool_context is not None:
        raw_eid = getattr(tool_context, "execution_id", None)
        eid = str(raw_eid).strip() if raw_eid else None
        raw_cid = getattr(tool_context, "conversation_id", None)
        cid = str(raw_cid).strip() if raw_cid else None
    session = resolve_coordination_session(eid)
    if session is not None and bool(getattr(session, "exec_env_dead", False)):
        return True
    if cid:
        registered = registered_coordination_for_conversation(cid)
        if registered is not None and bool(getattr(registered, "exec_env_dead", False)):
            return True
    return False


def apply_exec_env_dead_retire(
    *,
    disabled_tools: set[str],
    controller: LoopController | None = None,
    tool_context: Any | None = None,
) -> bool:
    """No-op: env-dead never strips ``run`` (per-call fail stays; tool stays).

    Kept for signature parity with :func:`apply_workspace_channel_dead_retire`
    and existing ``react_loop`` call sites. ``disabled_tools`` is unchanged.
    """
    _ = disabled_tools, controller, tool_context
    return False


def registry_can_execute(
    tools: ToolRegistry, tool_context: Any | None = None
) -> bool:
    """Whether this worker may claim / use ``run``.

    True when the tool is registered. Sticky ``session.exec_env_dead`` does
    **not** hide ``run`` (a failed call stays a failed call). Cloud-without-sandbox
    (tool absent) is the False case. ``tool_context`` kept for call-site parity.
    """
    _ = tool_context
    return tools.get_optional("run") is not None


def apply_workspace_channel_dead_retire(
    *,
    disabled_tools: set[str],
    controller: LoopController | None = None,
    tool_context: Any | None = None,
) -> bool:
    """Seed or restore execute-deny for ``WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS``.

    Called at ``react_loop`` entry and before each LLM round. The OpenAI table
    stays frozen; execute fails while the family is in ``disabled_tools``. When
    the fulfiller returns, execute is allowed again. Returns whether the
    execute-deny set changed.
    """
    from agentcore.workspace.limits import WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS

    hang = controller is not None and bool(getattr(controller, "_channel_hang_dead", False))
    if hang:
        before = len(disabled_tools)
        disabled_tools.update(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS)
        return len(disabled_tools) > before

    if not is_workspace_channel_sticky_dead(tool_context):
        return _revive_workspace_file_family(
            disabled_tools=disabled_tools,
            controller=controller,
        )

    before = len(disabled_tools)
    disabled_tools.update(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS)
    latch_flipped = False
    if controller is not None and not controller._workspace_channel_dead:
        controller._workspace_channel_dead = True
        latch_flipped = True
    return latch_flipped or len(disabled_tools) > before


def _revive_workspace_file_family(
    *,
    disabled_tools: set[str],
    controller: LoopController | None,
) -> bool:
    """Undo presence-retire when the desktop fulfiller is back.

    Only undoes a presence latch (session stamp / controller flag). A single-op
    timeout may force-retire one file tool; that is not a family presence retire
    and must stay.
    """
    from agentcore.runtime.coordination.session import active_coordination
    from agentcore.workspace.limits import (
        WORKSPACE_CHANNEL_DEAD_RETIRE_STEER,
        WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS,
    )

    session = active_coordination()
    session_stamped = session is not None and bool(
        getattr(session, "workspace_channel_dead", False)
    )
    latched = controller is not None and bool(controller._workspace_channel_dead)
    if not session_stamped and not latched:
        return False
    if session is not None and session_stamped:
        session.workspace_channel_dead = False
        session.channel_dead_user_notice_emitted = False
    if controller is not None and latched:
        controller._workspace_channel_dead = False
        family = set(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS)
        for name in family:
            controller._tool_force_retire.discard(name)
            controller._tool_disabled.discard(name)
            controller._tool_failures.pop(name, None)
        if controller._pending_retire_message == WORKSPACE_CHANNEL_DEAD_RETIRE_STEER:
            controller._pending_retire_message = None
    disabled_tools.difference_update(WORKSPACE_CHANNEL_DEAD_RETIRE_TOOLS)
    return True


@dataclass(frozen=True)
class CircuitBreakerOutcome:
    """Result of applying the B2 tool-failure circuit breaker after a tool round.

    Disabled names stay on the wire table; execute-deny is ``disabled_tools``.
    """

    message: str | None


def apply_circuit_breaker(
    controller: LoopController,
    *,
    messages: list[LLMMessage],
    run_id: str,
    round_idx: int,
    disabled_tools: set[str],
) -> CircuitBreakerOutcome:
    """Latch wedged tools for execute-deny. Does not shrink the OpenAI table."""
    breaker = controller.tool_circuit_breaker()
    if breaker.disabled:
        disabled_tools.update(breaker.disabled)
        from agentcore.runtime.audit.hooks import on_tool_disabled

        for tool_name in breaker.disabled:
            on_tool_disabled(
                tool_name=tool_name,
                run_id=run_id,
                failure_count=controller.tool_failure_count(tool_name),
            )
            # Persist web_fetch disable across react_loop restart (stream-stall →
            # Wave retry / contract write_pass). Same process + run_id.
            # web_search execute-denies with it so deep-read death cannot become
            # search thrash (failures do not charge retrieval_budget).
            if tool_name == "web_fetch":
                from agentcore.tools.builtin.web._net import (
                    WEB_FETCH_RETIRE_STEER,
                    mark_web_fetch_retired,
                )

                mark_web_fetch_retired(run_id, message=WEB_FETCH_RETIRE_STEER)
                disabled_tools.add("web_search")
    if breaker.disabled or breaker.force_segmented or breaker.validation_stop:
        logger.info(
            "engine.tool_circuit_breaker",
            warned=list(breaker.warned),
            disabled=list(breaker.disabled),
            force_segmented=sorted(breaker.force_segmented),
            round=round_idx,
        )
    return CircuitBreakerOutcome(message=None)


def decide_llm_failure(
    *,
    final_content: str,
    error_code: str = "",
    role: str = "",
    error_type: str | None = None,
    origin: str | None = None,
    classified: bool | None = None,
    error: str | None = None,
) -> LoopDirective:
    from agentcore.runtime.turn.ceo_continue import should_pause_ceo_rate_limit

    if should_pause_ceo_rate_limit(role=role, error_code=error_code):
        logger.warning(
            "engine.llm_rate_limit_paused",
            has_content=bool(final_content),
        )
        return Return(finish_reason=FinishReason.PAUSED)
    reason = FinishReason.DEGRADED if final_content else FinishReason.ERROR
    extra: dict[str, object] = {}
    if error_code:
        extra["error_code"] = error_code
    if error_type:
        extra["error_type"] = error_type
    if origin:
        extra["origin"] = origin
    if classified is not None:
        extra["classified"] = classified
    if error:
        extra["error"] = error
    logger.warning(
        "engine.llm_failed_terminal",
        reason=reason.value,
        has_content=bool(final_content),
        **extra,
    )
    return Return(finish_reason=reason)


def govern_after_tools(
    outcome: RoundOutcome,
    controller: LoopController,
    *,
    messages: list[LLMMessage],
    round_idx: int,
    run_id: str,
    breaker_message: str | None,
    role: str = "",
    disabled_tools: set[str] | None = None,
    investigation_tools: frozenset[str] | None = None,
) -> LoopDirective:
    """Run post-tool convergence governance and return the next directive.

    Steers that keep the loop going (a stuck-loop nudge, a periodic reflection)
    are injected here as side effects on ``messages`` and resolve to ``Continue``;
    a hard stop resolves to ``Finalize`` (the caller forces one tool-free round).
    ``UNPRODUCTIVE`` is stamped via the Finalize directive's ``finish_reason``.
    Convergence and reflection are suppressed when the circuit breaker already
    steered this round (``breaker_message is not None``) so steers don't stack.
    """
    controller.note_round_productivity(
        had_tool_calls=outcome.has_tool_calls,
        all_failed=outcome.all_tools_failed,
        had_content=bool(outcome.content),
        all_parse_failures=(
            bool(outcome.attempts)
            and all(not a.success for a in outcome.attempts)
            and all(a.parse_failure for a in outcome.attempts)
        ),
    )

    if controller.take_validation_hard_stop():
        # Same-fingerprint validation re-hit after path-stop steer — hard stop
        # before nudge/convergence so the run does not burn max_rounds.
        logger.warning(
            "engine.validation_thrash_stop",
            round=round_idx,
            attempts=len(outcome.attempts),
        )
        return Finalize(
            reason="validation_thrash", finish_reason=FinishReason.UNPRODUCTIVE
        )

    signal = controller.detect()
    action = controller.decide(signal)
    if signal is not None and action is Intervention.NUDGE:
        logger.info(
            "engine.loop_nudge",
            reason=signal.reason.value,
            tool=signal.tool_name,
            count=signal.count,
            round=round_idx,
        )
        maybe_inject_turn_token_budget_gate(
            controller,
            messages=messages,
            run_id=run_id,
            round_idx=round_idx,
            role=role,
        )
        return Continue()

    if signal is not None and action is Intervention.FINALIZE:
        logger.warning(
            "engine.loop_finalize",
            reason=signal.reason.value,
            tool=signal.tool_name,
            count=signal.count,
            round=round_idx,
        )
        return Finalize(reason=signal.reason.value)

    if controller.unproductive_early_stop():
        logger.warning(
            "engine.unproductive_stop", round=round_idx, attempts=len(outcome.attempts)
        )
        return Finalize(reason="unproductive", finish_reason=FinishReason.UNPRODUCTIVE)

    if breaker_message is None and controller.convergence_action() is Intervention.FINALIZE:
        # Zero-write mid-loop DEGRADED is retired; spin / absolute-cap FINALIZE
        # stays plain convergence (hard-ceiling thrashing still stamps DEGRADED).
        logger.warning(
            "engine.convergence_finalize",
            round=round_idx,
            investigation_rounds=controller.investigation_rounds,
            investigation_calls=controller.investigation_calls,
        )
        return Finalize(reason="convergence")

    maybe_inject_turn_token_budget_gate(
        controller,
        messages=messages,
        run_id=run_id,
        round_idx=round_idx,
        role=role,
    )
    return Continue()
