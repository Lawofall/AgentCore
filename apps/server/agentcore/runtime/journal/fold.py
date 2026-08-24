"""Journal projection (read path: ordered facts → runs payload / LLM window / resume seed)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.events import _JOURNAL_SURFACE_TYPES, EventType, FinishReason
from agentcore.runtime.events.payloads.process import RETIRED_PROCESS_STEP_KINDS
from agentcore.runtime.events.types import RETIRED_EVENT_TYPE_VALUES
from agentcore.runtime.facts import EXECUTION_ONLY_KINDS, FactKind, pre_pause_from_journal
from agentcore.runtime.runs.types import RunKind
from agentcore.runtime.terminal import RUN_PRODUCT_EVENT_TYPES

from .entries import _PROCESS_PREFIX, _RUN_PROCESS_PREFIX, KIND_TURN_END

if TYPE_CHECKING:
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.runs.plan import RunPlan
    from agentcore.runtime.runs.types import RunState

# Product-bearing close frames (completed / failed). Occupancy also includes
# cancelled / skipped — those live on RUN_CLOSE_EVENT_TYPES, not here.

# ``before_last_team`` process markers (开工卡 / 委派授权): product narrative is
# 放行 → 协作图. ``run_plan`` may journal ``process_team`` first; fold still inserts
# these ahead of the last ``team`` so reload order matches live EventSink.
_BEFORE_LAST_TEAM_PROCESS_KINDS = frozenset({"team_preview"})


def _normalize_process_lane(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Journal compat: pre-redirect tool rows stored channel steers as ``status=error``."""
    from agentcore.runtime.engine.tool_channel_redirect import process_tool_status_from_end

    changed = False
    out: list[dict[str, Any]] = []
    for step in steps:
        if step.get("kind") != "tool":
            out.append(step)
            continue
        new_status = process_tool_status_from_end(step)
        if new_status == step.get("status"):
            out.append(step)
            continue
        changed = True
        out.append({**step, "status": new_status})
    return out if changed else steps


def _upsert_tool_step(steps: list[dict[str, Any]], step: dict[str, Any]) -> None:
    """Append a tool step, or replace an earlier row with the same ``id`` (last wins).

    Progressive persist may compensate a stale ``status=running`` ``process_tool`` with
    a later terminal fact; cold reload must surface the terminal row only.
    """
    tid = step.get("id")
    if tid:
        for i, existing in enumerate(steps):
            if existing.get("kind") == "tool" and existing.get("id") == tid:
                steps[i] = step
                return
    steps.append(step)


def _has_team_marker(steps: list[dict[str, Any]], execution_id: str) -> bool:
    return any(
        s.get("kind") == "team" and s.get("execution_id") == execution_id for s in steps
    )


def _append_process_step(steps: list[dict[str, Any]], step: dict[str, Any]) -> None:
    """Append a non-tool process step, applying ``before_last_team`` when needed."""
    kind = step.get("kind")
    if kind in RETIRED_PROCESS_STEP_KINDS:
        return
    if kind == "team":
        eid = step.get("execution_id") or ""
        if eid and _has_team_marker(steps, eid):
            return
        steps.append(step)
        return
    if kind in _BEFORE_LAST_TEAM_PROCESS_KINDS:
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("kind") == "team":
                steps.insert(i, step)
                return
    steps.append(step)


def _note_team_slot_from_run_plan(
    slots: dict[str, int],
    process: list[dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    """Record where a missing ``team`` should sit (index = current process length).

    Applied after the process lane is fully rebuilt so we do not block G1 snapshot
    hydrate when the journal has ``run_plan`` but no progressive ``process_*``.
    """
    if payload.get("host_message_id"):
        return
    eid = payload.get("execution_id") or ""
    if not eid or eid in slots or _has_team_marker(process, eid):
        return
    slots[eid] = len(process)


def _apply_team_slots_from_run_plans(
    process: list[dict[str, Any]],
    slots: dict[str, int],
) -> None:
    """Insert deferred ``team`` markers (highest index first so earlier slots stay valid)."""
    if not slots:
        return
    for eid, at in sorted(slots.items(), key=lambda item: item[1], reverse=True):
        if _has_team_marker(process, eid):
            continue
        idx = max(0, min(at, len(process)))
        process.insert(idx, {"kind": "team", "execution_id": eid})


def _reorder_before_last_team_markers(process: list[dict[str, Any]]) -> None:
    """After deferred ``team`` insert, keep 开工卡 / 授权 ahead of the last team."""
    markers = [s for s in process if s.get("kind") in _BEFORE_LAST_TEAM_PROCESS_KINDS]
    if not markers:
        return
    rest = [s for s in process if s.get("kind") not in _BEFORE_LAST_TEAM_PROCESS_KINDS]
    team_idx = next(
        (i for i in range(len(rest) - 1, -1, -1) if rest[i].get("kind") == "team"),
        None,
    )
    if team_idx is None:
        process[:] = rest + markers
        return
    process[:] = [*rest[:team_idx], *markers, *rest[team_idx:]]


def _splice_synthetic_deltas(
    events: list[dict[str, Any]],
    final_outputs: dict[str, dict[str, str]],
    agent_run_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Reconstruct each agent run's run_output_delta / run_reasoning_delta from its
    ``message_final`` fact (执行级事件溯源: deltas 退场).

    The per-token worker deltas are no longer journaled; instead a single equivalent
    delta block is spliced in just before the run's terminal event (run_completed /
    run_failed), so the unchanged client fold rebuilds the node's 输出 / 思考全文 on
    reload (it sees the same event types, merely coalesced into one delta each).

    Scoped to agent runs (``agent_run_ids``, from the kind=agent run_started): the
    CAPTAIN's own ``message_final`` is the chat bubble's text (streamed as the
    turn-level ``content_delta``, not run-scoped), so it must NOT light up the captain
    run node — and its run_id is absent from ``agent_run_ids``, so it is skipped here.
    Reasoning precedes content, mirroring the live order (DeepSeek streams the whole
    reasoning_content before any content); both inherit the terminal event's timestamp
    so the replay timeline orders them immediately before completion.
    """
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("type") in RUN_PRODUCT_EVENT_TYPES:
            run_id = (ev.get("payload") or {}).get("run_id")
            agent_id = agent_run_ids.get(run_id) if run_id else None
            final = final_outputs.get(run_id) if run_id else None
            if final is not None and agent_id is not None:
                ts = ev.get("timestamp")
                if final["reasoning"]:
                    out.append(
                        {
                            "type": EventType.RUN_REASONING_DELTA.value,
                            "payload": {
                                "run_id": run_id,
                                "agent_id": agent_id,
                                "delta": final["reasoning"],
                            },
                            "timestamp": ts,
                        }
                    )
                if final["content"]:
                    out.append(
                        {
                            "type": EventType.RUN_OUTPUT_DELTA.value,
                            "payload": {
                                "run_id": run_id,
                                "agent_id": agent_id,
                                "delta": final["content"],
                            },
                            "timestamp": ts,
                        }
                    )
        out.append(ev)
    return out


def runs_from_entries(entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Project ordered journal entries back into a ``runs`` replay payload (DISPLAY).

    Inverse of :func:`entries.entries_from_runs` for the team-graph ``events`` /
    single-agent ``process`` / ``turn_end`` lanes: events rebuild the
    ``{type, payload, timestamp}`` shape the client folds, process steps restore
    verbatim, ``turn_end`` supplies ``finish_reason``. Returns ``None`` when nothing
    is replayable, matching the old「``messages.runs`` is NULL」contract so the client
    renders a plain bubble.

    Execution facts (``EXECUTION_ONLY_KINDS`` / ``message_final``) are skipped from
    ``events`` — they carry engine-rebuild state, not client-foldable display. The
    surface gate (parity with ``EventSink.execution_journal``) always runs: pre-gated
    display-only journals (salvage / incomplete / local-relay) pass through unchanged;
    execution-sourced journals drop captain-only noise. Plain chat turns (no graph,
    process, context, or abnormal outcome) project to ``None``.

    deltas 退场: per-token worker deltas are no longer journaled; each agent run's full
    output lives in its ``message_final`` fact, from which :func:`_splice_synthetic_deltas`
    reconstructs one equivalent delta block per run before the terminal event.

    挂起中冷启动重载 (G1): when progressive ``process_*`` / ``run_process_*`` are
    absent (legacy pause frames), this fold falls back to the last
    ``turn_paused`` snapshot's ``process`` / ``run_processes`` (via
    :func:`pre_pause_from_journal`). New pauses flush process_* and leave those
    snapshot fields empty; old journals without ``turn_paused`` stay empty
    on the process lanes.
    """
    if not entries:
        return None
    events: list[dict[str, Any]] = []
    process: list[dict[str, Any]] = []
    run_processes: dict[str, list[dict[str, Any]]] = {}
    # Legacy journals: ``run_plan`` without ``process_team`` — defer insert until the
    # process lane is complete (progressive facts or G1 snapshot).
    team_slots_from_run_plan: dict[str, int] = {}
    finish_reason: str | None = None
    # The 报错回合 outcome (code + message) carried on turn_end, projected back so the
    # bubble rebuilds its inline error card on reload (Tier 2 a). None for a clean turn.
    turn_error: dict[str, Any] | None = None
    # 上下文传递可视化 通道①: the CEO captain's received context is TURN-LEVEL (the chat
    # bubble above the graph), so it is lifted out of the node events into captain_context
    # — present even on a pure-chat turn (no surface), where the events gate to []. Keyed
    # by the captain run id (run_started kind=captain).
    captain_run_id: str | None = None
    captain_context: list[dict[str, Any]] | None = None
    # 预检警告（P2 DURABLE）：plain-chat 也可能只有 turn_warning（无 surface）——像
    # captain_context 一样抬到顶层，避免 surface gate 清空 events 后整段投影变 None。
    turn_warning: str | None = None
    # 裸聊自动建文件夹（§5.4 裸聊行）：抬到顶层供投影；对话内不再渲染落点条。
    auto_folder: dict[str, Any] | None = None
    # deltas 退场: a worker/revision run's full output + thinking now lives only in its
    # ``message_final`` fact (the per-token run_output_delta / run_reasoning_delta are no
    # longer journaled). Collect those finals (run_id → {content, reasoning}) and the
    # agent-kind run ids (run_id → agent_id, from run_started) so the display projection
    # can synthesize equivalent delta blocks below — keeping the client fold unchanged.
    final_outputs: dict[str, dict[str, str]] = {}
    agent_run_ids: dict[str, str] = {}
    for entry in entries:
        kind = entry.get("kind") or ""
        payload = entry.get("payload") or {}
        if kind == KIND_TURN_END:
            finish_reason = payload.get("finish_reason")
            turn_error = payload.get("error")
        elif kind == FactKind.MESSAGE_FINAL.value:
            # An execution fact (skipped from events like its peers), BUT its full
            # text is replayed as a synthetic delta block (spliced below). Collect
            # it keyed by run_id; the captain's own message_final is collected too but
            # is never synthesized (its run_id is not an agent run — see the splice).
            run_id = payload.get("run_id")
            if run_id:
                final_outputs[run_id] = {
                    "content": payload.get("content") or "",
                    "reasoning": payload.get("reasoning") or "",
                }
            continue
        elif kind in EXECUTION_ONLY_KINDS:
            # Execution-level facts carry engine-rebuild state, not client-foldable
            # display events — skip them so they never leak into runs.events.
            continue
        elif kind in RETIRED_EVENT_TYPE_VALUES:
            continue
        elif kind.startswith(_PROCESS_PREFIX):
            suffix = kind[len(_PROCESS_PREFIX) :]
            if suffix == "tool":
                _upsert_tool_step(process, payload)
            else:
                _append_process_step(process, payload)
        elif kind.startswith(_RUN_PROCESS_PREFIX):
            # Per-run worker timeline (对称 CEO process_ lane). Payload carries run_id
            # plus the ProcessStep fields; strip run_id so the restored step matches
            # the live wire shape (ProcessStep has no run_id).
            rid = payload.get("run_id")
            if rid:
                step = {k: v for k, v in payload.items() if k != "run_id"}
                lane = run_processes.setdefault(rid, [])
                suffix = kind[len(_RUN_PROCESS_PREFIX) :]
                if suffix == "tool":
                    _upsert_tool_step(lane, step)
                else:
                    lane.append(step)
        else:
            # Remember each agent (worker / revision) run's agent_id so the synthetic
            # delta block can be attributed (the captain run_started is kind=captain →
            # excluded, so its message_final never becomes a run-node delta). The captain
            # run id is remembered too, so its run_context lifts to captain_context below.
            if kind == EventType.RUN_STARTED.value:
                run_kind = payload.get("kind")
                if run_kind == RunKind.AGENT.value:
                    run_id = payload.get("run_id")
                    if run_id:
                        agent_run_ids[run_id] = payload.get("agent_id") or ""
                elif run_kind == RunKind.CAPTAIN.value:
                    captain_run_id = payload.get("run_id")
            elif (
                kind == EventType.RUN_CONTEXT.value
                and captain_run_id is not None
                and payload.get("run_id") == captain_run_id
            ):
                # 上下文传递可视化 通道①+⑤: capture the captain's context turn-level, GROWING
                # it across every emit (opening + each post-delegation team readback) — the
                # same APPEND the live/replay folds do. Still appended to events below (kept
                # for the team-graph round-trip); the client routes it off the captain node
                # and reads it from captain_context instead.
                if captain_context is None:
                    captain_context = []
                captain_context.extend(payload.get("blocks") or [])
            elif kind == EventType.TURN_WARNING.value:
                # Keep latest (preflight emits at most one; defensive if multiple).
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    turn_warning = msg
            elif kind == EventType.AUTO_FOLDER_CREATED.value:
                # Keep latest (a turn mints at most one auto folder).
                fid = payload.get("folder_id")
                if isinstance(fid, str) and fid.strip():
                    auto_folder = {
                        "folder_id": fid,
                        "name": payload.get("name") or "",
                    }
            events.append({"type": kind, "payload": payload, "timestamp": entry.get("ts")})
            if kind == EventType.RUN_PLAN.value:
                _note_team_slot_from_run_plan(
                    team_slots_from_run_plan, process, payload
                )
    # G1 挂起中重载: no process_* / run_process_* lanes → use last turn_paused snapshot.
    if not process and not run_processes:
        snap = pre_pause_from_journal(entries)
        if snap is not None:
            if snap.process:
                process = list(snap.process)
            if snap.run_processes:
                run_processes = {
                    rid: list(steps) for rid, steps in snap.run_processes.items()
                }
    _apply_team_slots_from_run_plans(process, team_slots_from_run_plan)
    _reorder_before_last_team_markers(process)
    if final_outputs:
        events = _splice_synthetic_deltas(events, final_outputs, agent_run_ids)
    # Surface gate (parity with EventSink.execution_journal): idempotent on journals
    # that were already gated at write (salvage / incomplete / local-relay).
    if not any(e["type"] in _JOURNAL_SURFACE_TYPES for e in events):
        events = []
    # None-gate: a plain chat turn (clean end_turn, no graph/process/context/error/warning)
    # → render a plain bubble. Abnormal finishes (cancelled / error / …) and salvage
    # payloads with only ``turn_end`` still project non-None.
    if (
        not events
        and not process
        and not run_processes
        and not captain_context
        and not turn_error
        and not turn_warning
        and not auto_folder
        and (finish_reason is None or finish_reason == FinishReason.END_TURN.value)
    ):
        return None
    runs: dict[str, Any] = {"events": events, "finish_reason": finish_reason}
    if process:
        runs["process"] = _normalize_process_lane(process)
    if run_processes:
        runs["run_processes"] = {
            rid: _normalize_process_lane(steps) for rid, steps in run_processes.items()
        }
    if captain_context is not None:
        runs["captain_context"] = captain_context
    if turn_error is not None:
        runs["error"] = turn_error
    if turn_warning is not None:
        runs["turn_warning"] = turn_warning
    if auto_folder is not None:
        runs["auto_folder"] = auto_folder
    return runs


def window_from_journal(
    entries: list[dict[str, Any]] | None,
    *,
    run_id: str | None = None,
    history: list[LLMMessage] | None = None,
) -> list[LLMMessage] | None:
    """Project a turn's journal facts into ONE run's LLM window (EXECUTION).

    The execution-side counterpart of :func:`runs_from_entries`: where that rebuilds
    the *display* runs payload, this folds the §8.3 execution facts back into the
    ``list[LLMMessage]`` the engine actually fed the model — the same shape the live
    captain transcript / a worker's ``messages`` take, so resume can feed it straight
    back and the conformance golden can assert it ``==`` the transcript at a pause
    (执行级事件溯源 §8.3, the ``window_from_journal`` projection).

    Correct-by-construction — only outputs are journaled, so the window is the fold of
    all prior facts (no quadratic input duplication):

    - ``run_head`` (per ``run_id``) → a **worker / continuation** head: ``system`` +
      opening ``user`` captured when that run assembled its task-prompt. Preferred
      whenever present so a worker is never falsely headed by the CEO turn prompt.
    - ``turn_started`` → the **captain** head: a ``system`` message (the verbatim
      captured prompt) + the ``user`` message, with ``history`` (prior turns —
      supplied by the caller, since the facts carry only its length) spliced between
      them exactly as the executor builds it. Used only when the target has no
      ``run_head`` (captain / legacy unscoped fold).
    - each ``llm_call`` of the target run that carried ``tool_calls`` → the ``assistant``
      message (``content`` / ``reasoning_content`` echoed verbatim — DeepSeek thinking
      mode 400s without the reasoning on a tool-call turn, see 平台LLM接入 · DeepSeek
      易错 — plus the
      ``tool_calls``), followed by one ``tool`` message per **completed** call (result
      matched by ``tool_call_id`` from the execution ``tool_call`` fact — the FULL
      post-annotation text the round carried, 边界① cleared). A call with no ``tool_call``
      fact is the SUSPENDED one (the pause happens inside ``ask_user`` / ``delegate``,
      which blocks before the fact is recorded): no tool message, so the window ends at
      the assistant exactly as the paused transcript does. A no-tool ``llm_call`` is the
      turn's final answer — the loop *returns* it, never appends it.
    - each engine-injected ``note`` (NUDGE / FINALIZE / circuit-breaker / reflection)
      belonging to the target run (by the note's own ``run_id``, 边界② cleared) → a
      ``user`` message, exactly as the loop injects it — so a captain note injected
      mid-delegate still folds into the captain window.

    ``run_id`` scopes a multi-agent turn to one run; ``None`` infers the captain (the
    run of the first ``role="captain"`` round_boundary — the resume target, whose head
    is ``turn_started``). Returns ``None`` when neither a usable head nor any folded
    rounds exist (a display-only journal).
    """
    if not entries:
        return None
    from agentcore.llm.provider.protocol import LLMMessage, ToolCall, ToolCallFunction

    # Head anchors + (when unscoped) the captain run to fold: one pass for both.
    started: dict[str, Any] | None = None
    run_heads: dict[str, dict[str, Any]] = {}
    run_roles: dict[str, str] = {}
    captain_id: str | None = None
    target = run_id
    for entry in entries:
        kind = entry.get("kind") or ""
        payload = entry.get("payload") or {}
        if kind == FactKind.TURN_STARTED.value and started is None:
            started = payload
        elif kind == FactKind.RUN_HEAD.value:
            rid = payload.get("run_id") or ""
            if rid and rid not in run_heads:
                run_heads[rid] = payload
        elif kind == FactKind.ROUND_BOUNDARY.value:
            rid = payload.get("run_id") or ""
            role = payload.get("role") or ""
            if rid and rid not in run_roles:
                run_roles[rid] = role
            if role == "captain" and captain_id is None:
                captain_id = rid
                if target is None:
                    target = rid
    if target is None:
        # No captain round_boundary (degenerate / single-run) → fold the first run.
        for entry in entries:
            if (entry.get("kind") or "") == FactKind.ROUND_BOUNDARY.value:
                target = (entry.get("payload") or {}).get("run_id") or ""
                break

    run_head = run_heads.get(target) if target else None
    target_role = run_roles.get(target or "") if target else None
    # Captain head (turn_started) only when this fold is the captain / unscoped path.
    # A worker without ``run_head`` (legacy journal) must NOT inherit the CEO prompt.
    use_turn_started = run_head is None and started is not None and (
        run_id is None
        or (captain_id is not None and target == captain_id)
        or target_role == "captain"
        or (target_role is None and captain_id is None and started is not None)
    )

    # Index each tool result by tool_call_id from the execution ``tool_call`` fact (the
    # FULL post-annotation result the round actually carried — NOT the forwarded display
    # ``tool_use_end``, whose text predates the CEO citation fold, 边界① cleared). The
    # assistant→tool pairing matches on tool_call_id (globally unique), so a worker's
    # tools never bleed into the captain window.
    tool_results: dict[str, str] = {}
    for entry in entries:
        if (entry.get("kind") or "") == FactKind.TOOL_CALL.value:
            payload = entry.get("payload") or {}
            tcid = payload.get("tool_call_id")
            if tcid:
                tool_results[tcid] = payload.get("result") or ""

    # Head selection:
    #   1. ``run_head`` for the target → worker / continuation (no conversation history)
    #   2. else ``turn_started`` → captain (or unscoped) head + caller history
    #   3. else empty head (legacy worker journal without run_head — honest rounds-only)
    window: list[LLMMessage] = []
    if run_head is not None:
        window.append(LLMMessage(role="system", content=run_head.get("system_prompt") or ""))
        window.append(LLMMessage(role="user", content=run_head.get("user_message") or ""))
    elif use_turn_started and started is not None:
        window.append(LLMMessage(role="system", content=started.get("system_prompt") or ""))
        if history:
            window.extend(history)
        window.append(LLMMessage(role="user", content=started.get("user_message") or ""))
    elif started is None and run_head is None and not target:
        return None

    # Fold the target run's rounds in stream order: assistant (+ its tool results),
    # then any active-run note, mirroring how react_loop mutates ``messages``.
    active_run: str | None = None
    for entry in entries:
        kind = entry.get("kind") or ""
        payload = entry.get("payload") or {}
        if kind == FactKind.ROUND_BOUNDARY.value:
            active_run = payload.get("run_id") or ""
        elif kind == FactKind.LLM_CALL.value:
            if payload.get("run_id") != target:
                continue
            tool_calls = payload.get("tool_calls") or []
            if not tool_calls:
                # A no-tool round is the turn's final answer (the loop returns it),
                # not part of the window the next round would have seen.
                continue
            window.append(
                LLMMessage(
                    role="assistant",
                    content=payload.get("content") or None,
                    tool_calls=[
                        ToolCall(
                            id=tc.get("id") or "",
                            type=tc.get("type") or "function",
                            function=ToolCallFunction(
                                name=(tc.get("function") or {}).get("name") or "",
                                arguments=(tc.get("function") or {}).get("arguments") or "",
                            ),
                        )
                        for tc in tool_calls
                    ],
                    reasoning_content=payload.get("reasoning_content") or None,
                )
            )
            for tc in tool_calls:
                tcid = tc.get("id") or ""
                # Append a tool message ONLY when the call actually completed (a
                # ``tool_use_end`` fact exists). The pause itself happens INSIDE the
                # suspended call (``ask_user`` / ``delegate``): it emitted ``tool_use_
                # start`` but no ``tool_use_end``, and the live transcript ends at the
                # assistant message with the result still pending (resume appends it).
                # So a missing result means "suspended / in-flight", NOT "empty result"
                # — keying on presence keeps the window == the paused transcript.
                if tcid in tool_results:
                    window.append(
                        LLMMessage(
                            role="tool",
                            content=tool_results[tcid],
                            tool_call_id=tcid,
                        )
                    )
        elif kind == FactKind.NOTE.value:
            # Attribute by the note's OWN run_id (边界② cleared), so a captain note
            # injected while a delegated worker is the active run still folds into the
            # captain window. Fall back to the active run for a note that carries no
            # run_id (a degenerate / pre-Phase-2 stream).
            note_run = payload.get("run_id") or active_run
            if note_run == target:
                window.append(
                    LLMMessage(
                        role=payload.get("role") or "user",
                        content=payload.get("content") or "",
                    )
                )
    if not window:
        return None
    return window


def completed_from_journal(
    entries: list[dict[str, Any]] | None,
) -> dict[str, RunState]:
    """Project the journal's worker run-final facts into the scheduler seed map (resume).

    The execution counterpart of ``frame.completed`` (执行级事件溯源 Phase 2 ⑥): every
    terminal worker recorded a ``message_final`` fact whose payload IS its seed
    :class:`RunState` (``serialize.run_final_fact`` → ``state_to_json``, tagged by the
    ``phase`` key). Fold them back keyed by ``run_id`` — with the SAME deserializer
    (``state_from_json``), so the projection is byte-for-byte the blob the frame stored
    (the conformance golden gates this ``==``) — so a resume re-seeds finished nodes from
    facts and bills the whole plan once, no旁路 frame.

    Last write per ``run_id`` wins (a retried / revised run supersedes). The captain's own
    ``message_final`` (content/reasoning, no ``phase``) is NOT a seed and is skipped, as is
    a display-only journal with no run-final facts (→ ``{}``).
    """
    if not entries:
        return {}
    from agentcore.runtime.runs.serialize import state_from_json

    completed: dict[str, RunState] = {}
    for entry in entries:
        if (entry.get("kind") or "") != FactKind.MESSAGE_FINAL.value:
            continue
        payload = entry.get("payload") or {}
        run_id = payload.get("run_id")
        # ``phase`` presence is the RunState-head discriminator: a worker run-final carries
        # the full seed shape, the captain's plain message_final does not.
        if run_id and "phase" in payload:
            completed[run_id] = state_from_json(payload)
    return completed


def plan_from_journal(entries: list[dict[str, Any]] | None) -> RunPlan | None:
    """Project the journal's ``plan_snapshot`` facts into the delegate's DAG (resume).

    The execution counterpart of ``frame.plan`` (执行级事件溯源 Phase 2, its exit): the
    delegate recorded a ``plan_snapshot`` fact (``serialize.plan_snapshot_fact`` →
    ``plan_to_json``) at plan build and after each ``adjust`` steer. Fold back the LAST one
    — last-write-wins, so the accumulated steer + any post-build mutation is reflected —
    with the SAME deserializer (``plan_from_json``), so the projection is byte-for-byte the
    graph the frame stored (the conformance golden gates this ``==``). A resume thus rebuilds
    the EXACT plan (its already-minted run_ids matching the ``completed_from_journal`` seed)
    and re-drives the unfinished tail, no旁路 frame.

    Returns ``None`` when no ``plan_snapshot`` fact is present (a display-only journal,
    or a non-delegate turn) — the caller falls back to the in-memory carrier.
    """
    if not entries:
        return None
    from agentcore.runtime.runs.serialize import plan_from_json

    latest: dict[str, Any] | None = None
    for entry in entries:
        if (entry.get("kind") or "") == FactKind.PLAN_SNAPSHOT.value:
            latest = entry.get("payload") or {}
    return plan_from_json(latest) if latest is not None else None


def execution_id_from_journal(
    journal_entries: list[dict[str, Any]] | None = None,
    display_journal: list[dict[str, Any]] | None = None,
) -> str | None:
    """Recover the pause-turn ``execution_id`` from a ``run_plan`` journal entry (resume).

    Resume settles mint a fresh pipeline ``execution_id``; re-emitting ``run_plan`` under
    that new id makes the frontend ``ingestPlan`` treat it as a different plan and reset
    frames (wiping plan_review graph history). Prefer the id already on the pause journal.

    Accepts both streams the suspension carries:
    - display ``journal`` entries: ``{"type": "run_plan", "payload": {...}}``
    - fact ``journal_entries``: ``{"kind": "run_plan", "payload": {...}}`` (forwarded
      display facts keep the SSE kind string)

    Last non-empty ``payload.execution_id`` wins (same last-write posture as
    ``plan_from_journal``). Facts are preferred over the display journal when both yield
    an id (facts are the persist source on claim).
    """
    run_plan = EventType.RUN_PLAN.value

    def _scan(entries: list[dict[str, Any]] | None) -> str | None:
        if not entries:
            return None
        found: str | None = None
        for entry in entries:
            label = entry.get("kind") or entry.get("type") or ""
            if label != run_plan:
                continue
            eid = (entry.get("payload") or {}).get("execution_id")
            if isinstance(eid, str) and eid:
                found = eid
        return found

    return _scan(journal_entries) or _scan(display_journal)
