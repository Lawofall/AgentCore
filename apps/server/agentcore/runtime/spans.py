"""Execution span tree — project a turn's journal into an OTel-aligned trace (D2).

A multi-agent turn already records everything an execution trace needs into the
§8.3 Turn Journal (唯一事实源): the run tree (``run_started`` / ``run_completed`` /
``run_failed`` — parent links, per-run wall-clock ``duration_ms``, model, token
usage, cost), the per-round LLM facts (``round_boundary`` / ``llm_call``), and the
tool calls (``tool_call`` facts + the ``tool_use_start`` / ``tool_use_end`` display
pair that carry timestamps). So a span tree is just **one more projection** of the
same ordered facts — the idiom of :mod:`agentcore.runtime.journal`
(``runs_from_entries`` / ``window_from_journal`` / ``completed_from_journal``), not a
parallel instrumentation layer the engine has to feed.

This is the "每个 run 节点一个 span 的 trace 树" of D2 可观测性 (契约见 管理员后台.md): it makes a
multi-agent run debuggable (which worker stalled, how many rounds it burned, where
the tokens / seconds went) — exactly the 案例 1 failure shape (4/5 研究 worker 轮次
耗尽). Attribute keys follow the OpenTelemetry **GenAI semantic conventions**
(``gen_ai.operation.name`` / ``gen_ai.agent.*`` / ``gen_ai.usage.*`` /
``gen_ai.tool.name`` …), so the eventual OTLP exporter (跨进程 trace, 远期规划 D2)
is a thin attribute mapping over :class:`Span` — no rework of this projection.

Design (留缝, not a heavyweight dependency): the projection is pure stdlib; the only
seam to the outside is the :class:`SpanExporter` port. The default
:class:`LogSpanExporter` emits the tree as a structured ``obs.turn_spans`` log line
(greppable by ``trace_id`` via the existing structlog pipeline), so a span tree is
available NOW without pulling in the OTel SDK / a collector; a future OTLP exporter
swaps in via the same port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agentcore.core.logging import get_logger
from agentcore.runtime.events import EventType
from agentcore.runtime.facts import FactKind
from agentcore.runtime.journal import KIND_TURN_END
from agentcore.runtime.runs.types import RunKind

logger = get_logger(__name__)

# OTel ``gen_ai.system`` — derived from model id / optional base_url host (not a
# hardcoded vendor). Unknown OpenAI-compatible endpoints fall back to
# ``openai_compatible`` so exporters still get a stable non-empty value.
_DEFAULT_GEN_AI_SYSTEM = "openai_compatible"


def infer_gen_ai_system(
    model: str | None = None,
    *,
    base_url: str | None = None,
) -> str:
    """Map a model id and/or base_url to an OTel ``gen_ai.system`` value.

    Precedence: vendor prefix on ``provider/model`` → base_url host cues →
    model-name cues → ``openai_compatible``.
    """
    raw_model = (model or "").strip()
    if "/" in raw_model:
        prefix, _, rest = raw_model.partition("/")
        prefix_l = prefix.lower()
        if prefix_l in ("kimi", "moonshot"):
            return "moonshot"
        if prefix_l in ("zhipu", "glm"):
            return "zhipu"
        if prefix_l in ("doubao", "volcengine", "ark"):
            return "doubao"
        if prefix_l in ("openai", "openrouter"):
            return prefix_l
        if prefix_l == "deepseek":
            return "deepseek"
        # Fall through to rest for bare model cues.
        raw_model = rest or raw_model

    host = ""
    if base_url:
        try:
            from urllib.parse import urlparse

            host = (urlparse(base_url).hostname or "").lower()
        except Exception:  # noqa: BLE001 — never let URL parse break spans
            host = base_url.lower()

    haystack = f"{host} {raw_model.lower()}"
    model_l = raw_model.lower()
    if "deepseek" in haystack:
        return "deepseek"
    if "moonshot" in haystack or "kimi" in haystack:
        return "moonshot"
    if "bigmodel" in haystack or "zhipu" in haystack or model_l.startswith("glm"):
        return "zhipu"
    if "doubao" in haystack or "volces" in haystack or "volcengine" in haystack:
        return "doubao"
    if "openrouter" in haystack:
        return "openrouter"
    if "openai" in haystack or model_l.startswith("gpt-"):
        return "openai"
    return _DEFAULT_GEN_AI_SYSTEM


# A run-final ``message_final`` fact carries a serialized RunState (it has a ``phase``
# key); the captain's plain ``message_final`` (content/reasoning only) does not. Used
# only to skip the latter when counting — token/timing come from ``run_completed``.
_PHASE_KEY = "phase"

# Bound an attribute string before it enters a span / log line (an output_summary or a
# tool args preview can be long; a span tree is a skeleton, not a content store).
_ATTR_STR_CAP = 240


def _cap(value: str) -> str:
    return value[:_ATTR_STR_CAP] + "…" if len(value) > _ATTR_STR_CAP else value


def _parse_ts_ms(ts: str | None) -> float | None:
    """Epoch milliseconds for an ISO-8601 timestamp, or ``None`` if absent/unparseable.

    The journal's display events stamp ``time.strftime("%Y-%m-%dT%H:%M:%S.000Z")``
    (second precision) — coarse, but enough to time a tool span (a web_fetch timeout is
    5–15s, 案例 1). Run spans use the authoritative ``run_completed.duration_ms`` instead,
    so this only feeds best-effort tool-span durations.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp() * 1000.0


@dataclass
class Span:
    """One node in a turn's execution trace (OTel-GenAI-semconv-aligned).

    ``operation`` is the ``gen_ai.operation.name`` value (``chat`` for the turn root,
    ``invoke_agent`` for a run node, ``execute_tool`` for a tool call); ``name`` is the
    OTel span name convention (``"<operation> <target>"``). ``attributes`` holds the
    ``gen_ai.*`` (+ a few ``agentcore.*``) keys an OTLP exporter maps 1:1. ``status`` is
    the OTel span status (``ok`` / ``error`` / ``unset``). ``duration_ms`` is wall-clock
    when known (run nodes: authoritative; tool nodes: best-effort from event timestamps).
    """

    span_id: str
    parent_span_id: str | None
    name: str
    operation: str
    status: str = "unset"
    duration_ms: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[Span] = field(default_factory=list)

    def flatten(self) -> list[Span]:
        """This span followed by all descendants, depth-first (pre-order)."""
        out = [self]
        for child in self.children:
            out.extend(child.flatten())
        return out

    def to_log_dict(self) -> dict[str, Any]:
        """A compact, self-contained dict for the structured ``obs.turn_spans`` line."""
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "operation": self.operation,
            "status": self.status,
        }
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.attributes:
            d["attributes"] = self.attributes
        return d


# ── projection ──────────────────────────────────────────────────────────────────


@dataclass
class _RunAgg:
    """Per-run accumulator folded from the run's execution facts (token/round/tool)."""

    rounds: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


def _usage_tokens(usage: dict[str, Any]) -> tuple[int, int, int]:
    """``(input, output, reasoning)`` from a usage dict, tolerant of both key forms.

    The ``llm_call`` fact carries the long-key usage (``input_tokens`` …); a
    ``run_completed`` event carries the ledger short-key form (``input`` …). Read either.
    """

    def pick(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    return (
        pick("input_tokens", "input"),
        pick("output_tokens", "output"),
        pick("reasoning_tokens", "reasoning"),
    )


def spans_from_entries(entries: list[dict[str, Any]] | None) -> Span | None:
    """Project a turn's journal entries into an execution span tree (root or ``None``).

    Shape: a synthetic ROOT ``chat`` span (turn totals) → one ``invoke_agent`` span per
    run node (the captain + every delegated worker, linked by ``parent_run_id``) → one
    ``execute_tool`` span per tool call (linked to its run by the ``tool_call`` fact's
    ``run_id``). Pure inverse of the recorded facts — robust to both journal lineages:
    an execution-sourced journal (rich: round/llm/tool facts) and a legacy/display one
    (run_* + tool_use_* events only) both project, the latter simply without per-round
    aggregates. Returns ``None`` when there is nothing to trace (empty journal).
    """
    if not entries:
        return None

    started: dict[str, Any] | None = None
    finish_reason: str | None = None
    runs: dict[str, Span] = {}
    run_parent: dict[str, str | None] = {}
    aggs: dict[str, _RunAgg] = {}
    # tool_call facts give the authoritative run scoping + outcome; tool_use_start/end
    # give timestamps for a best-effort duration. Joined by tool_call_id below.
    tool_run: dict[str, dict[str, Any]] = {}  # tool_call_id → {run_id, name, success}
    tool_start_ms: dict[str, float] = {}
    tool_end_ms: dict[str, float] = {}
    tool_order: list[str] = []

    for entry in entries:
        kind = entry.get("kind") or ""
        payload = entry.get("payload") or {}
        ts = entry.get("ts")
        if kind == FactKind.TURN_STARTED.value and started is None:
            started = payload
        elif kind == KIND_TURN_END:
            finish_reason = payload.get("finish_reason")
        elif kind == EventType.RUN_STARTED.value:
            rid = payload.get("run_id") or ""
            if not rid:
                continue
            agent_id = payload.get("agent_id") or ""
            run_kind = payload.get("kind") or RunKind.AGENT.value
            revision = payload.get("continues_run_id")
            label = "captain" if run_kind == RunKind.CAPTAIN.value else (agent_id or rid[:8])
            attrs: dict[str, Any] = {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.system": infer_gen_ai_system(
                    (started or {}).get("model_profile") if started else None
                ),
                "gen_ai.agent.id": agent_id,
                "agentcore.run.id": rid,
                "agentcore.run.kind": run_kind,
            }
            if revision:
                attrs["agentcore.run.continues_run_id"] = revision
                label = f"{label} (cont)"
            runs[rid] = Span(
                span_id=f"run:{rid}",
                parent_span_id=None,  # linked after the pass (parent may appear later)
                name=f"invoke_agent {label}",
                operation="invoke_agent",
                attributes=attrs,
            )
            run_parent[rid] = payload.get("parent_run_id")
            aggs.setdefault(rid, _RunAgg())
        elif kind == EventType.RUN_COMPLETED.value:
            rid = payload.get("run_id") or ""
            span = runs.get(rid)
            if span is None:
                continue
            span.status = "ok"
            dur = payload.get("duration_ms")
            if isinstance(dur, (int, float)):
                span.duration_ms = int(dur)
            model = payload.get("model") or ""
            if model:
                span.attributes["gen_ai.request.model"] = model
                span.attributes["gen_ai.system"] = infer_gen_ai_system(model)
            role = payload.get("role") or ""
            if role:
                span.attributes["agentcore.run.role"] = role
            usage = payload.get("usage") or {}
            inp, out, rea = _usage_tokens(usage)
            if inp:
                span.attributes["gen_ai.usage.input_tokens"] = inp
            if out:
                span.attributes["gen_ai.usage.output_tokens"] = out
            cost = payload.get("cost") or {}
            total = cost.get("total")
            if isinstance(total, (int, float)) and total:
                span.attributes["agentcore.cost.total_nano"] = int(total)
        elif kind == EventType.RUN_FAILED.value:
            rid = payload.get("run_id") or ""
            span = runs.get(rid)
            if span is not None:
                span.status = "error"
                err = payload.get("error") or ""
                if err:
                    span.attributes["error.message"] = _cap(str(err))
        elif kind == FactKind.ROUND_BOUNDARY.value:
            rid = payload.get("run_id") or ""
            aggs.setdefault(rid, _RunAgg()).rounds += 1
        elif kind == FactKind.LLM_CALL.value:
            rid = payload.get("run_id") or ""
            agg = aggs.setdefault(rid, _RunAgg())
            agg.llm_calls += 1
            inp, out, rea = _usage_tokens(payload.get("usage") or {})
            agg.input_tokens += inp
            agg.output_tokens += out
            agg.reasoning_tokens += rea
        elif kind == FactKind.TOOL_CALL.value:
            tcid = payload.get("tool_call_id") or ""
            if not tcid:
                continue
            tool_run[tcid] = {
                "run_id": payload.get("run_id") or "",
                "name": payload.get("name") or "",
                "success": bool(payload.get("success", True)),
            }
            if tcid not in tool_order:
                tool_order.append(tcid)
            rid = payload.get("run_id") or ""
            agg = aggs.setdefault(rid, _RunAgg())
            agg.tool_calls += 1
            if not payload.get("success", True):
                agg.tool_failures += 1
        elif kind == EventType.TOOL_USE_START.value:
            tcid = payload.get("tool_call_id") or ""
            ms = _parse_ts_ms(ts)
            if tcid and ms is not None:
                tool_start_ms[tcid] = ms
        elif kind == EventType.TOOL_USE_END.value:
            tcid = payload.get("tool_call_id") or ""
            ms = _parse_ts_ms(ts)
            if tcid and ms is not None:
                tool_end_ms[tcid] = ms

    # Synthetic root: the turn. Carries turn-level totals (summed from the runs) so the
    # one line answers「这回合花了多少 / 谁参与 / 怎么收的尾」at a glance.
    root_model = (started or {}).get("model_profile") if started else None
    # Prefer a completed run's model when available (more specific than profile).
    for span in runs.values():
        m = span.attributes.get("gen_ai.request.model")
        if m:
            root_model = m
            break
    root = Span(
        span_id="turn",
        parent_span_id=None,
        name="chat",
        operation="chat",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": infer_gen_ai_system(root_model),
        },
    )
    if started is not None:
        profile = started.get("model_profile") or ""
        if profile:
            root.attributes["agentcore.model_profile"] = profile
    if finish_reason is not None:
        root.attributes["agentcore.finish_reason"] = finish_reason
        root.status = "error" if finish_reason in ("error", "cancelled") else "ok"

    # Fold each run's aggregates onto its span; sum the turn totals.
    total_in = total_out = total_rea = total_tools = 0
    for rid, span in runs.items():
        agg = aggs.get(rid)
        if agg is not None:
            if agg.rounds:
                span.attributes["agentcore.rounds"] = agg.rounds
            if agg.tool_calls:
                span.attributes["agentcore.tool_calls"] = agg.tool_calls
            if agg.tool_failures:
                span.attributes["agentcore.tool_failures"] = agg.tool_failures
            # Prefer the run_completed usage already set; fall back to summed llm_call.
            if "gen_ai.usage.input_tokens" not in span.attributes and agg.input_tokens:
                span.attributes["gen_ai.usage.input_tokens"] = agg.input_tokens
            if "gen_ai.usage.output_tokens" not in span.attributes and agg.output_tokens:
                span.attributes["gen_ai.usage.output_tokens"] = agg.output_tokens
            total_tools += agg.tool_calls
        total_in += int(span.attributes.get("gen_ai.usage.input_tokens", 0) or 0)
        total_out += int(span.attributes.get("gen_ai.usage.output_tokens", 0) or 0)
        total_rea += agg.reasoning_tokens if agg is not None else 0

    # Tool spans: hang each tool under its run (by the tool_call fact's run_id), timed
    # best-effort from the tool_use_start/end pair. Also surface in-flight starts that
    # never got a tool_call fact (cancelled mid-approval / mid-execute) so hangs are
    # visible in 会话复盘.
    tool_spans: dict[str, list[Span]] = {}
    for tcid in tool_order:
        info = tool_run[tcid]
        name = info["name"]
        tspan = Span(
            span_id=f"tool:{tcid}",
            parent_span_id=None,
            name=f"execute_tool {name}" if name else "execute_tool",
            operation="execute_tool",
            status="ok" if info["success"] else "error",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
            },
        )
        start = tool_start_ms.get(tcid)
        end = tool_end_ms.get(tcid)
        if start is not None and end is not None and end >= start:
            tspan.duration_ms = int(end - start)
        tool_spans.setdefault(info["run_id"], []).append(tspan)

    # Orphan starts: tool_use_start without a completed tool_call fact
    # (cancelled mid-approval / mid-execute) so hangs are visible in 会话复盘.
    orphan_starts: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if (entry.get("kind") or "") != EventType.TOOL_USE_START.value:
            continue
        payload = entry.get("payload") or {}
        tcid = payload.get("tool_call_id") or ""
        if not tcid or tcid in tool_run:
            continue
        orphan_starts[tcid] = {
            "run_id": payload.get("run_id") or "",
            "name": payload.get("tool_name") or "",
        }
    for tcid, info in orphan_starts.items():
        name = info["name"]
        tspan = Span(
            span_id=f"tool:{tcid}",
            parent_span_id=None,
            name=f"execute_tool {name}" if name else "execute_tool",
            operation="execute_tool",
            status="unset",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "agentcore.tool.in_flight": True,
                "agentcore.tool.orphan_start": True,
            },
        )
        start = tool_start_ms.get(tcid)
        end = tool_end_ms.get(tcid)
        if start is not None and end is not None and end >= start:
            tspan.duration_ms = int(end - start)
        tool_spans.setdefault(info["run_id"], []).append(tspan)

    # Link the tree: runs → parent run or root; tools → their run or root.
    for rid, span in runs.items():
        parent = run_parent.get(rid)
        parent_span = runs.get(parent) if parent else None
        target = parent_span or root
        span.parent_span_id = target.span_id
        target.children.append(span)
        for tspan in tool_spans.get(rid, []):
            tspan.parent_span_id = span.span_id
            span.children.append(tspan)
    # Tools whose run never opened a run_started (degenerate) attach to the root.
    for rid, tspans in tool_spans.items():
        if rid in runs:
            continue
        for tspan in tspans:
            tspan.parent_span_id = root.span_id
            root.children.append(tspan)

    workers = sum(
        1 for s in runs.values() if s.attributes.get("agentcore.run.kind") != RunKind.CAPTAIN.value
    )
    if workers:
        root.attributes["agentcore.workers"] = workers
    if total_in:
        root.attributes["gen_ai.usage.input_tokens"] = total_in
    if total_out:
        root.attributes["gen_ai.usage.output_tokens"] = total_out
    if total_tools:
        root.attributes["agentcore.tool_calls"] = total_tools
    # Root duration ≈ the captain run's wall clock (the turn's root run node).
    for _rid, span in runs.items():
        if span.attributes.get("agentcore.run.kind") == RunKind.CAPTAIN.value:
            root.duration_ms = span.duration_ms
            break

    # Nothing meaningful to trace (no runs, no tools) → no tree.
    if not root.children:
        return None
    return root


# ── exporters (留缝: log now, OTLP later) ─────────────────────────────────────────


@runtime_checkable
class SpanExporter(Protocol):
    """The seam between the (pure) span projection and the outside world.

    The default :class:`LogSpanExporter` writes to structlog; a future OTLP exporter
    (跨进程 trace, 远期规划 D2) implements the same one method — the projection above
    never changes.
    """

    def export(
        self,
        root: Span,
        *,
        trace_id: str | None,
        conversation_id: str,
        message_id: str,
    ) -> None: ...


class NoopSpanExporter:
    """Drops the tree (the projection still runs; useful for a benchmark / opt-out)."""

    def export(self, root: Span, **_: Any) -> None:  # noqa: D102
        return None


# A turn with a runaway fan-out should not write an unbounded log line; cap the span
# count in the emitted tree (the count itself is still reported honestly).
_MAX_LOGGED_SPANS = 256


def _span_priority(span: Span) -> int:
    """Lower ranks stay when the flattened tree exceeds ``_MAX_LOGGED_SPANS``.

    DFS preorder alone drops later members' tool spans (and their failures). Prefer
    the run skeleton, then error / in-flight tools, then the remaining DFS fill.
    """
    if span.operation in ("chat", "invoke_agent"):
        return 0
    if span.status == "error":
        return 1
    attrs = span.attributes
    if attrs.get("agentcore.tool.in_flight") or attrs.get("agentcore.tool.orphan_start"):
        return 2
    return 3


def _select_logged_spans(flat: list[Span]) -> tuple[list[Span], int]:
    """Bound the emitted span list without silently dropping the tail.

    Under the cap the DFS preorder is unchanged. Over it, keep the run skeleton and
    failure / in-flight tool spans first, then fill remaining slots in DFS order.
    The returned list stays DFS-ordered so parent/child is still readable inline.
    """
    limit = _MAX_LOGGED_SPANS
    if len(flat) <= limit:
        return flat, 0
    ranked = sorted(enumerate(flat), key=lambda item: (_span_priority(item[1]), item[0]))
    keep_ids = {s.span_id for _, s in ranked[:limit]}
    kept = [s for s in flat if s.span_id in keep_ids]
    return kept, len(flat) - len(kept)


class LogSpanExporter:
    """Emit the tree as one structured ``obs.turn_spans`` log line (greppable by trace_id).

    structlog renders it as JSON in the JSONL file (logging.mdc), so an AI / operator can
    pull a turn's whole execution trace with ``grep trace_id=<id>`` — a span tree without
    an OTel SDK / collector. The flattened span list keeps each node compact
    (:meth:`Span.to_log_dict`); the depth-first order makes the parent/child structure
    readable inline.

    ``trace_id`` is emitted explicitly (not just relied upon from the log context) so the
    line honours its own「greppable by trace_id」promise even if a caller runs it outside a
    bound scope. The turn's ``turn_id`` still rides the log context; this line adds the
    ``message_id`` (the assistant row this turn produced) so it also joins to ``messages``.
    """

    def export(
        self,
        root: Span,
        *,
        trace_id: str | None,
        conversation_id: str,
        message_id: str,
    ) -> None:
        flat = root.flatten()
        kept, dropped = _select_logged_spans(flat)
        logger.info(
            "obs.turn_spans",
            trace_id=trace_id,
            message_id=message_id,
            conversation_id=conversation_id,
            span_count=len(flat),
            truncated=dropped > 0,
            dropped=dropped,
            duration_ms=root.duration_ms,
            finish_reason=root.attributes.get("agentcore.finish_reason"),
            workers=root.attributes.get("agentcore.workers", 0),
            team_batch=root.attributes.get("agentcore.team_batch"),
            spans=[s.to_log_dict() for s in kept],
        )


_default_exporter: SpanExporter = LogSpanExporter()


def export_turn_spans(
    entries: list[dict[str, Any]] | None,
    *,
    trace_id: str | None,
    conversation_id: str,
    message_id: str,
    exporter: SpanExporter | None = None,
) -> None:
    """Project ``entries`` into a span tree and hand it to the exporter — best-effort.

    Called off the user path when the durable journal is persisted (complete,
    salvage, and local pause snapshot — ``journal.persist_turn_journal``, the
    single durable-journal choke point for every turn path). Observability must NEVER
    break a turn (文档铁律, same posture as the journal / cost ledger): any failure is
    swallowed with a warning. Empty ``entries`` is a silent no-op. A turn with facts but
    no run/tool tree still logs ``team_batch`` (零人回合的 no_batch 必须进云端投影).
    """
    try:
        from agentcore.runtime.journal.team_batch import team_batch_from_entries

        status = team_batch_from_entries(entries)
        root = spans_from_entries(entries)
        if root is None:
            if not entries:
                return
            logger.info(
                "obs.turn_spans",
                trace_id=trace_id,
                message_id=message_id,
                conversation_id=conversation_id,
                span_count=0,
                truncated=False,
                dropped=0,
                team_batch=status,
                spans=[],
            )
            return
        root.attributes["agentcore.team_batch"] = status
        (exporter or _default_exporter).export(
            root,
            trace_id=trace_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    except Exception as e:  # noqa: BLE001 — observability must never break the turn
        logger.warning("obs.span_export_failed", message_id=message_id, error=str(e))
