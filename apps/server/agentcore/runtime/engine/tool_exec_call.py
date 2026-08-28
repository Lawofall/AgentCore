"""Single tool-call lifecycle for one ReAct round (parse → gates → execute → end)."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import time
from dataclasses import replace
from typing import Any

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import AgentCoreError
from agentcore.core.logging import get_logger
from agentcore.core.task_cancel import task_is_cancelling
from agentcore.core.types import ToolEffect
from agentcore.llm.provider.protocol import LLMMessage, ToolCall
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.events import (
    EventSink,
    run_phase,
    tool_use_end,
    tool_use_progress,
    tool_use_start,
)
from agentcore.runtime.facts import CrossTurnRetry, cross_turn_retry_meta
from agentcore.runtime.loop_controller import (
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_PERMISSION,
    ERROR_CLASS_VALIDATION,
    ToolAttempt,
    fingerprint_tool_call,
)
from agentcore.runtime.tool_deadline import reset_tool_deadline, set_tool_deadline
from agentcore.tools.file_products import LANDING_TOOLS, with_file_products_marker
from agentcore.tools.protocol import TOOL_AUDIENCE_CEO, ToolContext, ToolResult
from agentcore.tools.registry import ToolRegistry

from .timeout import resolve_tool_timeout
from .tool_channel_redirect import tool_wire_status
from .tool_exec_args import (
    _ARGS_PARSE_FAILED_MARKER,
    _attempt_meta_with_landing_path,
    _failed_tool_message,
    _format_args_parse_error,
    _leaked_cancel_quad,
    _missing_tool_feedback,
    _shell_observe_log_fields,
    _short_tool_error_reason,
    with_tool_failed_marker,
)
from .tool_exec_coalesce import _clone_tool_result, _file_read_round_coalesce_key
from .tool_exec_gates import _check_safety_and_approval_gates
from .tool_failure_face import tool_failure_fields, tool_failure_from_result
from .tool_protocol_sanitize import (
    parse_tool_call_arguments,
    sanitize_raw_tool_arguments,
    sanitize_tool_args,
    sanitize_tool_name,
    unwrap_nested_delegate_arguments,
)
from .write_args_clear import landed_status_name_rejection

logger = get_logger(__name__)

_MISSING_FILE_MODEL_MSG = "内部资源缺失，请换一种方式继续，不要原样重试。"


def _is_missing_file_exc(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOENT


type ToolCallQuad = tuple[LLMMessage, ToolResult | None, ToolAttempt, list[dict[str, Any]]]


async def run_one_tool(
    tc: ToolCall,
    *,
    registry: ToolRegistry,
    context: ToolContext,
    sink: EventSink,
    event_run_id: str,
    run_id: str,
    role: str,
    allowed_set: frozenset[str] | None,
    approval_gate: ApprovalGate | None,
    file_read_inflight: dict[str, asyncio.Future[ToolResult]],
) -> ToolCallQuad:
    """Parse, gate, execute, and emit wire events for one tool call.

    ``file_read_inflight`` is round-owned same-path coalesce state shared across
    parallel calls. Return shape matches the gather quad used by
    :func:`execute_tools`.
    """
    raw_name = tc.function.name or ""
    name = sanitize_tool_name(raw_name)
    # Mutate in place so transcript / debrief harvest see the cleaned name
    # (same ToolCall objects live on the assistant message).
    if name != raw_name:
        tc.function.name = name
        logger.info(
            "tool.name_protocol_sanitized",
            tool_call_id=tc.id,
            raw_name=raw_name[:120],
            cleaned_name=name[:80],
        )
    raw_args = tc.function.arguments or ""
    # Strip vendor/XML protocol residue before parse so hybrid leaks
    # (``{"tasks"><parameter…>``) become retryable JSON when salvageable.
    parse_args = sanitize_raw_tool_arguments(raw_args)
    if parse_args != raw_args:
        with contextlib.suppress(TypeError, ValueError):
            tc.function.arguments = parse_args
        raw_args = parse_args
    fingerprint = fingerprint_tool_call(name, raw_args)
    parse_exc: json.JSONDecodeError | None = None
    try:
        args, repaired = parse_tool_call_arguments(raw_args, tool_name=name or raw_name)
    except json.JSONDecodeError as exc:
        parse_exc = exc
        args = {}
    else:
        if repaired is not None:
            logger.info(
                "tool.args_salvaged",
                tool=name or raw_name,
                tool_call_id=tc.id,
                args_preview=repaired[:200],
            )
            with contextlib.suppress(TypeError, ValueError):
                tc.function.arguments = repaired
            raw_args = repaired
            fingerprint = fingerprint_tool_call(name, raw_args)
    if parse_exc is not None:
        model_msg, user_msg, parse_class = _format_args_parse_error(
            name or raw_name, raw_args, parse_exc
        )
        # Honest wire pair: marker args (not ``{}``) + error end — never run the tool.
        # Model transcript + ``result`` keep technical tip; ``failure`` carries user face.
        sink.emit(
            tool_use_start(
                tc.id, name or raw_name, dict(_ARGS_PARSE_FAILED_MARKER), run_id=event_run_id
            )
        )
        sink.emit(
            tool_use_end(
                tc.id,
                name or raw_name,
                success=False,
                output=model_msg,
                failure=tool_failure_fields(
                    code="args_parse_failed",
                    # Write tools author a short human line; other tools keep
                    # technical tip only on model face (user_msg == model_msg).
                    product_message=user_msg if user_msg != model_msg else None,
                ),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.args_parse_failed",
            tool=name or raw_name,
            tool_call_id=tc.id,
            pos=parse_exc.pos,
            msg=parse_exc.msg,
            args_preview=raw_args[:200],
            parse_class=parse_class,
        )
        logger.info(
            "tool.execute_end",
            tool=name or raw_name,
            status="args_parse_failed",
            duration_ms=0,
        )
        return (
            _failed_tool_message(tc.id, model_msg),
            None,
            ToolAttempt(
                fingerprint,
                name or raw_name,
                success=False,
                parse_failure=True,
                error_summary=model_msg,
                meta={"error_class": ERROR_CLASS_VALIDATION},
            ),
            [],
        )

    if isinstance(args, dict):
        if name == "delegate":
            unwrapped = unwrap_nested_delegate_arguments(args)
            if unwrapped is not None:
                logger.info(
                    "tool.delegate_arguments_unwrapped",
                    tool_call_id=tc.id,
                    inner_keys=sorted(unwrapped.keys())[:20],
                    has_tasks=isinstance(unwrapped.get("tasks"), list)
                    and bool(unwrapped.get("tasks")),
                    has_playbook=bool(
                        isinstance(unwrapped.get("playbook"), str)
                        and str(unwrapped.get("playbook") or "").strip()
                    ),
                )
                args = unwrapped
                with contextlib.suppress(TypeError, ValueError):
                    tc.function.arguments = json.dumps(args, ensure_ascii=False)
        cleaned_args = sanitize_tool_args(args)
        if cleaned_args != args:
            args = cleaned_args
            with contextlib.suppress(TypeError, ValueError):
                tc.function.arguments = json.dumps(args, ensure_ascii=False)

    sink.emit(tool_use_start(tc.id, name, args, run_id=event_run_id))
    if event_run_id:
        sink.emit(
            run_phase(
                event_run_id,
                getattr(context, "agent_id", "") or event_run_id,
                "tool",
                tool_name=name or raw_name or None,
            )
        )

    # Legacy write-args projection bait: reject before allowlist/not_found.
    landed_name_err = landed_status_name_rejection(name or raw_name)
    if landed_name_err:
        # ``landed_name_err`` teaches the model the read-then-rewrite path; user face
        # is curated by code only.
        sink.emit(
            tool_use_end(
                tc.id,
                name or raw_name,
                success=False,
                output=landed_name_err,
                failure=tool_failure_fields(code="landed_status_name"),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.execute_end",
            tool=name or raw_name,
            status="landed_status_name",
            duration_ms=0,
            reason=landed_name_err,
        )
        return (
            _failed_tool_message(tc.id, landed_name_err),
            None,
            ToolAttempt(
                fingerprint,
                name or raw_name,
                success=False,
                policy_failure=True,
                error_summary=landed_name_err,
                meta={
                    "error_class": ERROR_CLASS_VALIDATION,
                    "permission_kind": "landed_status_name",
                },
            ),
            [],
        )

    if allowed_set is not None and name not in allowed_set:
        if name in LANDING_TOOLS:
            # 白名单限制：说明限制即可；禁止劝「handoff 正文交差」冒充写盘。
            error_msg = (
                f"工具 '{name}' 不在本 run 的允许列表中，未执行。"
                "本回合未授权该写盘工具；请改用已提供的工具，或 escalate / "
                "handoff 说明缺写盘权限（勿用正文冒充落盘）。"
            )
            deny_status = "allowlist_deny"
        else:
            error_msg = (
                f"工具 '{name}' 不在本 run 的允许列表中，未执行。"
                "请仅使用当前已提供的工具，不要调用未授权的写盘或其他副作用工具。"
            )
            deny_status = "allowlist_deny"
        # ``error_msg`` names the run allow-list and steers the model; user face is
        # curated by code only.
        sink.emit(
            tool_use_end(
                tc.id,
                name or raw_name,
                success=False,
                output=error_msg,
                failure=tool_failure_fields(code="allowlist_deny"),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.execute_end",
            tool=name or raw_name,
            status=deny_status,
            duration_ms=0,
            reason=error_msg,
        )
        return (
            _failed_tool_message(tc.id, error_msg),
            None,
            ToolAttempt(
                fingerprint,
                name or raw_name,
                success=False,
                policy_failure=True,
                error_summary=error_msg,
                meta=_attempt_meta_with_landing_path(
                    name or raw_name,
                    args,
                    {
                        "error_class": ERROR_CLASS_PERMISSION,
                        "permission_kind": "allowlist",
                        **cross_turn_retry_meta(CrossTurnRetry.FUTILE),
                    },
                ),
            ),
            [],
        )

    tool = registry.get_optional(name) if name else None
    if tool is None:
        missing = name or raw_name
        error_msg, status, policy_failure = _missing_tool_feedback(
            missing, raw_name=raw_name, registry=registry
        )
        # ``error_msg`` carries role/assembly steering (CEO vs worker, 勿空转重试) —
        # model-only. The user face is curated by code: a gated-off tool reads as
        # out-of-scope, an unknown name as a step we routed around.
        sink.emit(
            tool_use_end(
                tc.id,
                name or raw_name,
                success=False,
                output=error_msg,
                failure=tool_failure_fields(
                    code=ErrorCode.TOOL_NOT_FOUND if status == "not_found" else "allowlist_deny",
                ),
                run_id=event_run_id,
            )
        )
        logger.info(
            "tool.execute_end",
            tool=name or raw_name,
            status=status,
            duration_ms=0,
        )
        return (
            _failed_tool_message(tc.id, error_msg),
            None,
            ToolAttempt(
                fingerprint,
                name or raw_name,
                success=False,
                policy_failure=policy_failure,
                error_summary=error_msg,
                meta=_attempt_meta_with_landing_path(
                    name or raw_name,
                    args,
                    (
                        cross_turn_retry_meta(CrossTurnRetry.FUTILE)
                        if status != "not_found"
                        else None
                    ),
                ),
            ),
            [],
        )

    denied = await _check_safety_and_approval_gates(
        name=name,
        args=args,
        tool_schema=tool.schema,
        tc=tc,
        context=context,
        sink=sink,
        event_run_id=event_run_id,
        run_id=run_id,
        role=role,
        fingerprint=fingerprint,
        approval_gate=approval_gate,
    )
    if denied is not None:
        return denied.message, None, denied.attempt, []

    # 检索预算 (提案 A1): reserve a per-run slot immediately before execute so
    # approval / breaker denials never consume budget. Orthogonal to
    # LoopController.investigation_calls.
    from agentcore.runtime.runs.retrieval_budget import (
        RETRIEVAL_TOOL_NAMES,
        budget_exhausted_output,
        charges_retrieval_budget,
    )

    budget_state = context.retrieval_budget
    budget_reserved = False
    if name in RETRIEVAL_TOOL_NAMES and budget_state is not None:
        if not await budget_state.try_reserve(name):
            exhausted = budget_exhausted_output()
            sink.emit(
                tool_use_end(
                    tc.id,
                    name,
                    success=False,
                    output=exhausted,
                    failure=tool_failure_fields(code="retrieval_budget_exhausted"),
                    run_id=event_run_id,
                )
            )
            logger.info(
                "tool.execute_end",
                tool=name,
                status="retrieval_budget_exhausted",
                duration_ms=0,
                retrieval_budget_limit=budget_state.limit,
                retrieval_budget_used=budget_state.used,
            )
            return (
                _failed_tool_message(tc.id, exhausted),
                None,
                ToolAttempt(
                    fingerprint,
                    name,
                    success=False,
                    error_summary=exhausted,
                    meta=_attempt_meta_with_landing_path(
                        name,
                        args,
                        {
                            "error_class": ERROR_CLASS_PERMANENT,
                            "code": "retrieval_budget_exhausted",
                            "retire_tools": sorted(RETRIEVAL_TOOL_NAMES),
                            "retire_message": (
                                "检索预算已尽：web_search / read_url 本回合已停用——"
                                "请基于已有材料交付，禁止再调用检索工具。"
                            ),
                        },
                    ),
                ),
                [],
            )
        budget_reserved = True

    # 工具执行阶段进度 (联网搜索前端展示优化): inject a per-call phase callback so a
    # long-running tool (web_search) can report a coarse EXECUTION phase mid-flight. The
    # executor owns event shape (引擎纯化) — the tool passes only a phase token; we close
    # over this call's id/name/event_run_id and emit the transport-only ``tool_use_progress``.
    def _emit_phase(phase: str) -> None:
        sink.emit(tool_use_progress(tc.id, name, phase, run_id=event_run_id))

    def _emit_progress(phase: str, data: dict[str, Any] | None = None) -> None:
        sink.emit(tool_use_progress(tc.id, name, phase, run_id=event_run_id, extra=data))

    ctx = replace(context, on_phase=_emit_phase, on_progress=_emit_progress)

    started = time.monotonic()
    timeout = resolve_tool_timeout(tool.schema, args)
    deadline_token = set_tool_deadline(timeout)
    coalesce_key = _file_read_round_coalesce_key(args) if name == "file_read" else None
    try:
        if coalesce_key is not None:
            existing = file_read_inflight.get(coalesce_key)
            if existing is not None:
                shared = await existing
                result = _clone_tool_result(shared, tc.id)
                result.duration_ms = int((time.monotonic() - started) * 1000)
            else:
                fut: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
                file_read_inflight[coalesce_key] = fut
                try:
                    if timeout is None:
                        result = await tool.execute(args, ctx)
                    else:
                        result = await asyncio.wait_for(tool.execute(args, ctx), timeout)
                    # Snapshot before this call mutates ``tool_call_id``.
                    if not fut.done():
                        fut.set_result(replace(result))
                except Exception as exc:
                    if not fut.done():
                        fut.set_exception(exc)
                    raise
        elif timeout is None:
            result = await tool.execute(args, ctx)
        else:
            result = await asyncio.wait_for(tool.execute(args, ctx), timeout)
    except TimeoutError:
        # B1 backstop: the call blew its ceiling. wait_for has already cancelled
        # the tool coroutine (a cancel-safe tool releases its side effects in
        # turn — e.g. the sandbox kills its subprocess); surface a model-facing
        # error so the loop adapts instead of hanging, and count it as a failed
        # attempt so a tool that keeps timing out trips convergence governance.
        # Liveness (hang) ≠ capacity contract — steer forbids identical retry.
        if budget_reserved and budget_state is not None:
            await budget_state.refund(name)
        duration_ms = int((time.monotonic() - started) * 1000)
        ceiling = timeout if timeout is not None else 0.0
        timeout_msg = (
            f"工具 '{name}' 活性挂起：超过 {ceiling:.0f}s 仍无响应，已中止。"
            "这不是字节/行数触顶——请缩小处理范围、换路径策略或换工具；"
            "禁止原样重试同一次调用。"
        )
        # The 活性挂起 / 触顶 distinction and the no-identical-retry ban exist to steer
        # the model; they stay on ``result``. User face is curated by code only.
        sink.emit(
            tool_use_end(
                tc.id,
                name,
                success=False,
                output=timeout_msg,
                failure=tool_failure_fields(code="liveness_timeout"),
                run_id=event_run_id,
            )
        )
        timeout_fields: dict[str, Any] = {
            "tool": name,
            "status": "timeout",
            "duration_ms": duration_ms,
            "timeout_layer": "outer",
            "liveness_timeout": True,
        }
        if name == "git" and isinstance(args.get("subcommand"), str):
            timeout_fields["subcommand"] = args["subcommand"]
        timeout_fields.update(_shell_observe_log_fields(name, args))
        logger.warning("tool.execute_end", **timeout_fields)
        return (
            _failed_tool_message(tc.id, timeout_msg),
            None,
            ToolAttempt(
                fingerprint,
                name,
                success=False,
                error_summary=timeout_msg,
                meta=_attempt_meta_with_landing_path(
                    name,
                    args,
                    {
                        "liveness_timeout": True,
                        "timeout_layer": "outer",
                        "error_class": ERROR_CLASS_PERMANENT,
                        **cross_turn_retry_meta(CrossTurnRetry.NOT_FUTILE),
                    },
                ),
            ),
            [],
        )
    except asyncio.CancelledError:
        # Real Stop: this task is cancelling → propagate so the turn salvages.
        # Leaked child cancel (httpx timeout wrap): isolate like a crash.
        if task_is_cancelling():
            raise
        if budget_reserved and budget_state is not None:
            await budget_state.refund(name)
        return _leaked_cancel_quad(
            tool_call_id=tc.id,
            name=name,
            args=args,
            fingerprint=fingerprint,
            started=started,
            event_run_id=event_run_id,
            sink=sink,
            error_msg=(
                f"工具 '{name}' 执行被中止。请换来源或缩小范围继续，不要原样重试。"
            ),
        )
    except Exception as e:
        # Per-tool exception firewall (audit/05 P2-1): a crash in one parallel call
        # must not cancel its siblings via asyncio.gather. Convert to a failed tool
        # result so the loop can adapt; SUSPEND terminals are unaffected (they return
        # normally, never raise).
        if budget_reserved and budget_state is not None:
            await budget_state.refund(name)
        duration_ms = int((time.monotonic() - started) * 1000)
        if _is_missing_file_exc(e):
            error_msg = f"工具 '{name}' {_MISSING_FILE_MODEL_MSG}"
            sink.emit(
                tool_use_end(
                    tc.id,
                    name,
                    success=False,
                    output=error_msg,
                    failure=tool_failure_fields(code=ErrorCode.NOT_FOUND),
                    run_id=event_run_id,
                )
            )
            logger.exception(
                "tool.execute_end",
                tool=name,
                status="error",
                duration_ms=duration_ms,
                reason=_short_tool_error_reason(error_msg),
                **_shell_observe_log_fields(name, args),
            )
            return (
                _failed_tool_message(tc.id, error_msg),
                None,
                ToolAttempt(
                    fingerprint,
                    name,
                    success=False,
                    contract_failure=True,
                    error_summary=error_msg,
                    meta=_attempt_meta_with_landing_path(
                        name,
                        args,
                        {"error_class": ERROR_CLASS_VALIDATION},
                        error=error_msg,
                        contract_failure=True,
                    ),
                ),
                [],
            )
        # Always carry the exception type: some builtins (e.g. NotImplementedError)
        # stringify to "" and the model would see a blank reason and retry blindly.
        detail = str(e).strip()
        detail = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
        error_msg = (
            f"工具 '{name}' 执行时发生内部错误：{detail}。请调整方案或换一种方式，不要原样重试。"
        )
        # Crash firewall: model face keeps ``detail``; user face is curated by
        # exception code only — never pass through ``exc.message`` (often embeds
        # ``str(cause)``). AgentCoreError product-copy pass-through is for
        # authored ToolResult.failure_message / engine deny paths.
        fail_code = e.code if isinstance(e, AgentCoreError) else ErrorCode.TOOL_ERROR
        sink.emit(
            tool_use_end(
                tc.id,
                name,
                success=False,
                output=error_msg,
                failure=tool_failure_fields(code=fail_code),
                run_id=event_run_id,
            )
        )
        logger.exception(
            "tool.execute_end",
            tool=name,
            status="crash",
            duration_ms=duration_ms,
            **_shell_observe_log_fields(name, args),
        )
        return (
            _failed_tool_message(tc.id, error_msg),
            None,
            ToolAttempt(
                fingerprint,
                name,
                success=False,
                error_summary=error_msg,
                meta=_attempt_meta_with_landing_path(name, args),
            ),
            [],
        )
    finally:
        reset_tool_deadline(deadline_token)
    result.tool_call_id = tc.id

    # 缓存命中 / A3 拒绝等不计预算：reserved slot refunded when not charged.
    if budget_reserved and budget_state is not None and not charges_retrieval_budget(result):
        await budget_state.refund(name)

    if result.success:
        output = result.output
    else:
        # Surface BOTH the terse error summary AND any diagnostic output
        # (stdout/stderr for code_execute) so the model can self-correct
        # instead of debugging blind: many tools put the real reason in
        # ``output``, not the short ``error`` (e.g. code_execute's error is
        # just "退出码 N" while the traceback / "command not found" lives in
        # output). Either may be empty; join the non-empty parts.
        # Identical error+output (common when tools mirror the same string)
        # must not double the model-visible failure text.
        err_part = (result.error or "").strip()
        out_part = (result.output or "").strip()
        if err_part and out_part and err_part == out_part:
            output = err_part
        else:
            output = "\n".join(p for p in (err_part, out_part) if p) or "Unknown error"
    # 挂起即收口: a SUSPEND terminal already persisted its *_required card in the
    # pause snapshot. Emitting a durable tool_use_end here would append a fact that
    # diverges snapshot vs DB (and the call stays PENDING — no tool_call fact either).
    # Live UI already has the interaction card; skip the end event entirely.
    if result.effect is not ToolEffect.SUSPEND:
        end_kwargs: dict[str, Any] = {
            "display": result.display,
            "run_id": event_run_id,
        }
        if not result.success:
            end_kwargs["failure"] = tool_failure_from_result(result)
        if result.metadata.get("partial_failure"):
            end_kwargs["partial_failure"] = True
        if getattr(result, "audience", None) == TOOL_AUDIENCE_CEO:
            end_kwargs["audience"] = TOOL_AUDIENCE_CEO
        sink.emit(
            tool_use_end(
                tc.id,
                name,
                success=result.success,
                output=output,
                **end_kwargs,
            )
        )
    # 检索观测：web_search 把 query / hosts 放进 metadata；code_search 把
    # index_status 放进 metadata——一并转发到 execute_end，便于从统一工具结束
    # 事件还原「搜了什么 / 命中哪些域 / 索引快照新鲜度」。
    wire_fail_code: str | None = result.failure_code
    if not isinstance(wire_fail_code, str) or not wire_fail_code.strip():
        meta_code = (result.metadata or {}).get("code") if result.metadata else None
        wire_fail_code = meta_code if isinstance(meta_code, str) else None
    wire_status = tool_wire_status(success=result.success, failure_code=wire_fail_code)
    end_fields: dict[str, Any] = {
        "tool": name,
        "status": "ok" if wire_status == "success" else wire_status,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    if wire_status == "error":
        # Short aggregable failure reason (true faults only). Full text stays on
        # tool_use_end.output / transcript; logs need a greppable tip without adjacent
        # event archaeology. Channel steers are status=redirect — no fault reason.
        end_fields["reason"] = _short_tool_error_reason(output)
    meta = result.metadata or {}
    if isinstance(meta.get("query"), str) and meta["query"]:
        end_fields["query"] = meta["query"]
    if isinstance(meta.get("hosts"), list):
        end_fields["hosts"] = meta["hosts"]
    if isinstance(meta.get("blocked_hosts"), list) and meta["blocked_hosts"]:
        end_fields["blocked_hosts"] = meta["blocked_hosts"]
    if isinstance(meta.get("subcommand"), str) and meta["subcommand"]:
        end_fields["subcommand"] = meta["subcommand"]
    if isinstance(meta.get("timeout_layer"), str) and meta["timeout_layer"]:
        end_fields["timeout_layer"] = meta["timeout_layer"]
    if isinstance(meta.get("index_status"), str) and meta["index_status"]:
        end_fields["index_status"] = meta["index_status"]
    end_fields.update(_shell_observe_log_fields(name, args))
    logger.info("tool.execute_end", **end_fields)

    citations = result.citations if (result.success and result.citations) else []
    # 落盘产物自报 + 执行层失败：两条机器尾注都只进 transcript（SSE 上文仍是无 marker 的
    # output），所以工具回执文案不受影响；也因此产物尾注落在 ToolResult 截断之后，不会被
    # 截掉。自报即事实，不按 success 二次裁决——写盘工具失败时本就不自报，而脚本非零退出前
    # 已 copy-out 的产物确实躺在盘上（漏账才是事故）；被拒 / 未执行的调用没有结果可自报。
    msg_content = with_file_products_marker(output, result.file_products)
    if not result.success:
        msg_content = with_tool_failed_marker(msg_content or "")
    message = LLMMessage(
        role="tool",
        content=msg_content,
        tool_call_id=tc.id,
        audience=(
            TOOL_AUDIENCE_CEO if getattr(result, "audience", None) == TOOL_AUDIENCE_CEO else None
        ),
    )
    policy_failure = bool(result.metadata.get("policy_failure"))
    # 参数契约拒绝 (tools/protocol.py): forward the tool's self-correctable-rejection
    # marker so the run-scoped circuit breaker skips it (loop_controller.record).
    contract_failure = bool(result.contract_failure)
    error_summary = ""
    if not result.success and not policy_failure:
        error_summary = output if isinstance(output, str) else ""
    result_meta = dict(result.metadata) if result.metadata else {}
    if (
        not result.success
        and result_meta.get("workspace_channel_dead")
        and getattr(context, "execution_id", None)
    ):
        # So loop_controller can stamp the coordination session (workers often
        # lack current_execution_id ContextVar).
        result_meta.setdefault("execution_id", context.execution_id)
    if not result.success and "error_class" not in result_meta:
        if result_meta.get("retire_tools") or result_meta.get("liveness_timeout"):
            result_meta["error_class"] = ERROR_CLASS_PERMANENT
        elif policy_failure:
            result_meta["error_class"] = ERROR_CLASS_PERMISSION
        elif contract_failure:
            result_meta["error_class"] = ERROR_CLASS_VALIDATION
    return (
        message,
        (result if result.is_terminal else None),
        ToolAttempt(
            fingerprint,
            name,
            success=result.success,
            policy_failure=policy_failure,
            contract_failure=contract_failure,
            error_summary=error_summary,
            meta=_attempt_meta_with_landing_path(
                name,
                args,
                result_meta or None,
                error=error_summary,
                contract_failure=contract_failure,
            ),
        ),
        citations,
    )
