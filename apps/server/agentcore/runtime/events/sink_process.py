"""Process / run_process timeline accumulation for EventSink.

Split from ``sink.py`` — pure move. Live SSE emit and DURABLE journal persist stay
on the sink; this mixin folds thinking / content / tool / marker steps so live,
reload, and the conformance oracle share one projection.
"""

from __future__ import annotations

import copy
from typing import Any

from agentcore.runtime.events.journal_config import cap_process_result
from agentcore.runtime.events.process_persist import (
    ProcessPersistCursor,
    should_persist_on_close,
)
from agentcore.runtime.events.types import EventType, SSEEvent

# Orchestration tools hand the turn to a sub-team and open a team execution. Their
# captain-level call is NOT rendered as a tool step — the `team` marker (emitted at
# run_plan) stands in its place as the collaboration graph's timeline slot. Mirrors
# the TS SSOT `@agentcore/protocol-fold-kit` (desktop/mobile consume that; keep Python
# twin in lockstep). Shared with the conformance oracle (projection.py) so live +
# golden agree.
ORCHESTRATION_TOOLS = frozenset({"delegate", "debate"})

# CEO self-calls whose inline-timeline slot is stood in for by a DEDICATED marker, so
# they make NO captain tool step: delegate/debate → `team` (at run_plan); ask_user →
# `checkpoint` (at checkpoint_required). Superset of ORCHESTRATION_TOOLS
# (which stays scoped to team/graph semantics). ask_user belongs here because a blocking
# ask SUSPENDs without a tool_use_end (its card marker represents it), and a rejected ask
# (card-shape validation) must not leak a red tool-error row — the model self-corrects and
# re-asks. Mirrors `@agentcore/protocol-fold-kit` + oracle (projection.py); keep lockstep.
MARKER_STANDIN_TOOLS = ORCHESTRATION_TOOLS | frozenset({"ask_user"})


def _step_has_marker(steps: list[dict[str, Any]], kind: str, key: str, value: str) -> bool:
    return any(s.get("kind") == kind and s.get(key) == value for s in steps)


def _insert_marker_step(
    steps: list[dict[str, Any]],
    marker: dict[str, Any],
    *,
    before_last_team: bool = False,
) -> None:
    """Insert a positional marker into ``steps`` (caller owns dedup).

    ``before_last_team`` mirrors team_preview product narrative: 开工卡 sits just
    before the collaboration-graph ``team`` marker, not after it.
    """
    if before_last_team:
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].get("kind") == "team":
                steps.insert(i, marker)
                return
    steps.append(marker)


def _marker_spec_for_required(
    event_type: EventType | str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool] | None:
    """Build (marker_step, before_last_team) for a timeline-marker surface event.

    Covers ``*_required`` / ask / raised ``run_escalation`` (统一时间线二期). Shared by
    ``EventSink._accumulate_process`` and suspension capture (G7) so live emit and
    ``turn_paused`` snapshot stay lockstep. Returns None when the event is not a
    marker surface or the id is empty.
    """
    t = event_type if isinstance(event_type, EventType) else EventType(event_type)
    if t == EventType.CHECKPOINT_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "checkpoint", "checkpoint_id": cid}, False
    if t == EventType.PLAN_REVIEW_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "plan_review", "checkpoint_id": cid}, False
    if t == EventType.TEAM_PREVIEW_REQUIRED:
        cid = payload.get("checkpoint_id") or ""
        if not cid:
            return None
        return {"kind": "team_preview", "checkpoint_id": cid}, True
    if t in (EventType.ESCALATION_REQUIRED, EventType.RUN_ESCALATION):
        eid = payload.get("escalation_id") or ""
        if not eid:
            return None
        return {"kind": "escalation", "escalation_id": eid}, False
    if t == EventType.APPROVAL_REQUIRED:
        aid = payload.get("approval_id") or ""
        if not aid:
            return None
        return {"kind": "approval", "approval_id": aid}, False
    if t == EventType.STAGE_CARD_REQUIRED:
        sid = payload.get("stage_card_id") or ""
        if not sid:
            return None
        return {"kind": "stage_card", "stage_card_id": sid}, False
    return None


def synthesize_required_marker(
    steps: list[dict[str, Any]],
    event_type: EventType | str,
    payload: dict[str, Any],
) -> bool:
    """Synthesize a marker step into ``steps`` from a ``*_required`` event (G7).

    Dedups within ``steps`` (``_has_marker`` semantics on the target list). Returns
    whether a marker was inserted. Capture side uses this on the live process lane
    (then flushes to journal) so the pause-anchor marker lands even though the
    required event emits *after* ``persist_suspension_capture``.
    """
    spec = _marker_spec_for_required(event_type, payload)
    if spec is None:
        return False
    marker, before_last_team = spec
    kind = marker["kind"]
    key = next(k for k in marker if k != "kind")
    value = marker[key]
    if _step_has_marker(steps, kind, key, value):
        return False
    _insert_marker_step(steps, marker, before_last_team=before_last_team)
    return True


class SinkProcessMixin:
    """Captain + per-run ProcessStep[] accumulation (seeded ⊕ live)."""

    _process: list[dict[str, Any]]
    _seeded_process: list[dict[str, Any]]
    _run_processes: dict[str, list[dict[str, Any]]]
    _seeded_run_processes: dict[str, list[dict[str, Any]]]
    _process_cursor: ProcessPersistCursor
    _interrupt_content_stash: str | None

    def _has_marker(self, kind: str, key: str, value: str) -> bool:
        """Whether a positional marker step (team / checkpoint / ask / plan_review) for
        ``value`` is already in the timeline — keeps a replayed / multi-batch event from
        dropping a duplicate anchor. Scans seeded ⊕ live so resume-seeded anchors dedup."""
        return _step_has_marker(self._seeded_process, kind, key, value) or _step_has_marker(
            self._process, kind, key, value
        )

    def _run_process(self, run_id: str) -> list[dict[str, Any]]:
        return self._run_processes.setdefault(run_id, [])

    def _persist_closed_captain_text(self) -> list[Any]:
        """Journal the open captain text step that a boundary is about to close."""
        merged = self.raw_process()
        if not merged or not should_persist_on_close(merged[-1]):
            return []
        return self._process_cursor.persist_captain_range(merged, start=0, end=len(merged))

    def _persist_closed_run_text(self, run_id: str) -> list[Any]:
        steps = self._run_process(run_id)
        seeded = self._seeded_run_processes.get(run_id) or []
        merged = [*seeded, *steps] if seeded else list(steps)
        if not merged or not should_persist_on_close(merged[-1]):
            return []
        return self._process_cursor.persist_run_range(run_id, merged, start=0, end=len(merged))

    def flush_process_to_journal(self) -> None:
        """Persist every not-yet-journaled process / run_process step (finalize / pause).

        Call at semantic turn boundaries so open trailing text steps and markers land
        before ``turn_end`` / ``turn_paused``. Idempotent via the ordinal cursor.
        """
        self._process_cursor.persist_new_captain_tail(self.raw_process())
        for rid, steps in self._merged_run_processes().items():
            self._process_cursor.persist_new_run_tail(rid, steps)

    def _persist_captain_marker_after_insert(
        self,
        marker: dict[str, Any],
        *,
        before_last_team: bool,
    ) -> list[Any]:
        """Journal a newly inserted captain marker (append or ``before_last_team``).

        Ordinal tail persist covers the common append case. Two compensations:

        - Mid-insert behind the cursor (``team`` already journaled at ``run_plan``,
          then 开工卡 / 授权 inserts before it): schedule the marker alone and advance
          the cursor by one so the shifted tail is not re-journaled.
        - Open tool ahead of the marker holds the cursor (SUSPEND ``ask_user``): schedule
          the marker and seed past the lane.
        """
        from agentcore.runtime.events.process_persist import schedule_process_step

        merged = self.raw_process()
        kind = marker["kind"]
        key = next(k for k in marker if k != "kind")
        marker_idx = next(
            (
                i
                for i, step in enumerate(merged)
                if step.get("kind") == kind and step.get(key) == marker[key]
            ),
            None,
        )
        futures: list[Any] = []
        if (
            before_last_team
            and marker_idx is not None
            and marker_idx < self._process_cursor.captain
        ):
            fut = schedule_process_step(marker)
            if fut is not None:
                futures.append(fut)
            self._process_cursor.seed_captain(self._process_cursor.captain + 1)
        futures.extend(self._process_cursor.persist_new_captain_tail(merged))
        # Open tool ahead of the marker holds the ordinal cursor — compensate.
        if marker_idx is not None and self._process_cursor.captain <= marker_idx:
            fut = schedule_process_step(marker)
            if fut is not None:
                futures.append(fut)
            self._process_cursor.seed_captain(len(merged))
        return futures

    def persist_required_marker(self, event_type: Any, payload: dict[str, Any]) -> None:
        """Insert a pause-anchor marker into the live captain lane and journal it.

        The ``*_required`` SSE is emitted *after* suspension capture, so the capture
        path must synthesize the marker itself for process_* progressive persistence.

        Mirrors the live SSE ``*_required`` accumulate path (close open text → insert
        → ordinal tail persist) so ``before_last_team`` markers keep live order in
        ``process_*`` (开工卡 before ``team``). When an open tool ahead of the marker
        holds the cursor (SUSPEND ``ask_user`` never emits ``tool_use_end`` before
        pause), fall back to scheduling the marker fact + seeding the cursor past
        the lane — same compensation the old always-``seed_captain(len)`` path used,
        but only when ordinal persist could not reach the marker.
        """
        spec = _marker_spec_for_required(event_type, payload)
        if spec is None:
            return
        marker, before_last_team = spec
        kind = marker["kind"]
        key = next(k for k in marker if k != "kind")
        if self._has_marker(kind, key, marker[key]):
            return

        # before_last_team: insert BEFORE closing/persisting so a trailing text step
        # cannot pin ``team`` ahead of the marker in process_* order. Append markers
        # still close open text first (live SSE parity).
        if not before_last_team:
            self._persist_closed_captain_text()
        if not synthesize_required_marker(self._process, event_type, payload):
            return

        self._persist_captain_marker_after_insert(
            marker, before_last_team=before_last_team
        )

    def _accumulate_run_process(self, event: SSEEvent) -> list[Any]:
        """Accumulate a worker run's ProcessStep[] (mirrors captain ``_accumulate_process``)."""
        futures: list[Any] = []
        t = event.type
        payload = event.payload
        if t == EventType.RUN_REASONING_DELTA:
            run_id = payload.get("run_id") or ""
            delta = payload.get("delta") or ""
            if not run_id or not delta:
                return futures
            steps = self._run_process(run_id)
            if steps and steps[-1].get("kind") == "reasoning":
                steps[-1]["text"] += delta
            else:
                if steps and should_persist_on_close(steps[-1]):
                    futures.extend(self._persist_closed_run_text(run_id))
                steps.append({"kind": "reasoning", "text": delta})
        elif t == EventType.RUN_OUTPUT_DELTA:
            run_id = payload.get("run_id") or ""
            delta = payload.get("delta") or ""
            if not run_id or not delta:
                return futures
            steps = self._run_process(run_id)
            if steps and steps[-1].get("kind") == "content":
                steps[-1]["text"] += delta
            else:
                if steps and should_persist_on_close(steps[-1]):
                    futures.extend(self._persist_closed_run_text(run_id))
                steps.append({"kind": "content", "text": delta})
        elif t == EventType.RUN_OUTPUT_RESET:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            steps = self._run_process(run_id)
            # Discard open (unpersisted) trailing content — do not journal it.
            while steps and steps[-1].get("kind") == "content":
                steps.pop()
            # Only 交付前核验回炉 leaves the persisted「已按交付规范重写」trace; every
            # other reason (retry / narration / …) clears the draft without a chip.
            if payload.get("reason") == "finish_guard":
                steps.append({"kind": "rework"})
                seeded = self._seeded_run_processes.get(run_id) or []
                merged = [*seeded, *steps] if seeded else list(steps)
                futures.extend(self._process_cursor.persist_new_run_tail(run_id, merged))
        elif t == EventType.TOOL_USE_START:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            futures.extend(self._persist_closed_run_text(run_id))
            self._run_process(run_id).append(
                {
                    "kind": "tool",
                    "id": payload.get("tool_call_id", ""),
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments") or {},
                    "result": None,
                    "status": "running",
                }
            )
        elif t == EventType.TOOL_USE_END:
            run_id = payload.get("run_id") or ""
            if not run_id:
                return futures
            call_id = payload.get("tool_call_id", "")
            result = cap_process_result(payload.get("result"))
            display = payload.get("display")
            failure = payload.get("failure")
            for step in reversed(self._run_process(run_id)):
                if step.get("kind") == "tool" and step.get("id") == call_id:
                    step["result"] = result
                    step["status"] = payload.get("status", "success")
                    if display is not None:
                        step["display"] = display
                    if failure is not None:
                        step["failure"] = failure
                    break
            seeded = self._seeded_run_processes.get(run_id) or []
            live = self._run_process(run_id)
            merged = [*seeded, *live] if seeded else list(live)
            # Terminal tool persist (holds open tools on flush; compensates if cursor
            # already advanced past a stale running row).
            futures.extend(
                self._process_cursor.persist_resolved_run_tool(run_id, merged, call_id)
            )
        return futures

    def _accumulate_process(self, event: SSEEvent) -> list[Any]:
        # Worker-scoped deltas / tools accumulate on the per-run lane first (then the
        # captain branch no-ops them via run_id / event-type guards below).
        futures = self._accumulate_run_process(event)
        t = event.type
        if t == EventType.RUN_PLAN:
            # 旧 divert 生长帧带 host_message_id：不在新回合插 team（锚点曾由 graph_append
            # 承担）。新路径每回合新图，无 host_message_id，正常插 team。
            if event.payload.get("host_message_id"):
                return futures
            # 协作图时间线落点 (统一团队时间线): the FIRST run_plan of an execution drops a
            # zero-width `team` marker at its chronological spot, so the inline graph renders
            # there rather than at the bottom. Later batches (same execution_id) merge into the
            # same graph — one marker per execution.
            execution_id = event.payload.get("execution_id") or ""
            if execution_id and not self._has_marker("team", "execution_id", execution_id):
                futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "team", "execution_id": execution_id})
                # Same as other timeline markers: journal at insert so mid-run reload
                # (workers still running, no captain flush yet) replays ``team``.
                futures.extend(
                    self._process_cursor.persist_new_captain_tail(self.raw_process())
                )
        elif t == EventType.USER_INTERJECTION:
            # 用户插话时间线落点: 同 interjection_id 首次出现（received）钉零宽 marker，
            # 正文与五态仍由旁路 userInterjections 按 id 查；后续 injected/addressed/
            # queued/failed 只更新旁路，不重复落标记。打断 content 尾部合并是预期红利。
            iid = event.payload.get("interjection_id") or ""
            if iid and not self._has_marker("user_interjection", "interjection_id", iid):
                futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "user_interjection", "interjection_id": iid})
                futures.extend(
                    self._process_cursor.persist_new_captain_tail(self.raw_process())
                )
        elif t == EventType.GRAPH_APPEND:
            # 已停发：仅兼容旧 journal 回放。
            futures.extend(self._persist_closed_captain_text())
            self._process.append(
                {
                    "kind": "graph_append",
                    "execution_id": event.payload.get("execution_id") or "",
                    "host_message_id": event.payload.get("host_message_id") or "",
                    "added_count": int(event.payload.get("added_count") or 0),
                }
            )
        elif t == EventType.REASONING_DELTA:
            delta = event.payload.get("delta") or ""
            if not delta:
                return futures
            if self._process and self._process[-1].get("kind") == "reasoning":
                self._process[-1]["text"] += delta
            else:
                if self._process and should_persist_on_close(self._process[-1]):
                    futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "reasoning", "text": delta})
        elif t == EventType.CONTENT_DELTA:
            delta = event.payload.get("delta") or ""
            if not delta:
                return futures
            # Live rewrite superseded the pre-reset draft — drop interrupt stash.
            self._interrupt_content_stash = None
            if self._process and self._process[-1].get("kind") == "content":
                self._process[-1]["text"] += delta
            else:
                if self._process and should_persist_on_close(self._process[-1]):
                    futures.extend(self._persist_closed_captain_text())
                self._process.append({"kind": "content", "text": delta})
        elif t == EventType.CONTENT_RESET:
            # Discard open (unpersisted) trailing content — do not journal discarded prose.
            # Stash discarded text for /stop salvage (empty discard keeps prior stash).
            trailing: list[str] = []
            i = len(self._process) - 1
            while i >= 0 and self._process[i].get("kind") == "content":
                trailing.append(self._process[i].get("text", "") or "")
                i -= 1
            discarded = "".join(reversed(trailing))
            if discarded:
                self._interrupt_content_stash = discarded
            while self._process and self._process[-1].get("kind") == "content":
                self._process.pop()
        elif t == EventType.TOOL_USE_START:
            payload = event.payload
            # Skip a delegated worker's call (run-scoped — belongs to its run node, not the
            # captain timeline) and a marker-standin call (delegate/debate → `team`;
            # ask_user → `checkpoint`/`ask`). Neither becomes a captain tool step.
            if payload.get("run_id") or payload.get("tool_name") in MARKER_STANDIN_TOOLS:
                return futures
            futures.extend(self._persist_closed_captain_text())
            self._process.append(
                {
                    "kind": "tool",
                    "id": payload.get("tool_call_id", ""),
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments") or {},
                    "result": None,
                    "status": "running",
                }
            )
        elif t == EventType.TOOL_USE_END:
            payload = event.payload
            if payload.get("run_id") or payload.get("tool_name") in MARKER_STANDIN_TOOLS:
                return futures
            call_id = payload.get("tool_call_id", "")
            result = cap_process_result(payload.get("result"))
            display = payload.get("display")
            failure = payload.get("failure")
            for step in reversed(self._process):
                if step.get("kind") == "tool" and step.get("id") == call_id:
                    step["result"] = result
                    step["status"] = payload.get("status", "success")
                    if display is not None:
                        step["display"] = display
                    if failure is not None:
                        step["failure"] = failure
                    break
            # Terminal tool persist (holds open tools on flush; compensates if cursor
            # already advanced past a stale running row).
            futures.extend(
                self._process_cursor.persist_resolved_captain_tool(
                    self.raw_process(), call_id
                )
            )
        elif t in (
            EventType.CHECKPOINT_REQUIRED,
            EventType.PLAN_REVIEW_REQUIRED,
            EventType.TEAM_PREVIEW_REQUIRED,
            EventType.ESCALATION_REQUIRED,
            EventType.RUN_ESCALATION,
            EventType.APPROVAL_REQUIRED,
            EventType.STAGE_CARD_REQUIRED,
        ):
            # Positional card / 痕迹 anchors — shared builder with synthesize_required_marker
            # (G7). Dedup scans seeded⊕live; insert targets live only.
            spec = _marker_spec_for_required(t, event.payload)
            if spec is None:
                return futures
            marker, before_last_team = spec
            kind = marker["kind"]
            key = next(k for k in marker if k != "kind")
            if self._has_marker(kind, key, marker[key]):
                return futures
            futures.extend(self._persist_closed_captain_text())
            _insert_marker_step(self._process, marker, before_last_team=before_last_team)
            # Middle-insert (before_last_team) after an already-journaled ``team`` needs
            # cursor compensation — shared with persist_required_marker.
            futures.extend(
                self._persist_captain_marker_after_insert(
                    marker, before_last_team=before_last_team
                )
            )
        return futures

    def seed_process(self, steps: list[dict[str, Any]]) -> None:
        """Hydrate resume captain timeline into the seeded zone (G1/G7). Deep-copied."""
        self._seeded_process = copy.deepcopy(list(steps))
        # Seeded steps already lived in journal — skip re-append on flush.
        self._process_cursor.seed_captain(len(self._seeded_process))

    def seed_run_processes(self, run_map: dict[str, list[dict[str, Any]]]) -> None:
        """Hydrate resume worker timelines into the seeded zone (G1/G7). Deep-copied."""
        self._seeded_run_processes = {
            rid: copy.deepcopy(list(steps)) for rid, steps in run_map.items()
        }
        for rid, steps in self._seeded_run_processes.items():
            self._process_cursor.seed_run(rid, len(steps))

    def raw_process(self) -> list[dict[str, Any]]:
        """Seeded ⊕ live captain steps with **no** structural gate (G1 capture).

        Unlike ``process_timeline()``, a pure prose turn still returns its steps —
        suspension snapshots must not drop pre-pause content/reasoning.
        """
        if not self._seeded_process:
            return list(self._process)
        return [*self._seeded_process, *self._process]

    def raw_run_processes(self) -> dict[str, list[dict[str, Any]]]:
        """Seeded ⊕ live worker step maps with **no** empty-map gate (G1 capture)."""
        return self._merged_run_processes()

    def _merged_run_processes(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for rid, steps in self._seeded_run_processes.items():
            if steps:
                out[rid] = list(steps)
        for rid, steps in self._run_processes.items():
            if not steps:
                continue
            prior = out.get(rid)
            out[rid] = [*prior, *steps] if prior else list(steps)
        return out

    def process_timeline(self) -> list[dict[str, Any]] | None:
        # Persist the timeline whenever it carries STRUCTURE beyond the CEO's own text —
        # a tool, the team graph, or an interaction / 痕迹 marker (checkpoint / ask /
        # plan_review / team_preview / escalation / approval /
        # user_interjection).
        # A pure reasoning/content turn needs none (the content scalar IS the answer, and
        # reasoning rides its own column), matching the fold's "tool-less single-agent turn
        # → no process" so live / reload / golden stay aligned.
        # Gate scans seeded⊕live; unseeded path returns the live list by reference
        # (status-quo identity for callers that mutate / compare).
        if self._seeded_process:
            merged = [*self._seeded_process, *self._process]
            structural = any(s.get("kind") not in ("reasoning", "content") for s in merged)
            return merged if structural else None
        structural = any(s.get("kind") not in ("reasoning", "content") for s in self._process)
        return self._process if structural else None

    def run_process_timelines(self) -> dict[str, list[dict[str, Any]]] | None:
        """Per-run ProcessStep[] maps for worker detail timelines (对称 CEO process).

        Persist any non-empty run timeline so reload keeps true interleaving (tools
        between thinking/output). Empty map → None (no field on the runs payload).
        When seeded, returns seeded⊕live merge; otherwise the live map only (status quo).
        """
        if self._seeded_run_processes:
            out = self._merged_run_processes()
            return out or None
        out = {rid: steps for rid, steps in self._run_processes.items() if steps}
        return out or None

    def streamed_content(self) -> str:
        """The CEO bubble's currently-streamed text — concatenated ``content``-kind
        process entries, honoring ``content_reset`` (reset pops them).

        断线别白干 (中途取消 salvage): the partial reply the user already saw, read off the
        turn's live accumulation so a turn CANCELLED before it persisted keeps that text
        instead of being replaced by a generic「连接中断」note. Empty for a turn that had
        streamed no assistant text yet (e.g. still mid-tool). Accumulates even while
        detached, so a disconnect that later cancels still recovers what streamed.

        Live zone only — seeded pre-pause content must not re-enter salvage joins (G8).
        After ``content_reset`` this is empty until the next delta; use
        :meth:`interrupt_salvage_content` for stop-after-reset salvage.
        """
        return "".join(
            step.get("text", "") for step in self._process if step.get("kind") == "content"
        )

    def interrupt_salvage_content(self) -> str:
        """Body to keep on user stop: live streamed text, else pre-reset stash.

        ``content_reset`` / finish_guard clears the live content lane so the bubble
        can rewrite; if the user stops before a new delta, industry habit is to keep
        whatever already streamed — not an empty shell.
        """
        live = self.streamed_content()
        if live:
            return live
        return self._interrupt_content_stash or ""

    def streamed_reasoning(self) -> str:
        """CEO thinking text accumulated so far (live ``reasoning`` steps only)."""
        return "".join(
            step.get("text", "") for step in self._process if step.get("kind") == "reasoning"
        )
