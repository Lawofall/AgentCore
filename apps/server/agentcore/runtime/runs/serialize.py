"""JSON (de)serialization for a recoverable RunSession (P3 跨进程落盘).

Kept out of the in-memory types so the roster core stays import-light. The message
shape mirrors the OpenAI / DeepSeek wire form (role / content / tool_calls /
tool_call_id / reasoning_content) and round-trips back into :class:`LLMMessage`, so
``continue_run`` replays the exact context — including a worker's tool-call turns and
tool results — after loading from disk. :class:`RunSpec` is ``asdict``-ed and rebuilt
with its nested :class:`RunPolicy` / :class:`Deliverable` (``continue_run`` reads
``spec.deliverable``), tolerating unknown / missing keys so a later schema tweak
never breaks loading an older row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, fields
from typing import Any

from agentcore.llm.provider.protocol import (
    LLMMessage,
    ToolCall,
    ToolCallFunction,
    llm_content_text,
)
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.session import RunSession
from agentcore.runtime.runs.types import (
    Deliverable,
    RunKind,
    RunOrigin,
    RunPhase,
    RunPolicy,
    RunSpec,
    RunState,
)
from agentcore.tools.file_products import (
    LANDING_TOOL_NAMES,
    LANDING_TOOLS,
    FileProduct,
    file_products_from_text,
)
from agentcore.workspace.limits import is_presence_disconnected_detail


def _tool_call_to_dict(tc: ToolCall) -> dict[str, Any]:
    return {
        "id": tc.id,
        "type": tc.type,
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def _tool_call_from_dict(d: dict[str, Any]) -> ToolCall:
    fn = d.get("function") or {}
    return ToolCall(
        id=str(d.get("id", "")),
        type=d.get("type", "function"),
        function=ToolCallFunction(
            name=str(fn.get("name", "")), arguments=str(fn.get("arguments", ""))
        ),
    )


def message_to_dict(m: LLMMessage) -> dict[str, Any]:
    """One transcript message → a compact JSON dict (omitting empty optionals)."""
    out: dict[str, Any] = {"role": m.role}
    if m.content is not None:
        out["content"] = m.content
    if m.tool_calls:
        out["tool_calls"] = [_tool_call_to_dict(tc) for tc in m.tool_calls]
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.reasoning_content:
        out["reasoning_content"] = m.reasoning_content
    return out


def message_from_dict(d: dict[str, Any]) -> LLMMessage:
    tcs = d.get("tool_calls")
    return LLMMessage(
        role=d.get("role", "user"),
        content=d.get("content"),
        tool_calls=[_tool_call_from_dict(t) for t in tcs] if tcs else None,
        tool_call_id=d.get("tool_call_id"),
        reasoning_content=d.get("reasoning_content"),
    )


# Failed landing-tool result → attribution for zero-disk gaps (contract / delivery card).
# Presence-disconnect over generic write-failed when both appear. Settle timeouts
# are write_failed, not disconnected.

# ``run`` (short path) lands files INDIRECTLY (sandbox copy-out), so the governance
# pen set (``LANDING_TOOLS``) excludes it — but a worker being told 「用什么落盘」
# should hear it. Prose only: the ledger reads self-reported products and needs no
# tool name at all.
_INDIRECT_LANDING_TOOL_NAME = "run"


def file_landing_tool_names() -> tuple[str, ...]:
    """Ordered tool names to NAME in files-not-landed gap copy (prose only).

    Single source for every such list — callers must not hand-write a subset (that is
    how a landing pen went missing from the contract copy for a whole release).
    """
    return (*LANDING_TOOL_NAMES, _INDIRECT_LANDING_TOOL_NAME)


def format_file_landing_tools_slash() -> str:
    """Slash-joined landing-tool names for CEO-facing files_written gap copy."""
    return " / ".join(file_landing_tool_names())


def landing_write_failure_kind(
    transcript: list[LLMMessage] | None,
) -> str | None:
    """Classify failed file-landing attempts for zero-disk / audit-JSON attribution.

    Returns ``channel_dead`` when any failed landing-tool result is a presence
    disconnect (desk fulfiller gone); ``write_failed`` when landing tools failed
    for other reasons (including a settle timeout); ``None`` when no failed
    landing-tool result is observed (true zero-attempt / paste-into-prose case).
    Successful landings are ignored here — callers may still pass the kind when
    some files landed (e.g. the desk dropped before companion ``*.audit.json``).
    """
    if not transcript:
        return None
    landing_call_ids: set[str] = set()
    saw_failed = False
    saw_channel_dead = False
    for msg in transcript:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function.name in LANDING_TOOLS and tc.id:
                    landing_call_ids.add(tc.id)
        elif msg.role == "tool" and msg.tool_call_id in landing_call_ids:
            content = llm_content_text(msg.content)
            if not _tool_result_failed(content):
                continue
            saw_failed = True
            if is_presence_disconnected_detail(content):
                saw_channel_dead = True
    if saw_channel_dead:
        return "channel_dead"
    if saw_failed:
        return "write_failed"
    return None


# 工具失败机器尾注 (生产方见 runtime/engine/tool_exec.py · TOOL_FAILED_MARKER):
# LLMMessage 无独立 success 字段，allowlist / 审批 / 熔断拒绝与执行失败均由 tool_exec 追加
# 此 marker，让 landing_write_failure_kind 区分「写盘尝试失败」与「压根没试」。格式内联 +
# round-trip 单测锁死（同构产物自报尾注）。
_TOOL_FAILED_MARKER = "<!--agentcore:tool_failed-->"


def _tool_result_failed(content: str) -> bool:
    """True when tool_exec stamped the machine failure trailer on this tool message."""
    return _TOOL_FAILED_MARKER in (content or "")


def file_products_from_transcript(transcript: list[LLMMessage]) -> list[FileProduct]:
    """产物台账：本 run 落盘的 ``{path, kind, derived_from?}``，首次出现顺序。

    事实来源是**工具自报**：落盘工具在 :class:`~agentcore.tools.protocol.ToolResult`
    上声明产物，引擎在 tool 消息上盖 ``<!--agentcore:file_products:…-->`` 机器尾注
    （生产方 ``tools.file_products.with_file_products_marker``，round-trip 单测钉死格式）。
    这里只读尾注——不认工具名、不解析入参、不读散文回执：

    - 入参不等于产物（``md_to_docx`` 入参是源 md，产物是推导出的 docx；批量工具一次产上千个）；
    - 落盘通道不止工具调用（沙箱写回、换树），凡自报者一律记账，无需在任何名单里登记；
    - 失败 / 被拒的调用不自报，天然不入账（引擎只在 ``success`` 时盖章）。

    回显防护也在生产侧：盖章前先清掉输出里回显的尾注（``file_read`` 读到一份带尾注的文本
    不算它产的），所以这里无需按 ``tool_call_id`` 反查工具名。只读 ``role="tool"`` 消息——
    模型正文里复述一段尾注不是事实。

    同一 path 被多次自报（写完再改）只记首次，与 ``files_touched`` 的去重口径一致。
    口径仍限本 run：transcript 是本 run 自己的，自报的是本次执行真正落的盘。
    """
    out: list[FileProduct] = []
    seen: set[str] = set()
    for msg in transcript:
        if msg.role != "tool":
            continue
        for product in file_products_from_text(llm_content_text(msg.content)):
            path = product.path.strip()
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(product)
    return out


def merge_file_products(*groups: Sequence[FileProduct]) -> list[FileProduct]:
    """Union product ledgers (first-seen path wins). Host/runtime rows after transcript."""
    out: list[FileProduct] = []
    seen: set[str] = set()
    for group in groups:
        for product in group:
            path = product.path.strip()
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(product)
    return out


def files_touched_from_transcript(transcript: list[LLMMessage]) -> list[str]:
    """Paths of :func:`file_products_from_transcript` (first-seen order, de-duped)."""
    return [p.path for p in file_products_from_transcript(transcript)]


def escalations_from_transcript(transcript: list[LLMMessage]) -> list[dict[str, Any]]:
    """Best-effort list of a worker's escalations (``escalate`` tool calls), call order.

    Each item is ``{question, assumption, blocking, kind, status, answer}`` parsed from
    the call's arguments (assumption defaults to "", blocking to False, kind to
    ``"normal"``). ``kind="scope"`` (职责/范围偏离) and ``kind="dep"`` (依赖缺口·卡在缺输入 X,
    §2.4) are BOTH consumed by the WaveScheduler at the reactive wave boundary
    (``BoundaryReason.SCOPE``) so the CEO re-steers / replan(add)s the not-yet-run tail
    (执行引擎架构设计.md §受监督的波循环); ``"normal"`` is an ordinary 待决问题 resolved at
    synthesis. ``status`` defaults to ``"raised"`` (a non-blocking escalate, or a blocking
    one that degraded) with no ``answer``; the executor overrides these to ``"resolved"``
    / ``"assumed"`` / ``"timed_out"`` for a blocking escalate that actually suspended (阻塞式求
    决策 §4.7). Unlike :func:`files_touched_from_transcript` (which correlates tool results
    for success), escalations are intent-level: read off the call itself; a call
    with malformed args or an empty ``question`` is skipped. The DelegateTool surfaces
    these to the CEO as「队员升级了待决问题」so it resolves them before finalizing.
    The tool name is the literal ``"escalate"`` (= ``ESCALATE_TOOL_NAME``); kept inline
    here to keep this serialization module dependency-light, as the file-tool names are.
    """
    out: list[dict[str, Any]] = []
    for msg in transcript:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.function.name != "escalate":
                continue
            try:
                parsed = json.loads(tc.function.arguments or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            question = str(parsed.get("question") or "").strip()
            if not question:
                continue
            kind = str(parsed.get("kind") or "normal").strip().lower()
            if kind not in ("normal", "scope", "dep"):
                kind = "normal"
            out.append(
                {
                    "question": question,
                    "assumption": str(parsed.get("assumption") or "").strip(),
                    "blocking": bool(parsed.get("blocking")),
                    # 执行引擎架构设计.md §受监督的波循环: "scope" (职责/范围偏离) and "dep"
                    # (依赖缺口·卡在缺输入 X, §2.4) are BOTH consumed at the reactive wave
                    # boundary — the CEO re-steers ("scope") / replan(add)s a producer ("dep")
                    # for the un-run tail; "normal" is an ordinary 待决问题 resolved at synthesis.
                    "kind": kind,
                    # 阻塞式求决策: lifecycle of a blocking escalate. Default for a
                    # non-blocking / degraded one; the executor folds in the user's
                    # resolution for one that suspended (设计 §4.7).
                    "status": "raised",
                    "answer": None,
                }
            )
    return out


# 完工交接简报 (worker → 下游/CEO): a delegated worker ends its run by calling the terminal
# ``handoff`` tool with a STRUCTURED brief (summary / key_points / assumptions / next_steps) — a
# wrap-up for its READERS, not more deliverable prose. Because it is structured, it is read
# STRAIGHT OFF the call's arguments here (never parsed back out of markdown prose — its former,
# fragile「## 交接简报」form): a downstream dep block can LEAD with the author's own 结论 (cheapest
# to read, survives budget-trim) and the CEO aggregate can surface 建议下一步 to relay to the user,
# instead of every reader re-deriving the gist from raw prose. Same discipline as the sibling
# transcript harvesters (escalations_from_transcript; files_touched_from_transcript for the
# call→result correlation): pure, unit-testable. Nodes with downstream dependents
# **require** a minimum-quality handoff
# (executor injects one correction shot; still missing → synthesize_debrief with ``degraded``);
# leaf nodes (no dependents) may finish without handoff when the deliverable is short and
# tool-free; after substantial work (tools / longer body) they share the same补要 / degraded
# path so CEO / ``delivery_status`` can see incomplete reports.
#
# The tool name is the literal ``"handoff"`` (= ``HANDOFF_TOOL_NAME``); kept inline here to keep
# this serialization module dependency-light, exactly as ``escalations_from_transcript`` keeps
# ``"escalate"`` and the file harvester keeps its file-tool names inline.
def _debrief_from_handoff_args(args: dict[str, Any]) -> dict[str, Any] | None:
    """A ``handoff`` call's arguments → the debrief dict, or None when it carried nothing usable.

    Only the fields the author actually filled are kept (each omitted when empty), matching the
    shape the run-detail card / dep injection / CEO synthesis already consume. ``key_points`` is a
    list (a lone string is tolerated by wrapping it; a markdown bullet list string is split);
    the other three are single strings.
    ``motion_card`` 已撤：新回合不收获；历史 debrief JSON 仍可躺在 journal。"""
    from agentcore.runtime.engine.tool_protocol_sanitize import sanitize_protocol_text
    from agentcore.tools.builtin.ask_user.schema import (
        ListArgError,
        coerce_list_arg,
        split_markdown_list_items,
    )

    out: dict[str, Any] = {}
    summary = sanitize_protocol_text(str(args.get("summary") or "")).strip()
    if summary:
        out["summary"] = summary
    raw_points = args.get("key_points")
    if isinstance(raw_points, str):
        md_items = split_markdown_list_items(raw_points)
        if md_items is not None:
            raw_points = md_items
        else:
            # JSON-array-as-string or plain prose → coerce_list_arg / wrap.
            try:
                raw_points = coerce_list_arg(
                    raw_points, field="key_points", allow_markdown_bullets=True
                )
            except ListArgError:
                raw_points = [raw_points]
    key_points: list[str] = []
    for p in raw_points or []:
        cleaned = sanitize_protocol_text(str(p)).strip()
        if cleaned:
            # Nested markdown blob inside a one-element list (model fumble).
            nested = split_markdown_list_items(cleaned)
            if nested is not None and len(nested) > 1:
                key_points.extend(nested)
            else:
                key_points.append(cleaned)
    if key_points:
        out["key_points"] = key_points
    assumptions = sanitize_protocol_text(str(args.get("assumptions") or "")).strip()
    if assumptions:
        out["assumptions"] = assumptions
    next_steps = sanitize_protocol_text(str(args.get("next_steps") or "")).strip()
    if next_steps:
        out["next_steps"] = next_steps
    return out or None


def debrief_from_transcript(transcript: list[LLMMessage]) -> dict[str, Any] | None:
    """The worker's 交接简报, harvested from its ``handoff`` tool call, or None.

    Walks the transcript for ``handoff`` calls and parses the LAST valid one's arguments (a
    re-worked / revised run may submit more than once — the final brief wins). Mirrors
    :func:`escalations_from_transcript`: read off the call itself, a call with malformed args or
    no usable field is skipped. None when there is no ``handoff`` call at all."""
    result: dict[str, Any] | None = None
    for msg in transcript:
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.function.name != "handoff":
                continue
            try:
                parsed = json.loads(tc.function.arguments or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            debrief = _debrief_from_handoff_args(parsed)
            if debrief is not None:
                result = debrief  # last valid handoff wins
    return result


def transcript_to_json(transcript: list[LLMMessage]) -> list[dict[str, Any]]:
    return [message_to_dict(m) for m in transcript]


def transcript_from_json(data: list[dict[str, Any]] | None) -> list[LLMMessage]:
    return [message_from_dict(d) for d in (data or [])]


def _optional_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _filtered(cls: type, data: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only keys that are real fields of ``cls`` — tolerate schema drift so a
    row written by an older/newer build still loads."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in names}


def spec_to_json(spec: RunSpec) -> dict[str, Any]:
    """RunSpec → JSON dict. ``asdict`` recurses into RunPolicy / Deliverable; the
    StrEnum ``kind`` serializes as its string value through JSONB."""
    return asdict(spec)


def spec_from_json(data: dict[str, Any]) -> RunSpec:
    """Rebuild a RunSpec (with nested RunPolicy / Deliverable) from its JSON dict."""
    data = dict(data or {})
    policy_raw = dict(data.pop("policy", None) or {})
    policy = RunPolicy(**_filtered(RunPolicy, policy_raw))
    deliverable_raw = data.pop("deliverable", None)
    deliverable: Deliverable | None = None
    if isinstance(deliverable_raw, dict):
        fields = _filtered(Deliverable, deliverable_raw)
        deliverable = Deliverable(**fields)
    kwargs = _filtered(RunSpec, data)
    kwargs["policy"] = policy
    if deliverable is not None:
        kwargs["deliverable"] = deliverable
    kind = kwargs.get("kind")
    if isinstance(kind, str):
        kwargs["kind"] = RunKind(kind)
    return RunSpec(**kwargs)


def state_to_json(state: RunState) -> dict[str, Any]:
    """A RunState → compact JSON for a paused-turn seed (结构化挂起 durable resume).

    Carries exactly what a resume needs to treat a node as already-finished: its
    ``phase`` + product (downstream reads ``content``) plus the priced
    ``usage``/``cost``/``citations`` so the resumed turn bills the pre-pause work
    ONCE (it was never billed — the turn paused before persistence) and folds its
    tokens/sources into the totals. The heavy ``transcript`` is intentionally
    dropped: a seed_completed node is never re-run or revised, downstream reads only
    its ``content`` — so the frame stays light.
    """
    return {
        "phase": state.phase.value,
        "content": state.content,
        "reasoning": state.reasoning,
        "error": state.error,
        # 确定性失败区分 (BL-6): persist the retryable verdict so a resume
        # rebuild + the audit trail keep the「这次失败是确定性的」signal (default True keeps
        # older frames unchanged). Omitted from the shape for COMPLETED nodes is fine — the
        # deserializer defaults it True.
        "error_retryable": state.error_retryable,
        "error_code": state.error_code,
        "error_retry_after": state.error_retry_after,
        "warnings": list(state.warnings),
        "delivery_gaps": [
            dict(g) for g in (state.delivery_gaps or []) if isinstance(g, dict)
        ],
        "escalations": [dict(e) for e in state.escalations],
        "citations": list(state.citations),
        "model": state.model,
        "duration_ms": state.duration_ms,
        "rounds": state.rounds,
        "files_touched": list(state.files_touched),
        "file_acceptance": [
            dict(row) for row in (state.file_acceptance or []) if isinstance(row, dict)
        ],
        "tool_failures": [dict(row) for row in state.tool_failures if isinstance(row, dict)],
        "debrief": dict(state.debrief) if state.debrief else None,
        "usage": dict(state.usage),
        "cost": dict(state.cost),
    }


def state_from_json(data: dict[str, Any]) -> RunState:
    """Rebuild a (seed) RunState from :func:`state_to_json`; tolerates missing keys
    so an older/newer frame still loads."""
    data = dict(data or {})
    phase = data.get("phase")
    return RunState(
        phase=RunPhase(phase) if isinstance(phase, str) else RunPhase.COMPLETED,
        content=data.get("content", "") or "",
        reasoning=data.get("reasoning", "") or "",
        error=data.get("error", "") or "",
        error_retryable=bool(data.get("error_retryable", True)),
        error_code=str(data.get("error_code") or ""),
        error_retry_after=_optional_float(data.get("error_retry_after")),
        warnings=list(data.get("warnings") or []),
        delivery_gaps=[
            dict(g) for g in (data.get("delivery_gaps") or []) if isinstance(g, dict)
        ],
        escalations=[dict(e) for e in (data.get("escalations") or []) if isinstance(e, dict)],
        citations=list(data.get("citations") or []),
        model=data.get("model", "") or "",
        duration_ms=int(data.get("duration_ms", 0) or 0),
        rounds=int(data.get("rounds", 0) or 0),
        files_touched=list(data.get("files_touched") or []),
        file_acceptance=[
            dict(row)
            for row in (data.get("file_acceptance") or [])
            if isinstance(row, dict)
        ],
        tool_failures=[
            dict(row) for row in (data.get("tool_failures") or []) if isinstance(row, dict)
        ],
        debrief=data.get("debrief") if isinstance(data.get("debrief"), dict) else None,
        usage=dict(data.get("usage") or {}),
        cost=dict(data.get("cost") or {}),
    )


def plan_to_json(plan: RunPlan) -> dict[str, Any]:
    """A RunPlan → JSON ({nodes, origin}) so a paused turn rebuilds the EXACT graph
    — with its already-minted run_ids — on resume. Re-deriving from the delegate
    args would mint fresh ids (``del_<uuid>_N``) that no longer match the
    seed_completed map keyed by the original ids."""
    payload: dict[str, Any] = {
        "nodes": [spec_to_json(n) for n in plan.nodes],
        "origin": plan.origin.value,
    }
    if plan.topology_lock:
        payload["topology_lock"] = True
    if plan.workflow_id:
        payload["workflow_id"] = plan.workflow_id
    if plan.workflow_version is not None:
        payload["workflow_version"] = int(plan.workflow_version)
    return payload


def plan_from_json(data: dict[str, Any]) -> RunPlan:
    """Rebuild a RunPlan from :func:`plan_to_json`."""
    data = dict(data or {})
    origin = data.get("origin")
    plan = RunPlan(nodes=[spec_from_json(n) for n in (data.get("nodes") or [])])
    if isinstance(origin, str):
        plan.origin = RunOrigin(origin)
    plan.topology_lock = bool(data.get("topology_lock"))
    wid = data.get("workflow_id")
    plan.workflow_id = str(wid).strip() if isinstance(wid, str) and wid.strip() else None
    wv = data.get("workflow_version")
    if isinstance(wv, int):
        plan.workflow_version = wv
    elif isinstance(wv, str) and wv.strip().isdigit():
        plan.workflow_version = int(wv.strip())
    # 旧快照若含 ``finalize`` 键：忽略，不当直出。
    return plan


def state_map_to_json(completed: dict[str, RunState]) -> dict[str, dict[str, Any]]:
    """The scheduler's completed map (run_id → RunState) → JSON for a paused frame."""
    return {run_id: state_to_json(state) for run_id, state in completed.items()}


def state_map_from_json(data: dict[str, Any] | None) -> dict[str, RunState]:
    """Rebuild the completed map (seed_completed) from :func:`state_map_to_json`."""
    return {run_id: state_from_json(raw) for run_id, raw in (data or {}).items()}


def run_final_fact(run_id: str, state: RunState) -> Any:
    """A worker run's terminal RunState as a ``message_final`` journal fact.

    执行级事件溯源 Phase 2 ⑥ (``frame.completed`` 的事实来源): the payload **is**
    :func:`state_to_json` (the exact seed shape the frame stored) keyed by ``run_id`` and
    tagged by its ``phase``, so :func:`agentcore.runtime.journal.completed_from_journal`
    rebuilds the scheduler seed map with the SAME deserializer (:func:`state_from_json`) —
    zero drift between the (being-removed) ``paused_turns.frame`` blob and its journal
    projection. Recorded for EVERY terminal worker (COMPLETED / FAILED) at the executor's
    single run choke point, so a resume re-seeds finished nodes from facts, never the旁路
    frame. ``message_final`` (vs a new kind) keeps the §8.3 execution-kind set stable; the
    captain's own ``message_final`` (content/reasoning, no ``phase``) is NOT a seed and is
    skipped by the projection.
    """
    from agentcore.runtime.facts import Fact, FactKind

    return Fact(
        kind=FactKind.MESSAGE_FINAL.value,
        payload={"run_id": run_id, **state_to_json(state)},
    )


def plan_snapshot_fact(plan: RunPlan) -> Any:
    """A delegate's full DAG as a ``plan_snapshot`` journal fact (执行级事件溯源 Phase 2).

    The execution source for ``frame.plan`` (its exit): the payload **is**
    :func:`plan_to_json` (the exact graph the frame stored — every :class:`RunSpec` with
    its minted run_id, accumulated ``steer`` and policy/contract), so
    :func:`agentcore.runtime.journal.plan_from_journal` rebuilds it with the SAME
    deserializer (:func:`plan_from_json`) — zero drift between the (being-removed) blob and
    its journal projection (the conformance golden gates this ``==``). Recorded at plan
    build AND after each ``adjust`` steer, so the LAST snapshot reflects the cumulative
    plan (steer accumulates across checkpoints); the projector takes the last one,
    last-write-wins. A distinct kind from the display ``run_plan`` event keeps the display
    projection's surface gate untouched.
    """
    from agentcore.runtime.facts import Fact, FactKind

    return Fact(kind=FactKind.PLAN_SNAPSHOT.value, payload=plan_to_json(plan))


def session_to_row(session: RunSession) -> dict[str, Any]:
    """The persisted columns for a RunSession (``conversation_id`` is attached by the
    repository from the turn envelope, not stored on the in-memory session)."""
    return {
        "run_id": session.run_id,
        "spec": spec_to_json(session.spec),
        "transcript": transcript_to_json(session.transcript),
        "content": session.content,
        "recall_count": session.recall_count,
        # partial is in-memory only (redirect salvage); durable rows are completed sessions.
    }


def session_from_row(row: Any) -> RunSession:
    """Rebuild a RunSession from a ``RunSessionRow`` (attribute access)."""
    return RunSession(
        run_id=row.run_id,
        spec=spec_from_json(row.spec),
        transcript=transcript_from_json(row.transcript),
        content=row.content or "",
        recall_count=row.recall_count or 0,
        partial=False,
    )
