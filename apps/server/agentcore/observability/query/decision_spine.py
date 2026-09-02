"""Decision spine — compact decision-level evidence for one ``trace_id``.

Product surface for product-AI logs (飞行记录仪证据面). Distinct from
``turn_spine`` (``load_conversation_spine_events`` / ``spine_events`` = session
``chat.turn_start|complete`` directory).

Summary truth = persisted ``turn_metrics`` (or export ``turn_metrics.jsonl``).
Cost truth = ``cost_events`` by ``trace_id`` (turn_metrics has no cost columns).
``total_nano`` is billed (quota SUM); ``estimated_nano`` is BYOK display-only.
Drift L1 = JSONL-internal ``collab_drift``; Drift L2 = turn_metrics ⋈ JSONL
close/recompute — never silently pick one side.

Token 口径（复盘必读）：
- ``llm.input_tokens`` / ``llm.output_tokens`` = 本 trace JSONL 全部 ``llm.call`` 合计
  （含 pause 前段 / team_preview 前）。
- ``tail`` / ``turn_metrics`` tokens = 收口折账；``kind=resume`` 时通常只有 resume
  段 usage（不含 pause 前 captain），与 ``llm`` 合计可能不一致——属两口径，非漂移必修。

执行面（保持可扫）：``execution`` 是折叠摘要——工具按名聚合、run 只取
``obs.turn_spans`` 的 ``invoke_agent`` 行（失败优先、有上限）、prepare 分段一行。
成功工具 / 逐条 ``llm.call`` 不进 ``decisions``；失败 ``llm.call_failed``、
``engine.llm_round_exception`` / ``engine.llm_failed_terminal`` 与失败
``tool.execute_end``（:func:`~agentcore.observability.query.tool_end.is_tool_failure`，
含 ``allowlist_deny`` 等未执行；``redirect`` 改道上脊但不计 Exec err）一样上脊。
"""

from __future__ import annotations

from typing import Any

from agentcore.llm.resilience import summarize_degradation
from agentcore.observability.query.stats import (
    COLLAB_FIELD_MAP,
    accumulate_trace,
    collab_drift,
    new_trace,
)
from agentcore.observability.query.tool_end import is_tool_failure, tool_end_on_spine

SCHEMA_VERSION = "decision_spine.v0"

# Key decision / failure events that belong on the spine (not the firehose).
_ACTIVE_DECISION_EVENTS = frozenset(
    {
        "delegate.started",
        "delegate.completed",
        "delegate.yielded",
        "delegate.acceptance_resolved",
        "delegate.continuation_ok",
        "delegate.continuation_rejected",
        "delegate.post_close_gap_fill_rejected",
        "delegate.post_close_redelegation_rejected",
        "delegate.run_redirect_hot",
        "worker.escalate",
        "worker.handoff",
        "engine.ceiling_finalize",
        "engine.loop_finalize",
        "run.failed",
        "run.captain_failed",
        "pipeline.error",
        "approval.timeout",
        # Local solo cancel has no coordination.user_stop_* — these are the fingerprint.
        "sidecar.turn_cancel_requested",
        "sidecar.turn_cancelled",
        # Execution-layer failures (rare; same "obvious failure only" bar as tools).
        "llm.call_failed",
        "llm.rate_limit_no_retry",
        "engine.llm_round_exception",
        "engine.llm_failed_terminal",
        "chat.prepare_local_io_abort",
    }
)

# Folded execution summary caps — if these grow, the spine stops being scannable.
_MAX_RUN_ROWS = 8
_MAX_TOOL_GROUPS = 12
_MAX_TOOL_LINE_GROUPS = 6

# S3-retired: production no longer emits; still surface when reading pre-S3 JSONL.
# Do not treat as current contract — see docs「委派验收事件（S3 后）」.
_HISTORICAL_DECISION_EVENTS = frozenset(
    {
        "delegate.completion_criteria_unmet",
    }
)

_DECISION_EVENTS = _ACTIVE_DECISION_EVENTS | _HISTORICAL_DECISION_EVENTS

# Local sidecar write-back: one ``chat.local_turn_recorded`` is both head+close
# anchor (do NOT also emit ``chat.turn_start/complete`` — avoids double-write).
_CLOSE_EVENTS = frozenset(
    {"chat.turn_complete", "chat.resume_complete", "chat.local_turn_recorded"}
)
_START_EVENTS = frozenset({"chat.turn_start", "chat.local_turn_recorded"})
_PRIMARY_START_EVENT = "chat.turn_start"

# Tail fields whose truth is turn_metrics when a row is present.
_TAIL_METRIC_FIELDS = (
    "status",
    "finish_reason",
    "error",
    "rounds",
    "duration_ms",
    "delegated",
    "workers",
    "input_tokens",
    "output_tokens",
    "boundary_yields",
    "scope_signals",
    "revises",
    "escalations",
)

# L2 compare set: stored turn_metrics ⋈ JSONL close (and collab recompute).
_L2_COMPARE_FIELDS = (
    "finish_reason",
    "delegated",
    "workers",
    "rounds",
    "input_tokens",
    "output_tokens",
    "boundary_yields",
    "scope_signals",
    "revises",
    "escalations",
)

_PHASE0_FIELDS = (
    "prepare_ms",
    "assemble_ms",
    "ttft_reasoning_ms",
    "ttft_content_ms",
)

_DECISION_KEEP_KEYS = (
    "agents",
    "nodes",
    "parallel",
    "call",
    "reason",
    "criteria",
    "gaps",
    "escalate",
    "execution_id",
    "source",
    "streak",
    "run_id",
    "cancelled_run_id",
    "continuation_run_id",
    "kind",
    "question",
    "blocking",
    "assumption",
    "error",
    "error_code",
    "origin",
    "classified",
    "scope",
    "scope_ratio",
    "escalations",
    "chars",
    "body_chars",
    "tool",
    "status",
    "rounds",
    "thrashing",
    "token_budget",
    "tokens",
    "count",
    "codes",
    "tools",
    "model",
    "scenario",
    "latency_ms",
    "error_type",
    "credential_source",
    "provider_id",
    "provider",
    "phase",
    "ms",
    "detail",
    "attempt",
    "cooldown_sec",
    "cooldown_source",
    "ceiling_sec",
)


def _pick(obj: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: obj[k] for k in keys if k in obj and obj[k] is not None}


def _close_event(log_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    close: dict[str, Any] | None = None
    local_close: dict[str, Any] | None = None
    for ev in log_events:
        event = ev.get("event")
        if event not in _CLOSE_EVENTS:
            continue
        # Prefer real cloud/resume close; local_turn_recorded is fallback only.
        if event == "chat.local_turn_recorded":
            local_close = ev
            continue
        # Terminal close wins over paused mid-snapshot (same rule as collab_drift).
        if ev.get("finish_reason") == "paused":
            if close is None:
                close = ev
            continue
        close = ev
    return close if close is not None else local_close


def _start_event(log_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    local: dict[str, Any] | None = None
    for ev in log_events:
        event = ev.get("event")
        if event not in _START_EVENTS:
            continue
        if event == _PRIMARY_START_EVENT:
            return ev
        if local is None:
            local = ev
    return local


def _project_decision(ev: dict[str, Any]) -> dict[str, Any]:
    event = str(ev.get("event") or "")
    row: dict[str, Any] = {
        "timestamp": ev.get("timestamp", ""),
        "event": event,
    }
    if ev.get("level") in ("error", "warning"):
        row["level"] = ev["level"]
    detail = _pick(ev, _DECISION_KEEP_KEYS)
    if event == "delegate.started":
        plan = ev.get("plan")
        waves = ev.get("waves")
        if isinstance(plan, list):
            detail["roles"] = [
                n.get("role") or n.get("id") for n in plan if isinstance(n, dict)
            ]
        if waves is not None:
            detail["wave_count"] = len(waves) if isinstance(waves, list) else waves
    if event == "tool.execute_end":
        detail["tool"] = ev.get("tool")
        detail["status"] = ev.get("status")
        if ev.get("reason"):
            detail["reason"] = ev.get("reason")
    row["detail"] = detail
    return row


def _iter_decisions(log_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in log_events:
        event = str(ev.get("event") or "")
        if event in _DECISION_EVENTS:
            out.append(_project_decision(ev))
            continue
        # Failures + channel steers (success noise stays off the spine).
        # Same predicate as patrol: allowlist_deny etc. are failures; redirect is steer.
        if tool_end_on_spine(ev):
            out.append(_project_decision(ev))
            continue
        # Local write-back: aggregate tool failure codes (no fake tool.execute_end).
        if event == "chat.local_turn_tool_failures":
            out.append(_project_decision(ev))
    return out


def _int_field(ev: dict[str, Any], key: str) -> int:
    try:
        return int(ev.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _scenario_slot(scenarios: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    slot = scenarios.get(name)
    if slot is None:
        slot = {
            "scenario": name,
            "calls": 0,
            "failed": 0,
            "latency_ms_max": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        scenarios[name] = slot
    return slot


def _runs_from_obs(log_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fold ``obs.turn_spans`` to invoke_agent rows only (not the tool firehose)."""
    for ev in reversed(log_events):
        if ev.get("event") != "obs.turn_spans":
            continue
        items: list[dict[str, Any]] = []
        for span in ev.get("spans") or []:
            if not isinstance(span, dict) or span.get("operation") != "invoke_agent":
                continue
            raw_attrs = span.get("attributes")
            attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
            items.append(
                {
                    "name": span.get("name"),
                    "status": span.get("status"),
                    "duration_ms": span.get("duration_ms"),
                    "run_id": attrs.get("agentcore.run.id"),
                    "kind": attrs.get("agentcore.run.kind"),
                    "role": attrs.get("agentcore.run.role"),
                }
            )
        items.sort(
            key=lambda row: (
                0 if row.get("status") == "error" else 1,
                -(row.get("duration_ms") or 0),
            )
        )
        shown = items[:_MAX_RUN_ROWS]
        hidden = len(items) - len(shown)
        block: dict[str, Any] = {
            "source": "obs.turn_spans",
            "span_count": ev.get("span_count"),
            "truncated": bool(ev.get("truncated")),
            "dropped": int(ev.get("dropped") or 0),
            "items": shown,
        }
        if hidden:
            block["hidden_runs"] = hidden
        return block
    return None


def _execution_summary(log_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact execution map: grouped tools + run skeleton + prepare phases."""
    tools: dict[str, dict[str, Any]] = {}
    starts = 0
    ends = 0
    for ev in log_events:
        event = ev.get("event")
        if event == "tool.execute_start":
            starts += 1
            continue
        if event != "tool.execute_end":
            continue
        ends += 1
        name = str(ev.get("tool") or "?")
        slot = tools.setdefault(
            name, {"tool": name, "ok": 0, "error": 0, "ms_sum": 0, "ms_max": 0}
        )
        ms = _int_field(ev, "duration_ms")
        slot["ms_sum"] += ms
        if ms > slot["ms_max"]:
            slot["ms_max"] = ms
        if is_tool_failure(ev):
            slot["error"] += 1
        else:
            slot["ok"] += 1

    by_tool = sorted(
        tools.values(),
        key=lambda row: (-row["error"], -row["ms_sum"], row["tool"]),
    )
    other: dict[str, Any] | None = None
    if len(by_tool) > _MAX_TOOL_GROUPS:
        rest = by_tool[_MAX_TOOL_GROUPS:]
        by_tool = by_tool[:_MAX_TOOL_GROUPS]
        other = {
            "tools": len(rest),
            "ok": sum(row["ok"] for row in rest),
            "error": sum(row["error"] for row in rest),
            "ms_sum": sum(row["ms_sum"] for row in rest),
        }

    ok = sum(row["ok"] for row in tools.values())
    error = sum(row["error"] for row in tools.values())
    in_flight = max(0, starts - ends)

    prepare_phases: list[dict[str, Any]] = []
    for ev in log_events:
        if ev.get("event") != "chat.prepare_phase":
            continue
        phase = ev.get("phase")
        if not phase:
            continue
        prepare_phases.append({"phase": str(phase), "ms": _int_field(ev, "ms")})

    execution: dict[str, Any] = {}
    runs = _runs_from_obs(log_events)
    if runs is not None:
        execution["runs"] = runs
    if tools or in_flight:
        tool_block: dict[str, Any] = {
            "calls": ok + error,
            "ok": ok,
            "error": error,
            "in_flight": in_flight,
            "by_tool": by_tool,
        }
        if other is not None:
            tool_block["other"] = other
        execution["tools"] = tool_block
    if prepare_phases:
        execution["prepare"] = {
            "phases": prepare_phases,
            "ms_sum": sum(row["ms"] for row in prepare_phases),
        }
    return execution


def _llm_summary(log_events: list[dict[str, Any]]) -> dict[str, Any]:
    calls = 0
    failed = 0
    models: dict[str, int] = {}
    input_tokens = 0
    output_tokens = 0
    cost_nano = 0
    scenarios: dict[str, dict[str, Any]] = {}
    slowest: dict[str, Any] | None = None
    for ev in log_events:
        event = ev.get("event")
        if event == "llm.call":
            calls += 1
            model = ev.get("model")
            if model:
                models[str(model)] = models.get(str(model), 0) + 1
            inp = _int_field(ev, "input_tokens")
            out_tok = _int_field(ev, "output_tokens")
            input_tokens += inp
            output_tokens += out_tok
            cost_nano += _int_field(ev, "cost_nano")
            scenario = str(ev.get("scenario") or "?")
            slot = _scenario_slot(scenarios, scenario)
            slot["calls"] += 1
            slot["input_tokens"] += inp
            slot["output_tokens"] += out_tok
            latency = _int_field(ev, "latency_ms")
            if latency > slot["latency_ms_max"]:
                slot["latency_ms_max"] = latency
            if latency > 0 and (
                slowest is None or latency > int(slowest.get("latency_ms") or 0)
            ):
                slowest = {
                    "timestamp": ev.get("timestamp", ""),
                    "model": ev.get("model"),
                    "scenario": scenario,
                    "latency_ms": latency,
                    "input_tokens": inp,
                    "output_tokens": out_tok,
                }
        elif event == "llm.call_failed":
            failed += 1
            model = ev.get("model")
            if model:
                models[str(model)] = models.get(str(model), 0) + 1
            _scenario_slot(scenarios, str(ev.get("scenario") or "?"))["failed"] += 1
    out: dict[str, Any] = {
        "calls": calls,
        "failed": failed,
        "models": [{"model": m, "calls": n} for m, n in sorted(models.items())],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_nano": cost_nano,
        # 与 turn_metrics/tail 区分：此处为全 trace ``llm.call`` 合计（含 pause 前）。
        "token_scope": "full_trace",
    }
    if scenarios:
        out["by_scenario"] = sorted(
            scenarios.values(),
            key=lambda row: (-row["failed"], -row["calls"], row["scenario"]),
        )
    if slowest is not None and calls >= 2:
        out["slowest"] = slowest
    return out


def _jsonl_collab_recompute(log_events: list[dict[str, Any]]) -> dict[str, Any]:
    rec = new_trace()
    for ev in log_events:
        event = str(ev.get("event") or "")
        if not event:
            continue
        accumulate_trace(rec, event, ev)
    return rec


def _tail_from_close(close: dict[str, Any] | None) -> dict[str, Any]:
    if close is None:
        return {"source": "none"}
    tail: dict[str, Any] = {
        "source": "jsonl_close",
        "event": close.get("event"),
        "finish_reason": close.get("finish_reason"),
        "delegated": bool(close.get("delegated")) if "delegated" in close else None,
        "workers": close.get("workers"),
        "rounds": close.get("rounds"),
        "duration_ms": close.get("duration_ms"),
        "input_tokens": close.get("input_tokens"),
        "output_tokens": close.get("output_tokens"),
        "reply_preview": close.get("reply_preview"),
        # jsonl close：turn_complete 常带 tokens；resume_complete 通常不带 → 勿当全 trace。
        "token_scope": (
            "settlement_segment"
            if close.get("event") == "chat.resume_complete"
            else "settlement"
        ),
    }
    for col in COLLAB_FIELD_MAP:
        if col in close:
            tail[col] = int(close.get(col) or 0)
    for f in _PHASE0_FIELDS:
        if f in close:
            tail[f] = close.get(f)
    return {k: v for k, v in tail.items() if v is not None or k == "source"}


def _tail_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    tail: dict[str, Any] = {"source": "turn_metrics"}
    for f in _TAIL_METRIC_FIELDS:
        if f in metrics:
            tail[f] = metrics[f]
    if "kind" in metrics:
        tail["kind"] = metrics["kind"]
    if "mode" in metrics:
        tail["mode"] = metrics["mode"]
    if "turn_id" in metrics:
        tail["turn_id"] = metrics["turn_id"]
    # resume 收口折账 ≠ 全 trace llm 合计（pause 前段不在本段 usage）。
    if metrics.get("kind") == "resume":
        tail["token_scope"] = "settlement_segment"
    else:
        tail["token_scope"] = "settlement"
    return tail


def _normalize_for_compare(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "delegated":
        return bool(value)
    if field in (
        "workers",
        "rounds",
        "input_tokens",
        "output_tokens",
        "boundary_yields",
        "scope_signals",
        "revises",
        "escalations",
        "duration_ms",
    ):
        return int(value)
    if field == "finish_reason":
        return str(value)
    return value


def compute_drift_l2(
    *,
    turn_metrics: dict[str, Any] | None,
    close: dict[str, Any] | None,
    recomputed: dict[str, Any],
) -> dict[str, Any]:
    """Compare persisted turn_metrics against JSONL close + event recompute.

    Returns a structured L2 report. ``ok`` is True when metrics exist and every
    comparable field agrees (or the JSONL side has nothing to compare yet).
    """
    if turn_metrics is None:
        return {
            "ok": True,
            "compared": False,
            "reason": "turn_metrics_missing",
            "mismatches": [],
        }

    mismatches: list[dict[str, Any]] = []
    # Prefer terminal JSONL close values; fall back to event recompute for collab.
    close_usable = close is not None and close.get("finish_reason") != "paused"
    close_vals: dict[str, Any] = {}
    if close_usable and close is not None:
        for f in _L2_COMPARE_FIELDS:
            if f in close:
                close_vals[f] = close.get(f)

    recompute_collab = {
        col: int(recomputed.get(trace_field, 0) or 0)
        for col, trace_field in COLLAB_FIELD_MAP.items()
    }

    for field in _L2_COMPARE_FIELDS:
        if field not in turn_metrics:
            continue
        stored = _normalize_for_compare(field, turn_metrics.get(field))
        jsonl_side: Any = None
        jsonl_source: str | None = None
        if field in close_vals:
            jsonl_side = _normalize_for_compare(field, close_vals[field])
            jsonl_source = "jsonl_close"
        elif field in recompute_collab:
            jsonl_side = recompute_collab[field]
            jsonl_source = "jsonl_recompute"
        else:
            continue
        if stored != jsonl_side:
            mismatches.append(
                {
                    "field": field,
                    "turn_metrics": stored,
                    "jsonl": jsonl_side,
                    "jsonl_source": jsonl_source,
                }
            )

    return {
        "ok": len(mismatches) == 0,
        "compared": True,
        "reason": None if mismatches else "aligned",
        "mismatches": mismatches,
    }


def _cost_block(
    cost_events: dict[str, Any] | None,
    log_events: list[dict[str, Any]],
) -> dict[str, Any]:
    if cost_events is not None:
        return {
            "source": "cost_events",
            "total_nano": cost_events.get("total_nano"),
            "estimated_nano": cost_events.get("estimated_nano"),
            "currency": cost_events.get("currency"),
            "estimated_currency": cost_events.get("estimated_currency"),
            "billing": cost_events.get("billing"),
            "runs": cost_events.get("runs"),
            "models": cost_events.get("models"),
        }
    for ev in reversed(log_events):
        if ev.get("event") == "cost.recorded":
            return {
                "source": "jsonl_cost.recorded",
                "total_nano": ev.get("total_nano"),
                "total_usd": ev.get("total_usd"),
                "runs": ev.get("runs"),
                "models": ev.get("models"),
                "by_role": ev.get("by_role"),
            }
    return {"source": "none"}


def _drift_l1_for_trace(log_events: list[dict[str, Any]]) -> dict[str, Any]:
    rec = _jsonl_collab_recompute(log_events)
    # collab_drift expects a traces map keyed by trace_id; use a placeholder key.
    tid = "local"
    for ev in log_events:
        if ev.get("trace_id"):
            tid = str(ev["trace_id"])
            break
    return collab_drift({tid: rec})


def build_decision_spine(
    log_events: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    turn_metrics: dict[str, Any] | None = None,
    cost_events: dict[str, Any] | None = None,
    traffic: str | None = None,
    jsonl_gap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the v0 decision_spine dict (human formatter and ``--json`` share this)."""
    resolved_trace = trace_id
    resolved_cid = conversation_id
    for ev in log_events:
        if not resolved_trace and ev.get("trace_id"):
            resolved_trace = str(ev["trace_id"])
        if not resolved_cid and ev.get("conversation_id"):
            resolved_cid = str(ev["conversation_id"])
        if resolved_trace and resolved_cid:
            break

    start = _start_event(log_events)
    close = _close_event(log_events)
    recomputed = _jsonl_collab_recompute(log_events)

    head: dict[str, Any] = {"source": "none"}
    if start is not None:
        head = {
            "source": str(start.get("event") or _PRIMARY_START_EVENT),
            "timestamp": start.get("timestamp", ""),
            "preview": start.get("preview", ""),
            **_pick(
                start,
                (
                    "chars",
                    "history",
                    "location",
                    "via",
                    "stream_path_reason",
                    "rounds",
                    "message_id",
                ),
            ),
        }

    if turn_metrics is not None:
        tail = _tail_from_metrics(turn_metrics)
        # Phase-0 only lives on JSONL close — attach as evidence, not summary truth.
        if close is not None:
            phase0 = _pick(close, _PHASE0_FIELDS)
            if phase0:
                tail["phase0"] = phase0
            if close.get("reply_preview") and "reply_preview" not in tail:
                tail["reply_preview"] = close.get("reply_preview")
    else:
        tail = _tail_from_close(close)

    drift_l2 = compute_drift_l2(
        turn_metrics=turn_metrics,
        close=close,
        recomputed=recomputed,
    )

    # Paused close is a mid-turn snapshot — still incomplete for the reader.
    incomplete = start is not None and (
        close is None or close.get("finish_reason") == "paused"
    )

    health: dict[str, Any] = {
        "traffic": traffic,
        "incomplete": incomplete,
        "jsonl_gap": jsonl_gap,
        "drift_l1": _drift_l1_for_trace(log_events),
        "drift_l2": drift_l2,
        "turn_metrics_joined": turn_metrics is not None,
        "cost_joined": cost_events is not None,
        # 复盘用：两口径说明（非 L2 漂移字段）。
        "token_accounting": {
            "llm": "full_trace_llm_call_sum",
            "tail": "turn_metrics_or_jsonl_close_settlement",
            "resume_note": (
                "kind=resume / chat.resume_complete：tail·metrics 为收口折账"
                "（通常不含 pause 前 / team_preview 前段）；"
                "llm 为同 trace 全部 llm.call 合计"
            ),
        },
    }

    spine: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": resolved_trace,
        "conversation_id": resolved_cid,
        "head": head,
        "decisions": _iter_decisions(log_events),
        "llm": _llm_summary(log_events),
        "tail": tail,
        "cost": _cost_block(cost_events, log_events),
        "health": health,
    }
    execution = _execution_summary(log_events)
    if execution:
        spine["execution"] = execution
    degradation = summarize_degradation(log_events)
    if degradation is not None:
        spine["degradation"] = degradation
    replay_persist = _replay_persist_block(log_events)
    if replay_persist is not None:
        spine["replay_persist"] = replay_persist
    return spine


_REPLAY_UNSAVED = "这一轮的回放没存上"


def _replay_persist_block(log_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Optional spine line when journal persist failed (reply still reached the user)."""
    if any(ev.get("event") == "journal.persist_failed" for ev in log_events):
        return {"saved": False, "summary": _REPLAY_UNSAVED}
    return None


def _format_ms(ms: int) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _format_execution(execution: dict[str, Any]) -> list[str]:
    """Human Exec block — grouped tools + capped run rows + one Prep line."""
    if not execution:
        return []
    tools = execution.get("tools") or {}
    runs = execution.get("runs") or {}
    prepare = execution.get("prepare") or {}
    lines: list[str] = []

    bits: list[str] = []
    if tools:
        bits.append(
            f"tools={tools.get('calls', 0)} ok={tools.get('ok', 0)} err={tools.get('error', 0)}"
        )
        if tools.get("in_flight"):
            bits.append(f"in_flight={tools['in_flight']}")
        groups: list[str] = []
        for row in (tools.get("by_tool") or [])[:_MAX_TOOL_LINE_GROUPS]:
            err = int(row.get("error") or 0)
            ok_n = int(row.get("ok") or 0)
            ms_s = _format_ms(int(row.get("ms_sum") or 0))
            name = row.get("tool") or "?"
            if err:
                groups.append(f"{name} {ok_n}ok/{err}err {ms_s}")
            else:
                groups.append(f"{name} {ok_n}ok {ms_s}")
        other = tools.get("other")
        leftover = max(0, len(tools.get("by_tool") or []) - _MAX_TOOL_LINE_GROUPS)
        extra_groups = leftover + int((other or {}).get("tools") or 0)
        if extra_groups:
            groups.append(f"+{extra_groups} tools")
        if groups:
            bits.append("[" + " · ".join(groups) + "]")
    elif runs:
        bits.append(f"runs source={runs.get('source')}")
    if bits:
        lines.append("  Exec  " + "  ".join(bits))
    elif prepare:
        lines.append("  Exec")

    for row in runs.get("items") or []:
        status = str(row.get("status") or "?")
        icon = "[E]" if status == "error" else "   "
        dur = f"  {row['duration_ms']}ms" if row.get("duration_ms") is not None else ""
        extra = row.get("kind") or row.get("role") or ""
        extra_s = f"  {extra}" if extra else ""
        label = row.get("name") or row.get("run_id") or "?"
        lines.append(f"        {icon} {label}  {status}{dur}{extra_s}")
    hidden_bits: list[str] = []
    if runs.get("hidden_runs"):
        hidden_bits.append(f"+{runs['hidden_runs']} runs")
    if runs.get("truncated"):
        hidden_bits.append(f"spans truncated dropped={runs.get('dropped')}")
    if hidden_bits:
        lines.append("        " + "  ".join(hidden_bits))

    if prepare:
        phase_s = " ".join(
            f"{p['phase']}={p['ms']}ms" for p in (prepare.get("phases") or [])
        )
        if len(phase_s) > 120:
            phase_s = phase_s[:117] + "..."
        lines.append(f"        Prep  {phase_s}  sum={prepare.get('ms_sum')}ms")
    return lines


def _format_cost_line(cost: dict[str, Any]) -> str:
    """Keep billed ``total_nano``; surface BYOK estimate so ops do not read 0 as free."""
    bits = [
        f"source={cost.get('source')}",
        f"total_nano={cost.get('total_nano')}",
    ]
    estimated = cost.get("estimated_nano")
    billing = cost.get("billing")
    if estimated not in (None, 0) or billing in ("BYOK", "mixed"):
        bits.append(f"estimated_nano={0 if estimated is None else estimated}")
        if billing:
            bits.append(f"billing={billing}")
        est_cur = cost.get("estimated_currency")
        if est_cur:
            bits.append(f"estimated_currency={est_cur}")
    if cost.get("runs") is not None:
        bits.append(f"runs={cost.get('runs')}")
    return "  Cost  " + "  ".join(bits)


def format_decision_spine(spine: dict[str, Any]) -> str:
    """Human-readable rendering isomorphic to the ``decision_spine`` JSON."""
    lines: list[str] = [
        "=" * 70,
        f"  Decision Spine  ({spine.get('schema_version', SCHEMA_VERSION)})",
        f"  Trace: {spine.get('trace_id') or '?'}",
    ]
    cid = spine.get("conversation_id")
    if cid:
        lines.append(f"  Conversation: {cid}")

    health = spine.get("health") or {}
    if health.get("traffic"):
        lines.append(f"  Traffic: {health['traffic']} (合成流量)")
    if health.get("incomplete"):
        lines.append("  Status: ⚠️ 未完成（进行中或仅 kickoff）")
    gap = health.get("jsonl_gap")
    if gap:
        reason = gap.get("reason", "gap")
        if reason == "timestamp_gap":
            secs = int(gap.get("gap_seconds") or 0)
            lines.append(
                f"  ⚠ jsonl 时间线疑似断档（gap≈{secs}s）；以 Postgres journal 为准"
            )
        else:
            lines.append("  ⚠ jsonl 时间线疑似断档；以 Postgres journal 为准")

    drift_l2 = health.get("drift_l2") or {}
    if drift_l2.get("compared") and not drift_l2.get("ok"):
        n = len(drift_l2.get("mismatches") or [])
        lines.append(f"  ⚠ Drift L2: turn_metrics ⋈ JSONL 不一致（{n} 字段）")
    elif drift_l2.get("compared") and drift_l2.get("ok"):
        lines.append("  Drift L2: aligned")
    elif drift_l2.get("reason") == "turn_metrics_missing":
        lines.append("  Drift L2: (no turn_metrics to join)")

    lines.append("=" * 70)

    head = spine.get("head") or {}
    preview = (head.get("preview") or "").replace("\n", " ").strip() or "(no preview)"
    if len(preview) > 80:
        preview = preview[:77] + "..."
    ts = str(head.get("timestamp") or "")[:19]
    lines.append(f"  Head  {ts}  \"{preview}\"")
    extras = []
    if head.get("via") is not None:
        extras.append(f"via={head['via']}")
    if head.get("stream_path_reason"):
        extras.append(f"path_reason={head['stream_path_reason']}")
    if head.get("location") is not None:
        extras.append(f"location={head['location']}")
    if head.get("history") is not None:
        extras.append(f"history={head['history']}")
    if head.get("chars") is not None:
        extras.append(f"chars={head['chars']}")
    if extras:
        lines.append(f"         {' · '.join(extras)}")

    decisions = spine.get("decisions") or []
    lines.append(f"  Decisions ({len(decisions)})")
    if not decisions:
        lines.append("    (none)")
    for d in decisions:
        dts = str(d.get("timestamp") or "")[:19]
        detail = d.get("detail") or {}
        bits = [f"{k}={v}" for k, v in detail.items()]
        detail_s = " ".join(bits)
        if len(detail_s) > 100:
            detail_s = detail_s[:100] + "..."
        icon = {"error": "[E]", "warning": "[W]"}.get(d.get("level", ""), "   ")
        ev = d.get("event")
        hist = " (historical/S3)" if ev in _HISTORICAL_DECISION_EVENTS else ""
        lines.append(f"    {dts}  {icon} {ev}{hist}  {detail_s}".rstrip())

    lines.extend(_format_execution(spine.get("execution") or {}))

    llm = spine.get("llm") or {}
    model_bits = ", ".join(
        f"{m['model']}×{m['calls']}" for m in (llm.get("models") or [])
    )
    llm_scope = llm.get("token_scope") or "full_trace"
    lines.append(
        f"  LLM  calls={llm.get('calls', 0)} failed={llm.get('failed', 0)}"
        + (f"  [{model_bits}]" if model_bits else "")
        + f"  tok_in={llm.get('input_tokens', 0)} tok_out={llm.get('output_tokens', 0)}"
        + f"  (scope={llm_scope})"
    )
    by_sc = llm.get("by_scenario") or []
    if len(by_sc) > 1:
        sc_bits = []
        for row in by_sc:
            bit = f"{row.get('scenario')}×{row.get('calls', 0)}"
            if row.get("failed"):
                bit += f"/{row['failed']}fail"
            sc_bits.append(bit)
        lines.append("         scenario  " + "  ".join(sc_bits))
    slowest = llm.get("slowest")
    if slowest:
        lines.append(
            "         slowest  "
            f"{slowest.get('model') or '?'}  {slowest.get('scenario')}"
            f"  {slowest.get('latency_ms')}ms"
            f"  in={slowest.get('input_tokens')} out={slowest.get('output_tokens')}"
        )

    degradation = spine.get("degradation") or {}
    summary = degradation.get("summary")
    if summary:
        lines.append(f"  Degraded  {summary}")

    replay_persist = spine.get("replay_persist") or {}
    if replay_persist.get("saved") is False:
        lines.append(
            f"  Replay  {replay_persist.get('summary') or _REPLAY_UNSAVED}"
        )

    tail = spine.get("tail") or {}
    tail_bits = [
        f"source={tail.get('source')}",
        f"finish={tail.get('finish_reason')}",
        f"status={tail.get('status')}" if "status" in tail else None,
        f"kind={tail.get('kind')}" if "kind" in tail else None,
        f"mode={tail.get('mode')}" if tail.get("mode") else None,
        f"delegated={tail.get('delegated')}" if "delegated" in tail else None,
        f"workers={tail.get('workers')}" if "workers" in tail else None,
        f"rounds={tail.get('rounds')}" if "rounds" in tail else None,
        f"dur_ms={tail.get('duration_ms')}" if "duration_ms" in tail else None,
        (
            f"tok_in={tail.get('input_tokens')} tok_out={tail.get('output_tokens')}"
            if "input_tokens" in tail or "output_tokens" in tail
            else None
        ),
        f"token_scope={tail.get('token_scope')}" if tail.get("token_scope") else None,
    ]
    collab = []
    for col in COLLAB_FIELD_MAP:
        if col in tail:
            collab.append(f"{col}={tail[col]}")
    lines.append("  Tail  " + " · ".join(b for b in tail_bits if b))
    if collab:
        lines.append("         collab: " + " · ".join(collab))
    if tail.get("error"):
        lines.append(f"         error: {tail['error']}")

    # 两口径提示：llm 全 trace vs tail 收口折账（resume 常见差）。
    llm_in = int(llm.get("input_tokens") or 0)
    llm_out = int(llm.get("output_tokens") or 0)
    if "input_tokens" in tail or "output_tokens" in tail:
        tail_in = int(tail.get("input_tokens") or 0)
        tail_out = int(tail.get("output_tokens") or 0)
        if (llm_in, llm_out) != (tail_in, tail_out):
            resume_hint = ""
            if (
                tail.get("kind") == "resume"
                or tail.get("token_scope") == "settlement_segment"
            ):
                resume_hint = "；resume 折账通常不含 pause 前 / team_preview 前"
            lines.append(
                f"  Token口径: llm=全trace合计 in={llm_in}/out={llm_out}；"
                f"tail/metrics=收口折账 in={tail_in}/out={tail_out}"
                f"{resume_hint}"
            )
    elif health.get("token_accounting"):
        note = (health.get("token_accounting") or {}).get("resume_note")
        if note and (
            tail.get("kind") == "resume"
            or tail.get("event") == "chat.resume_complete"
            or tail.get("token_scope") == "settlement_segment"
        ):
            lines.append(f"  Token口径: {note}")

    cost = spine.get("cost") or {}
    if cost.get("source") != "none":
        lines.append(_format_cost_line(cost))
    else:
        lines.append("  Cost  (none)")

    if drift_l2.get("mismatches"):
        lines.append("  Drift L2 mismatches:")
        for m in drift_l2["mismatches"][:8]:
            lines.append(
                f"    - {m['field']}: turn_metrics={m['turn_metrics']} "
                f"jsonl={m['jsonl']} ({m.get('jsonl_source')})"
            )

    lines.append("")
    return "\n".join(lines)
