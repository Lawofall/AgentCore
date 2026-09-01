"""Approval + destructive baseline gates for one tool call (pre-execute)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage, ToolCall
from agentcore.runtime.always_confirm import requires_always_confirm
from agentcore.runtime.approvals import ApprovalDecision, ApprovalGate, tool_call_requires_approval
from agentcore.runtime.events import EventSink, tool_use_end
from agentcore.runtime.facts import CrossTurnRetry, cross_turn_retry_meta
from agentcore.runtime.loop_controller import ToolAttempt
from agentcore.tools.protocol import ToolContext, ToolSchema

from .tool_exec_args import (
    _attempt_meta_with_landing_path,
    _failed_tool_message,
    _shell_observe_log_fields,
)
from .tool_failure_face import tool_failure_fields

logger = get_logger(__name__)


async def _apply_local_destructive_baseline_gate(
    *,
    tool_name: str,
    args: dict[str, Any],
    context: ToolContext,
    existing: Any,
) -> Any:
    """P0a/b: Local destructive delete without zip baseline → FORCE_APPROVAL.

    Regular :func:`~agentcore.workspace.turn_baseline.maybe_capture_turn_baseline`
    remains non-blocking. This gate only upgrades the breaker hit when the call
    matches a destructive_fs heuristic, the backend is Local, and no usable zip
    can be ensured. Cloud desk guests bind the workspace (``location != local``
    skips this Local-only gate). Packaging allowlist rw-bind deletes are out of
    scope (footnote / tests).

    Readiness is ``ensure_local_baseline_for_destructive`` (Path root *or*
    desktop channel ready) — never "does backend have Path.root".

    Does not stack a second card when ``existing`` is already DENY or
    FORCE_APPROVAL — still best-effort ensures a baseline so post-approval
    restore remains possible.
    """
    from agentcore.runtime.safety_breaker import (
        BreakerVerdict,
        command_text_for_tool,
        no_turn_baseline_hit,
    )
    from agentcore.workspace.destructive_fs import (
        requires_destructive_baseline_gate,
        scan_destructive_fs,
    )
    from agentcore.workspace.turn_baseline import ensure_local_baseline_for_destructive

    if getattr(context.backend, "location", None) != "local":
        return existing
    name = (tool_name or "").strip()
    from agentcore.tools.builtin.host import host_call_is_shell

    if name not in {"run"} and not (
        name == "host" and host_call_is_shell(args)
    ):
        return existing
    if name == "run":
        action = str(args.get("action") or "").strip().lower()
        if action in {"read", "stop", "list"}:
            return existing

    fs_hit = scan_destructive_fs(command_text_for_tool(name, args))
    if not requires_destructive_baseline_gate(fs_hit):
        return existing

    # Fuse-aligned DENY already owns the card — do not zip or stack.
    if existing is not None and existing.verdict is BreakerVerdict.DENY:
        return existing

    # Prefer ServerWorkspace.root (sidecar Local). Channel-only LocalWorkspace
    # has no Path root — ask desktop ensure_turn_baseline (ready signal), never
    # invent a server-side Path.root.
    from pathlib import Path

    raw_root = getattr(context.backend, "root", None)
    workspace_root = raw_root if isinstance(raw_root, Path) else None
    from agentcore.runtime.journal.writer import current_journal_writer

    writer = current_journal_writer.get()
    message_id = (writer.turn_id if writer is not None else "") or ""

    ready = False
    if message_id:
        try:
            ready = await ensure_local_baseline_for_destructive(
                user_id=context.user_id or "",
                conversation_id=context.conversation_id or "",
                message_id=message_id,
                workspace_root=workspace_root,
                backend=None if workspace_root is not None else context.backend,
            )
        except Exception:
            logger.warning(
                "turn.local_baseline_failed",
                conversation_id=context.conversation_id,
                message_id=message_id,
                phase="destructive_ensure",
                exc_info=True,
            )
            ready = False

    if ready:
        return existing

    # Already forcing approval (e.g. P2 top-tree) — keep that card (no stack).
    if existing is not None and existing.verdict is BreakerVerdict.FORCE_APPROVAL:
        return existing
    return no_turn_baseline_hit()


@dataclass(frozen=True)
class _ToolGateDenied:
    """Early exit from safety/approval gates (no tool execute)."""

    message: LLMMessage
    attempt: ToolAttempt


async def _check_safety_and_approval_gates(
    *,
    name: str,
    args: Any,
    tool_schema: ToolSchema,
    tc: ToolCall,
    context: ToolContext,
    sink: EventSink,
    event_run_id: str,
    run_id: str,
    role: str,
    fingerprint: str,
    approval_gate: ApprovalGate | None,
) -> _ToolGateDenied | None:
    """P3 breaker + Local destructive baseline + approval authorize.

    The single chokepoint for「这次调用要不要人批」. It runs in two independent steps
    and never collapses them:

    1. **要不要审批** — schema ``GRANTABLE`` / runtime-elevated git·terminal writes /
       恒确认 / 熔断 FORCE, minus the exemptions in ``runtime.sandbox_approval``
       (cloud-worker sandbox ungate, execution auto-pass) and the captain browser
       short op. Nothing here reads ``approval_gate``: whether a caller happened to
       hand one over says nothing about whether the *tool* is dangerous.
    2. **有没有人可问** — only then does ``approval_gate is None`` matter, and it
       means exactly one thing: this path has no user to ask (unattended job / ops
       kill switch). A call that needs approval there is DENIED, not waved through.

    Callers must therefore pass the turn's gate down verbatim and let this function
    narrow — see :func:`~agentcore.runtime.delegate.drive_setup.resolve_worker_gate`.

    Returns a deny outcome, or ``None`` when the call may proceed to execute.
    """
    # P3 safety circuit breaker — last-line heuristic (not a security boundary).
    # full_trust / kickoff / turn grants never override FORCE_APPROVAL or DENY.
    from agentcore.runtime.safety_breaker import BreakerVerdict, evaluate_tool_call

    breaker = evaluate_tool_call(name, args)
    # P0a/b: Local destructive_fs without usable zip → FORCE_APPROVAL (分轨).
    # Runs after sync evaluate so P2 top-tree / fuse DENY stay single-card;
    # still best-effort ensures baseline when already forcing.
    if isinstance(args, dict):
        breaker = await _apply_local_destructive_baseline_gate(
            tool_name=name,
            args=args,
            context=context,
            existing=breaker,
        )
    if breaker is not None and breaker.verdict is BreakerVerdict.DENY:
        from agentcore.runtime.audit.hooks import on_circuit_breaker

        on_circuit_breaker(
            tool_name=name,
            tool_call_id=tc.id,
            rule_id=breaker.rule_id,
            verdict=breaker.verdict.value,
            reason=breaker.reason,
            run_id=run_id or None,
        )
        denial = (
            f"工具 '{name}' 被安全熔断拒绝：{breaker.reason}"
            "请改用其他方案，不要原样重试该路径。"
        )
        # ``denial`` steers the model and stays on ``result``; the user face is curated
        # by code only (see tool_failure_face) — an order aimed at the model is not copy.
        sink.emit(
            tool_use_end(
                tc.id,
                name,
                success=False,
                output=denial,
                failure=tool_failure_fields(code="safety_breaker_deny"),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.execute_end",
            tool=name,
            status="circuit_breaker_deny",
            rule_id=breaker.rule_id,
            duration_ms=0,
            **_shell_observe_log_fields(name, args),
        )
        return _ToolGateDenied(
            message=_failed_tool_message(tc.id, denial),
            attempt=ToolAttempt(
                fingerprint,
                name,
                success=False,
                policy_failure=True,
                meta=_attempt_meta_with_landing_path(name, args),
            ),
        )

    # Bool flag for later gates; attribute access stays under the narrowed `if`
    # so mypy does not treat `breaker` as still optional inside the block.
    force_breaker = (
        breaker is not None and breaker.verdict is BreakerVerdict.FORCE_APPROVAL
    )
    # Sensitive credential read stays FORCE at the entrance (needs_approval /
    # no sandbox auto-pass) but authorize uses force=False so APPROVE_ALWAYS
    # can write a same-tool turn grant. True destructive / no-baseline / top-tree
    # FORCE keep force=True (one-shot; turn grant refused).
    allow_turn_grant = (
        force_breaker
        and breaker is not None
        and breaker.rule_id == "sensitive.path_read_ask"
    )
    authorize_force = force_breaker and not allow_turn_grant
    if breaker is not None and breaker.verdict is BreakerVerdict.FORCE_APPROVAL:
        from agentcore.runtime.audit.hooks import on_circuit_breaker

        on_circuit_breaker(
            tool_name=name,
            tool_call_id=tc.id,
            rule_id=breaker.rule_id,
            verdict=breaker.verdict.value,
            reason=breaker.reason,
            run_id=run_id or None,
        )
        # Surface a preview-only hint on the approval card (arguments are already
        # truncated for SSE; tools execute the original ``args`` unchanged).
        # Machine-readable rule_id / force_one_shot let clients hide turn-grant
        # buttons and pick copy without treating hint presence as "fuse".
        hint = breaker.reason
        if breaker.rule_id == "sensitive.path_read_ask" and isinstance(args, dict):
            from agentcore.runtime.credential_preview import build_keys_preview_line

            keys_line = await build_keys_preview_line(
                context.backend, tool_name=name, arguments=args
            )
            if keys_line:
                hint = f"{hint}\n{keys_line}"
        args_for_gate = {
            **args,
            "circuit_breaker_hint": hint,
            "rule_id": breaker.rule_id,
        }
        if authorize_force:
            args_for_gate["force_one_shot"] = True
    else:
        args_for_gate = args

    # 恒确认 (git push / create_pr · host install_package): the truth source lives
    # in ``runtime.always_confirm`` — above BOTH the gate and every pre-authorize
    # skip below — because it used to be private to ``ApprovalGate.authorize`` and
    # anything short-circuiting earlier silently published to the remote.
    always_confirm = isinstance(args, dict) and requires_always_confirm(name, args)
    # 「这个工具要不要审批」必须先独立算完，**不得**以「有没有 gate 对象」为判据——否则
    # 一个漏传 gate 的调用点连问都不问就放行（fail-open），而漏传恰恰看不出来。有没有人
    # 可问是下一个问题（见下方 ``approval_gate is None`` 分支）。
    needs_approval = (
        force_breaker
        or always_confirm
        or tool_call_requires_approval(name, tool_schema.approval, args)
    )
    # CEO 短操作：captain 直调 browser（除 screenshot）不弹审批；force_breaker 仍拦。
    # 执行层不转发旧 browser_* 名。
    if needs_approval and not force_breaker and role == "captain" and name == "browser":
        from agentcore.tools.builtin.browser import browser_action_name

        if browser_action_name(args if isinstance(args, dict) else None) != "screenshot":
            needs_approval = False
    # Cloud *workers* historically ungated for server-sandbox tools (MCP/Host
    # still gated). ``file_write=ask`` overrides that ungate for the
    # file-mutation class so 谨慎 prompts reversible writes on cloud too.
    # CEO / captain always keep full GRANTABLE gating — do not key off
    # backend.location alone.
    if needs_approval and not force_breaker and role == "worker":
        from agentcore.runtime.sandbox_approval import cloud_worker_skips_per_call_gate

        if cloud_worker_skips_per_call_gate(
            context.backend,
            name,
            arguments=args if isinstance(args, dict) else None,
            # 会话权限轴随 gate 一起装配，所以无 gate 的路径（handoff job / 评测）本就
            # 没有会话轴可读 —— 传 None 即历史云端免审口径，不在此另起一套判断。
            permission_axes=(
                approval_gate.permission_axes if approval_gate is not None else None
            ),
            file_op_tools=(
                approval_gate.file_op_tools if approval_gate is not None else frozenset()
            ),
        ):
            needs_approval = False
    # Re-assert after every downgrade above: a 恒确认 call keeps its card no matter
    # which skip a future branch adds here.
    if always_confirm:
        needs_approval = True
    if needs_approval:
        from agentcore.runtime.sandbox_approval import execution_tool_auto_passes

        if approval_gate is None:
            # 该问而没人可问 → fail closed。到这里 ``approval_gate is None`` 只剩一个
            # 含义：这条路根本没有可询问的用户（无人值守作业 / 运维关闸），**不是**
            # 「这条路不需要卡」——后者已在上面按 sandbox_approval 判掉了。
            # Model face keeps「让用户在可确认的界面重试」; the user face must not — on an
            # unattended path the reader IS the user and that surface does not exist.
            if force_breaker:
                no_gate_status = "circuit_breaker_no_gate"
                no_gate_face = "safety_breaker_unattended"
                denial = (
                    f"工具 '{name}' 触发安全熔断且当前路径无法人工确认，已拒绝执行。"
                    f"{breaker.reason if breaker else ''}"
                    "请改用其他方案。"
                )
            elif always_confirm:
                no_gate_status = "always_confirm_no_gate"
                no_gate_face = "approval_unattended"
                denial = (
                    f"工具 '{name}' 必须由用户逐次确认，但当前路径弹不出确认卡，已拒绝执行。"
                    "请改用其他方案，或让用户在可确认的界面重试。"
                )
            else:
                no_gate_status = "grantable_no_gate"
                no_gate_face = "approval_unattended"
                denial = (
                    f"工具 '{name}' 需要用户授权，但当前路径没有可询问的用户，已拒绝执行。"
                    "请改用其他方案，或让用户在可确认的界面重试。"
                )
            sink.emit(
                tool_use_end(
                    tc.id,
                    name,
                    success=False,
                    output=denial,
                    failure=tool_failure_fields(code=no_gate_face),
                    run_id=event_run_id,
                )
            )
            logger.info(
                "tool.execute_end",
                tool=name,
                status=no_gate_status,
                duration_ms=0,
                **_shell_observe_log_fields(name, args),
            )
            return _ToolGateDenied(
                message=_failed_tool_message(tc.id, denial),
                attempt=ToolAttempt(
                    fingerprint,
                    name,
                    success=False,
                    policy_failure=True,
                    meta=_attempt_meta_with_landing_path(name, args),
                ),
            )

        auto_pass = (not force_breaker) and execution_tool_auto_passes(
            context.backend, name, permission_axes=approval_gate.permission_axes
        )
        # INFO（非 debug）：round_end 后若长时间无 execute_end，靠此定位卡在审批还是执行。
        # will_prompt peeks kickoff/session/_granted/_denied short-circuits so
        # awaiting_approval is not true when authorize would silently pass.
        awaiting_approval = (not auto_pass) and approval_gate.will_prompt(
            tool_name=name,
            arguments=args_for_gate,
            execution_id=context.execution_id,
            force=authorize_force,
        )
        logger.info(
            "tool.execute_start",
            tool=name,
            tool_call_id=tc.id,
            run_id=run_id or "",
            awaiting_approval=awaiting_approval,
            **_shell_observe_log_fields(name, args),
        )
        if auto_pass:
            logger.info("approval.sandbox_auto_pass", tool=name)
        else:
            if awaiting_approval:
                # Only when a human will actually see the card: resolve id-only
                # arguments (delete_folder → 文件夹路径) from the authoritative source
                # so the user is not asked to approve a bare UUID.
                from agentcore.runtime.approval_preview import enrich_approval_preview

                args_for_gate = await enrich_approval_preview(
                    tool_name=name,
                    arguments=args_for_gate,
                    user_id=context.user_id or "",
                )
            decision = await approval_gate.authorize(
                tool_name=name,
                tool_call_id=tc.id,
                arguments=args_for_gate,
                execution_id=context.execution_id,
                force=authorize_force,
            )
            if decision is ApprovalDecision.DENY:
                # Denial is a governance signal (user refuse / timeout), not an execution
                # failure — mark policy_failure so the run-scoped circuit breaker ignores it.
                denial = (
                    f"工具 '{name}' 未获用户授权，该操作未执行。"
                    "请改用其他方案或询问如何继续，不要再调用此工具。"
                )
                # The user just clicked 拒绝 (or let the card expire) — echoing the
                # model's「不要再调用此工具」back at them reads as an order to the person
                # who made the call. User face is curated by code only.
                sink.emit(
                    tool_use_end(
                        tc.id,
                        name,
                        success=False,
                        output=denial,
                        failure=tool_failure_fields(code="approval_denied"),
                        run_id=event_run_id,
                    )
                )
                logger.info(
                    "tool.execute_end",
                    tool=name,
                    status="denied",
                    duration_ms=0,
                    **_shell_observe_log_fields(name, args),
                )
                return _ToolGateDenied(
                    message=_failed_tool_message(tc.id, denial),
                    attempt=ToolAttempt(
                        fingerprint,
                        name,
                        success=False,
                        policy_failure=True,
                        meta=_attempt_meta_with_landing_path(
                            name,
                            args,
                            cross_turn_retry_meta(CrossTurnRetry.FUTILE),
                        ),
                    ),
                )
    else:
        logger.info(
            "tool.execute_start",
            tool=name,
            tool_call_id=tc.id,
            run_id=run_id or "",
            awaiting_approval=False,
            **_shell_observe_log_fields(name, args),
        )
    return None
