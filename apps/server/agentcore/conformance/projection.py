"""The ProjectedTurn oracle: fold an SSE event vector → the normalized judge state.

This is the backend-authoritative twin of the frontend folds (mobile
``src/protocol/fold.ts``; desktop ``stores/execution.ts`` + ``streamConversation``).
Its output IS the golden every端 must match (前端技术与架构 §十 SSE 与协议一致性).

Semantics are deliberately a port of the two PROVEN frontend/runtime projections, so
the oracle never invents behavior the product doesn't already have:

- the multi-agent team graph (agents / runs / progress) mirrors desktop
  ``projectExecution`` (run_plan skeleton → run_* frames fold in; progress derived
  from run states; revisions synthesized from their run_started frame);
- the 思考·正文·工具·协作 ``process`` timeline mirrors ``EventSink._accumulate_process``
  (reasoning/content deltas coalesce; one step per captain tool call resolved by its
  tool_use_end; zero-width positional markers — ``team`` at run_plan, ``checkpoint`` /
  ``ask`` / ``plan_review`` at their *_required — fix where the graph / interaction
  cards render in chronological order; orchestration tool steps are dropped, the
  ``team`` marker stands in), carried for single-agent AND multi-agent turns (统一团队
  时间线 — the CEO's own steps), parity with ``process_timeline()`` (which only goes
  None for a turn with no structural step);
- ``content`` / ``reasoning`` accumulate the captain bubble's deltas (present even in
  a multi-agent turn — the CEO speaks above the graph); a delta flagged ``replace``
  carries a WHOLE open block (attach 回放段) and swaps the channel's tail block instead
  of appending — same rule on the per-run ``run_output_delta`` / ``run_reasoning_delta``;
- ``status`` / ``interactions[]`` fold the gate state machine (a gate *_required pauses,
  its *_resolved resumes when no gate remains pending; a paused turn's stream may end
  at the *_required). Full interaction lifecycle (pending|resolved|orphaned) is projected
  via :func:`fold_interactions` (runtime journal fold — single implementation);
- ``cost`` / ``finishReason`` come from message_end (回合总账);
  ``outcome`` is ``message_end.outcome`` or the batch-bit aggregate
  (``delivery_status=partial`` / ``product_landed`` / ``partial_failure``).

Output keys are the camelCase ProjectedTurn shape (see
``packages/protocol-conformance/src/projectedTurn.ts``); wire-shaped leaves
(usage / cost / tool arguments / process step) are carried verbatim (snake_case kept).
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime.engine.tool_channel_redirect import process_tool_status_from_end
from agentcore.runtime.events.journal_config import cap_process_result
from agentcore.runtime.events.sink import MARKER_STANDIN_TOOLS
from agentcore.runtime.interaction import GATE_KINDS
from agentcore.runtime.journal.pending_interactions import (
    fold_interactions,
    project_interaction_leaf,
)
from agentcore.runtime.turn.outcome import resolve_turn_outcome

# message_end.finish_reason → terminal TurnStatus (parity with TS
# `@agentcore/protocol-fold-kit` turnStatusFromFinish / FINISH_TO_STATUS).
_FINISH_TO_STATUS: dict[str, str] = {
    "end_turn": "completed",
    "max_rounds": "completed",
    "degraded": "completed",
    "unproductive": "completed",
    "error": "failed",
    "cancelled": "cancelled",
    # Crash / lease-sweeper salvage (流式回复持久化 §3.4 / P4): incomplete turn kept as
    # cancelled-class terminal so the bubble offers retry, not a completed chip.
    "interrupted": "cancelled",
    # 挂起即收口 (②): a turn that ended AT a durable checkpoint (ask_user blocking /
    # plan_review) finalizes with finish_reason=paused — the stream carries a terminal
    # message_end yet the turn is NOT done. It must STAY paused (gate *_required already
    # parked interactions[]; message_end only adds finishReason + cost), so the resume
    # card renders, NOT a completed bubble. Without this the trailing message_end would
    # fall through to "completed" and erase the pause.
    "paused": "paused",
}


def _agent_from_plan(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id", ""),
        "role": a.get("role", ""),
        "thinking": bool(a.get("thinking", True)),
        "status": "idle",
        "currentRunId": None,
        "output": "",
        "reasoning": "",
        "toolProgress": None,
    }


def _act_from_plan(p: dict[str, Any]) -> dict[str, Any]:
    """Resolve the act declaration for a ``run_plan`` payload.

    Wire ``act`` present → use it. Absent (old journal / old vectors) → synthesize a
    single act (``act-1``, kind = ``plan_type`` when it is a first-class act kind).
    """
    raw = p.get("act")
    if isinstance(raw, dict) and raw.get("act_id"):
        kind = raw.get("kind") or "multi_agent"
        if kind not in ("multi_agent", "debate"):
            kind = "multi_agent"
        auth = raw.get("authorized_by")
        if auth not in ("stage_card", "auto", "preview"):
            auth = None
        return {
            "actId": str(raw["act_id"]),
            "kind": kind,
            "title": raw.get("title"),
            "anchorRunId": raw.get("anchor_run_id"),
            "authorizedBy": auth,
        }
    plan_type = p.get("plan_type") or "multi_agent"
    kind = plan_type if plan_type in ("multi_agent", "debate") else "multi_agent"
    return {
        "actId": "act-1",
        "kind": kind,
        "title": None,
        "anchorRunId": None,
        "authorizedBy": None,
    }


def _upsert_act(acts: list[dict[str, Any]], act: dict[str, Any]) -> None:
    for i, existing in enumerate(acts):
        if existing["actId"] == act["actId"]:
            acts[i] = act
            return
    acts.append(act)


def _run_from_plan(s: dict[str, Any], *, act_id: str) -> dict[str, Any]:
    return {
        "id": s.get("id", ""),
        "agentId": s.get("agent_id", ""),
        "task": s.get("task", ""),
        "status": "pending",
        "dependsOn": list(s.get("depends_on") or []),
        "outputSummary": None,
        # 完工交接简报: the worker's authored {summary/key_points/assumptions/next_steps},
        # set by run_completed; None until then (辩手 / trivial worker / captain carry none).
        "debrief": None,
        "durationMs": None,
        "error": None,
        "failureKind": None,
        "productLanded": None,
        "parentRunId": s.get("parent_run_id"),
        "kind": s.get("kind") or "agent",
        "role": None,
        "model": None,
        "usage": None,
        "cost": None,
        "stance": s.get("stance"),
        "group": s.get("group"),
        "round": s.get("round") or 0,
        "continuesRunId": None,
        # 「计划已调整」轻痕迹 (设计 §7.2): set by the plan_revised fact to "bind"/"steer" when
        # the CEO autonomously re-bound / re-steered this node mid-flight; None otherwise.
        "revised": None,
        # 回落换人: set from run_plan.replaces_run_id when CEO re-delegates after continue miss.
        "replacesRunId": s.get("replaces_run_id"),
        # 幕归属：该 run_plan 声明的幕（旧事件无 act → 合成 act-1）。
        "actId": act_id,
        "checkpoint": None,
        # 收到的上下文 (上下文传递可视化): filled by the run_context fact; empty until then.
        "receivedContext": [],
        # 升级实时可见: appended by the run_escalation fact; empty until a worker escalates.
        "escalations": [],
        # Per-run 思考·正文·工具 timeline (对称 CEO process); empty until deltas/tools fold in.
        "process": [],
    }


def project_turn(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold an ordered SSE event vector into the normalized ProjectedTurn dict."""
    content = ""
    reasoning = ""
    # 收到的上下文 · CEO 侧 (上下文传递可视化): the captain run id (its kind=captain
    # run_started) + the structured opening context it was fed (system/history/request),
    # routed turn-level — the CEO is the bubble above the graph, not a peer node.
    captain_run_id: str | None = None
    captain_context: list[dict[str, Any]] = []
    process: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    evidence_ledger: list[dict[str, Any]] = []
    cited_ids: list[str] = []
    agents: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    # 幕序列（批 A1）：旧 run_plan 无 act → 合成单幕 act-1；无 run_plan 时恒 []。
    acts: list[dict[str, Any]] = []
    plan_id: str | None = None
    finish_reason: str | None = None
    explicit_outcome: str | None = None
    cost: dict[str, Any] | None = None
    saw_error = False
    # Latest SSE ``error`` payload (turn-level face authority when content empty).
    turn_error: dict[str, str] | None = None
    # 跨回合流 vs 同回合 resume：仅 message_id 变化时清空气泡正文（见 message_start）。
    last_message_id: str | None = None
    # 辩论编排收场产物（debate_result）：整段 payload verbatim 折入，与 run_plan 的辩手
    # 节点互补（图承载执行/发言全文，本字段承载决策简报 + 交锋叙事线）。None=本回合无辩论。
    debate: dict[str, Any] | None = None
    # 本场是否开启质询（debate_round_started.cross_exam_enabled）：首轮开场即达；缺字段→False。
    cross_exam_enabled = False
    # 主持人开场白（debate_round_started.opening）：仅首轮携带；sticky 取第一个非空，不被后续覆盖。
    # 收场 debate.opening 仍是权威。缺字段 / 老 journal → None。
    debate_opening: str | None = None
    # 辩论逐轮叙事（debate_round_started / debate_round）：进行中实时叠加，折叠累积按 round_no
    # 升序。P2 DURABLE——落 journal，刷新后 hydrate/fold 重建；收场后全量叙事线亦在 debate。
    debate_rounds: list[dict[str, Any]] = []
    # 庭前取证（§二之二）：started/orders/progress → completed 权威覆盖。
    debate_pretrial: dict[str, Any] | None = None
    # 协调模式团队进展预览（team_synthesis_preview）：同 key 保最新。P2 DURABLE。
    team_synthesis_preview: dict[str, Any] | None = None
    # 交付状态（delivery_status，能力闸门与交付诚实性）：同 execution_id 保最新。DURABLE。
    delivery_status: dict[str, Any] | None = None
    # 预检警告（turn_warning）：P2 DURABLE。
    turn_warning: str | None = None
    # 裸聊写盘自动建文件夹（auto_folder_created，§5.4 裸聊行）：DURABLE；对话内不再渲染。
    auto_folder: dict[str, Any] | None = None
    # 团队便签墙 (§2.2 通): the batch's posted notes in chronological order. Journaled, so it
    # replays on reload (unlike transport-only board ops). Deduped by noteId for replay safety.
    team_notes: list[dict[str, Any]] = []
    # 墙已升（run_plan.note_wall）：缺省 / 旧 journal = 无墙。Sticky-OR 同 execution；换图重置。
    note_wall = False
    # 运行中用户插话（user_interjection，经典+协调共用）：同 interjection_id 保最新 status
    # （含 injected）。DURABLE。
    user_interjections: list[dict[str, Any]] = []
    _user_interjection_by_id: dict[str, int] = {}
    # plan_review_resolved carries only the checkpoint id → remember the gated run ids.
    checkpoint_steps: dict[str, list[str]] = {}

    def agent_by_id(aid: str) -> dict[str, Any] | None:
        return next((a for a in agents if a["id"] == aid), None)

    def run_by_id(rid: str) -> dict[str, Any] | None:
        return next((r for r in runs if r["id"] == rid), None)

    def has_marker(kind: str, key: str, value: str) -> bool:
        """Whether a positional marker (team / checkpoint / ask / plan_review) for
        ``value`` is already in the timeline (multi-batch / replay dedup)."""
        return any(s.get("kind") == kind and s.get(key) == value for s in process)

    def upsert_round(entry: dict[str, Any]) -> None:
        """Fold one 逐轮叙事 update by ``round_no`` (a later debate_round overwrites the
        focus-only round_started entry — it carries focus too), kept ascending. Mirrors
        the TS folds' ``upsertDebateRound`` (conformance pins them equal)."""
        for i, r in enumerate(debate_rounds):
            if r["round_no"] == entry["round_no"]:
                debate_rounds[i] = entry
                return
        debate_rounds.append(entry)
        debate_rounds.sort(key=lambda r: r["round_no"])

    for ev in events:
        etype = ev.get("type") or ""
        p = ev.get("payload") or {}

        if etype == "message_start":
            # 跨回合流：message_id 变化 = 新助手气泡 → 清空正文/过程时间线；
            # 同 execution_id 的 runs/agents 保留，使第二回合追加帧继续生长同一张协作图。
            # 同 message_id = 挂起恢复重开同一气泡 → 保留已累积正文（pause→resume）。
            # full_replay = attach 回放段段首：服务端明说「这段是本回合全量重放」，
            # 无条件重置本回合流式态再折后续帧——不靠 id 与屏上气泡比对猜（猜错叠正文）。
            mid = str(p.get("message_id") or "")
            new_bubble = last_message_id is None or (mid and mid != last_message_id)
            if p.get("full_replay") or new_bubble:
                content = ""
                reasoning = ""
                process = []
                finish_reason = None
                explicit_outcome = None
                cost = None
                turn_error = None
            if mid:
                last_message_id = mid

        elif etype == "content_delta":
            # replace = 整块帧（attach 回放段专用）：delta 是该通道末尾那个仍开放的文本块的
            # 当前全文，不是增量。末尾确实是开放正文块就整块换掉（标量退掉旧块再接新块），
            # 否则（工具/标记步已把它闭合、或本回合还没正文）当普通新块折。
            delta = p.get("delta") or ""
            tail = process[-1] if process and process[-1].get("kind") == "content" else None
            if p.get("replace") and tail is not None:
                content = content.removesuffix(tail["text"]) + delta
                tail["text"] = delta
            else:
                content += delta
                if delta:
                    if tail is not None:
                        tail["text"] += delta
                    else:
                        process.append({"kind": "content", "text": delta})

        elif etype == "content_reset":
            # 草稿丢弃信号：引擎丢弃已流式的这一版正文、发 content_reset（reason 说明为何）。
            # 该事件进 _history（重连回放重发），故 oracle 必须与三端 fold 一致：清正文标量 +
            # 弹掉 process 尾部连续 content 步（reasoning/tool 是真实过程，保留），让重写版从
            # 干净态重累积——否则会把「违规版+修正版」拼在一起。
            content = ""
            while process and process[-1].get("kind") == "content":
                process.pop()
            # 仅交付前核验回炉 (reason=finish_guard) 折出「已按交付规范重写」轻 chip；
            # 其余 reason（retry / soft_gate / ask_user / …）只清正文、不留痕。
            if p.get("reason") == "finish_guard":
                process.append({"kind": "rework"})

        elif etype == "reasoning_delta":
            delta = p.get("delta") or ""
            tail = process[-1] if process and process[-1].get("kind") == "reasoning" else None
            if p.get("replace") and tail is not None:
                reasoning = reasoning.removesuffix(tail["text"]) + delta
                tail["text"] = delta
            else:
                reasoning += delta
                if delta:
                    if tail is not None:
                        tail["text"] += delta
                    else:
                        process.append({"kind": "reasoning", "text": delta})

        elif etype == "tool_use_start":
            # A delegated worker's call (run-scoped) belongs to its run node, not the
            # captain's inline timeline; an orchestration call (delegate/debate) is
            # represented by the `team` marker (dropped at run_plan), not a tool step.
            # Either way it creates no captain step (统一团队时间线 = the CEO's OWN steps);
            # still clear the run's live toolProgress below.
            rid = p.get("run_id") or ""
            if rid:
                run = run_by_id(rid)
                if run is not None:
                    run["process"].append(
                        {
                            "kind": "tool",
                            "id": p.get("tool_call_id", ""),
                            "tool_name": p.get("tool_name", ""),
                            "arguments": p.get("arguments") or {},
                            "result": None,
                            "status": "running",
                        }
                    )
            elif p.get("tool_name") not in MARKER_STANDIN_TOOLS:
                step: dict[str, Any] = {
                    "kind": "tool",
                    "id": p.get("tool_call_id", ""),
                    "tool_name": p.get("tool_name", ""),
                    "arguments": p.get("arguments") or {},
                    "result": None,
                    "status": "running",
                }
                process.append(step)
            # Multi-agent: attach the executing call to the running run's agent too
            # (desktop attaches tool calls to whichever run is running). Captured on the
            # agent's currentRunId; worker tool fidelity beyond status is a later ratchet.
            running = next((r for r in runs if r["status"] == "running"), None)
            if running:
                ag = agent_by_id(running["agentId"])
                if ag:
                    ag["toolProgress"] = None

        elif etype == "tool_use_end":
            rid = p.get("run_id") or ""
            call_id = p.get("tool_call_id", "")
            result = cap_process_result(p.get("result"))
            display = p.get("display")
            failure = p.get("failure")
            if rid:
                run = run_by_id(rid)
                if run is not None:
                    for step in reversed(run["process"]):
                        if step.get("kind") == "tool" and step.get("id") == call_id:
                            step["result"] = result
                            step["status"] = process_tool_status_from_end(p)
                            if display is not None:
                                step["display"] = display
                            if failure is not None:
                                step["failure"] = failure
                            break
                continue
            if p.get("tool_name") in MARKER_STANDIN_TOOLS:
                continue
            for step in reversed(process):
                if step.get("kind") == "tool" and step.get("id") == call_id:
                    step["result"] = result
                    step["status"] = process_tool_status_from_end(p)
                    if display is not None:
                        step["display"] = display
                    if failure is not None:
                        step["failure"] = failure
                    break

        elif etype == "citations":
            citations = list(p.get("citations") or [])

        elif etype == "evidence_ledger":
            # Turn 级台账通道：entries 权威覆盖；否则 merge delta（按 id）。
            full = p.get("entries")
            if isinstance(full, list):
                evidence_ledger = list(full)
            else:
                for entry in p.get("delta") or []:
                    if not isinstance(entry, dict):
                        continue
                    eid = entry.get("id")
                    if not eid:
                        continue
                    replaced = False
                    for i, existing in enumerate(evidence_ledger):
                        if existing.get("id") == eid:
                            evidence_ledger[i] = entry
                            replaced = True
                            break
                    if not replaced:
                        evidence_ledger.append(entry)
            if "cited_ids" in p and isinstance(p.get("cited_ids"), list):
                cited_ids = [str(x) for x in p["cited_ids"]]

        elif etype == "graph_append":
            # 跨回合同图追加锚点（新回合 process 标记；生长帧带 host_message_id）。
            process.append(
                {
                    "kind": "graph_append",
                    "execution_id": p.get("execution_id") or "",
                    "host_message_id": p.get("host_message_id") or "",
                    "added_count": int(p.get("added_count") or 0),
                }
            )

        elif etype == "run_plan":
            ip = p.get("execution_id")
            act = _act_from_plan(p)
            # 跨回合同图追加：带 host_message_id 的生长 run_plan 不插新 team 标记
            # （锚点由 graph_append 承担；宿主回合已有 team）。
            # 协作图时间线落点 (统一团队时间线): the first run_plan of an execution drops a
            # zero-width `team` marker at its chronological spot (later same-id batches merge
            # into one graph → one marker). Mirrors EventSink._accumulate_process.
            if (
                not p.get("host_message_id")
                and ip
                and not has_marker("team", "execution_id", ip)
            ):
                process.append({"kind": "team", "execution_id": ip})
            if plan_id is not None and plan_id != ip:
                # A different execution id is a fresh plan (desktop resets the slot).
                note_wall = False
            if p.get("note_wall") is True:
                note_wall = True
            if plan_id is None or plan_id == ip:
                plan_id = ip
                _upsert_act(acts, act)
                for a in p.get("agents") or []:
                    if not agent_by_id(a.get("id", "")):
                        agents.append(_agent_from_plan(a))
                for s in p.get("runs") or []:
                    if not run_by_id(s.get("id", "")):
                        runs.append(_run_from_plan(s, act_id=act["actId"]))
            else:
                # A different execution id is a fresh plan (desktop resets the slot).
                plan_id = ip
                acts = [act]
                agents = [_agent_from_plan(a) for a in (p.get("agents") or [])]
                runs = [
                    _run_from_plan(s, act_id=act["actId"]) for s in (p.get("runs") or [])
                ]

        elif etype == "run_started":
            rid = p.get("run_id", "")
            agid = p.get("agent_id", "")
            continues = p.get("continues_run_id")
            parent = p.get("parent_run_id")
            kind = p.get("kind") or "agent"
            # The CEO captain is the turn's root (kind=captain); remember its run id so its
            # run_context routes turn-level. The captain node itself comes from run_plan (or
            # is dropped on a non-delegating turn) — this only tracks the id.
            if kind == "captain":
                captain_run_id = rid
            run = run_by_id(rid)
            # 同人续派 / 热修 / 辩论续写: not in the plan — synthesize off the session root.
            if run is None and continues:
                original = run_by_id(continues)
                if original is not None:
                    origin_agent = agent_by_id(original["agentId"])
                    agents.append(
                        {
                            "id": agid,
                            "role": origin_agent["role"] if origin_agent else original["agentId"],
                            "thinking": origin_agent["thinking"] if origin_agent else True,
                            "status": "idle",
                            "currentRunId": None,
                            "output": "",
                            "reasoning": "",
                            "toolProgress": None,
                        }
                    )
                    run = {
                        **_run_from_plan(
                            {"id": rid, "agent_id": agid, "task": original["task"]},
                            act_id=original.get("actId") or "act-1",
                        ),
                        "parentRunId": parent,
                        "kind": kind,
                        "continuesRunId": continues,
                        # 乙 wire 携 round/stance：debate 续写从 wire 读身份与轮次。
                        "stance": p.get("stance"),
                        "group": p.get("group"),
                        "round": p.get("round") or 0,
                    }
                    runs.append(run)
            if run is not None:
                run["status"] = "running"
                run["parentRunId"] = parent
                run["kind"] = kind
                if continues:
                    run["continuesRunId"] = continues
                # 冷回落接手: mid-flight `_redir` carries replaces_run_id on the wire.
                replaces = p.get("replaces_run_id")
                if replaces:
                    run["replacesRunId"] = replaces
            ag = agent_by_id(agid)
            if ag:
                ag["status"] = "working"
                ag["currentRunId"] = rid
                ag["toolProgress"] = None

        elif etype == "run_phase":
            # Worker 活动相位单一源：thinking / tool / waiting_children / winding_down.
            # winding_down 粘性覆盖 thinking/tool，直到终态清除。
            # queued = status pending；skipped = status skipped（不经本事件）。
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                phase = p.get("phase") or ""
                current = run.get("phase")
                if current == "winding_down" and phase in ("thinking", "tool"):
                    pass
                elif phase in (
                    "thinking",
                    "tool",
                    "waiting_children",
                    "winding_down",
                ):
                    run["phase"] = phase
                    run["phaseTool"] = (
                        p.get("tool_name") if phase == "tool" else None
                    )

        elif etype == "run_context":
            # 收到的上下文 (上下文传递可视化): the structured context this run was fed, carried
            # verbatim (wire-shaped snake_case blocks) — the same data the LLM saw. The
            # CAPTAIN's (kind=captain) routes TURN-LEVEL onto captainContext (the CEO is the
            # bubble above the graph, not a node — so it shows on every turn, pure chat
            # included), APPENDING across emits so its context GROWS by each post-delegation
            # team readback (通道⑤); a WORKER's folds onto its graph node. Mirrors the
            # desktop/mobile folds (conformance pins them equal).
            rid = p.get("run_id", "")
            if rid and rid == captain_run_id:
                captain_context.extend(p.get("blocks") or [])
            else:
                run = run_by_id(rid)
                if run is not None:
                    run["receivedContext"] = list(p.get("blocks") or [])

        elif etype == "run_output_delta":
            # 同 content_delta 的 replace 语义，作用在这个 worker 的正文通道上。
            ag = agent_by_id(p.get("agent_id", ""))
            run = run_by_id(p.get("run_id", ""))
            delta = p.get("delta") or ""
            steps = run["process"] if run is not None else None
            tail = steps[-1] if steps and steps[-1].get("kind") == "content" else None
            if p.get("replace") and tail is not None:
                if ag:
                    ag["output"] = ag["output"].removesuffix(tail["text"]) + delta
                tail["text"] = delta
            else:
                if ag:
                    ag["output"] += delta
                if delta and steps is not None:
                    if tail is not None:
                        tail["text"] += delta
                    else:
                        steps.append({"kind": "content", "text": delta})

        elif etype == "run_output_reset":
            # 草稿丢弃信号的 worker 对偶（content_reset 之于 CEO）：引擎丢弃 worker 卡片已流式
            # 的这一版草稿、发 run_output_reset（reason 说明为何）。清该 agent 的 output 标量
            # （重写版从干净态重累积），reasoning 是真实过程、保留——否则会把「违规版+修正版」
            # 拼在一起。transport-only（不进 journal），与三端 fold 一致。
            ag = agent_by_id(p.get("agent_id", ""))
            if ag:
                ag["output"] = ""
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                steps = run["process"]
                while steps and steps[-1].get("kind") == "content":
                    steps.pop()
                # 仅 finish_guard（交付前核验回炉）留「已按交付规范重写」痕迹。
                if p.get("reason") == "finish_guard":
                    steps.append({"kind": "rework"})

        elif etype == "run_reasoning_delta":
            ag = agent_by_id(p.get("agent_id", ""))
            run = run_by_id(p.get("run_id", ""))
            delta = p.get("delta") or ""
            steps = run["process"] if run is not None else None
            tail = steps[-1] if steps and steps[-1].get("kind") == "reasoning" else None
            if p.get("replace") and tail is not None:
                if ag:
                    ag["reasoning"] = ag["reasoning"].removesuffix(tail["text"]) + delta
                tail["text"] = delta
            else:
                if ag:
                    ag["reasoning"] += delta
                if delta and steps is not None:
                    if tail is not None:
                        tail["text"] += delta
                    else:
                        steps.append({"kind": "reasoning", "text": delta})

        elif etype == "run_tool_progress":
            ag = agent_by_id(p.get("agent_id", ""))
            if ag:
                ag["toolProgress"] = {
                    "toolName": p.get("tool_name", ""),
                    "chars": p.get("chars", 0),
                }

        elif etype == "run_completed":
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                run["status"] = "completed"
                run["outputSummary"] = p.get("output_summary")
                # 完工交接简报: verbatim structured brief when the worker authored one (else absent
                # → stays None), so the run-detail 摘要 shows the author's own wrap-up.
                run["debrief"] = p.get("debrief")
                run["durationMs"] = p.get("duration_ms")
                run["role"] = p.get("role")
                run["model"] = p.get("model")
                run["usage"] = p.get("usage")
                run["cost"] = p.get("cost")
                run.pop("phase", None)
                run.pop("phaseTool", None)
            ag = agent_by_id(p.get("agent_id", ""))
            if ag:
                ag["status"] = "completed"
                ag["currentRunId"] = None
                ag["toolProgress"] = None

        elif etype == "run_failed":
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                run["status"] = "failed"
                run["error"] = p.get("error")
                # Additive face class; absent on old journals → None（脸回退「失败」）.
                run["failureKind"] = p.get("failure_kind")
                # Additive: files already landed before terminal failure.
                run["productLanded"] = p.get("product_landed")
                # 完工交接简报 on a failed run: the author's wrap-up when a contract-missing
                # worker still produced one (else absent → stays None).
                run["debrief"] = p.get("debrief")
                run.pop("phase", None)
                run.pop("phaseTool", None)
            ag = agent_by_id(p.get("agent_id", ""))
            if ag:
                ag["status"] = "error"
                ag["toolProgress"] = None

        elif etype == "run_cancelled":
            # 跑一半改方向 / 整轮停止 / 只停这项工作: interrupt mid-flight (orthogonal to
            # run_failed). reason=redirect → single-worker hard-stop (hot continue_run /
            # cold _redir may follow); reason=stop → whole-turn abort; reason=user_stop →
            # per-worker stop with no hot/cold follow-up. Clear currentRunId + toolProgress
            # so the node leaves its live「正在生成」line (reload-safe).
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                run["status"] = "cancelled"
                run.pop("phase", None)
                run.pop("phaseTool", None)
            ag = agent_by_id(p.get("agent_id", ""))
            if ag:
                ag["status"] = "cancelled"
                ag["currentRunId"] = None
                ag["toolProgress"] = None

        elif etype == "run_skipped":
            # 级联跳过 / graceful abort: node never ran — materialised SKIPPED so the graph
            # shows「未执行」instead of forever-pending. Orthogonal to run_cancelled.
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                run["status"] = "skipped"
                run.pop("phase", None)
                run.pop("phaseTool", None)
            # Agent never started — leave idle (no currentRunId / toolProgress to clear).

        elif etype == "run_progress":
            # Progress is derived from run states below (cumulative, multi-batch safe);
            # the wire counter is a timeline marker only.
            pass

        elif etype == "plan_revised":
            # 「计划已调整」轻痕迹 (设计 §7.2): the CEO autonomously re-bound / re-steered the
            # paused plan via replan. Fold each affected node's kind onto its run so every end
            # paints a non-interrupting trace (mirrors the desktop/mobile folds; conformance
            # pins them equal). A stray run_id (not on this graph) is ignored.
            for rev in p.get("revisions") or []:
                run = run_by_id(rev.get("run_id", ""))
                if run is not None:
                    run["revised"] = rev.get("kind")

        elif etype == "run_escalation":
            # 升级实时可见 (非阻塞): a worker flagged a decision/blocker for the CEO — append
            # it to its run so the node carries a ⚠️ badge (mirrors the desktop/mobile folds,
            # conformance pins them equal). Status stays "raised" (the worker kept working).
            # 统一时间线二期 D1/D6: also drop an `escalation` process marker keyed by
            # escalation_id (raised 轻行 slot; ProjectedTurn escalations[] 形状不加 id).
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                run["escalations"].append(
                    {
                        "question": p.get("question", ""),
                        "assumption": p.get("assumption", ""),
                        "blocking": bool(p.get("blocking")),
                        "status": "raised",
                        "answer": None,
                        "kind": p.get("kind") or "normal",
                    }
                )
            eid = p.get("escalation_id") or ""
            if eid and not has_marker("escalation", "escalation_id", eid):
                process.append({"kind": "escalation", "escalation_id": eid})

        elif etype == "escalation_required":
            # 阻塞式求决策: a worker SUSPENDED on a blocking escalate — append a "pending"
            # card to its run. The turn does NOT pause (siblings keep running), so unlike
            # the halting gates this sets no `pending` interaction.
            # ``awaiting=ceo`` is projected; classic user path omits (default).
            # 统一时间线二期 D1/D2: positional `escalation` marker at required 时刻.
            run = run_by_id(p.get("run_id", ""))
            if run is not None:
                awaiting = p.get("awaiting") or "user"
                if awaiting not in ("user", "ceo"):
                    awaiting = "user"
                entry: dict = {
                    "question": p.get("question", ""),
                    "assumption": p.get("assumption", ""),
                    "blocking": True,
                    "status": "pending",
                    "answer": None,
                    "kind": p.get("kind") or "normal",
                }
                if awaiting == "ceo":
                    entry["awaiting"] = "ceo"
                run["escalations"].append(entry)
            eid = p.get("escalation_id") or ""
            if eid and not has_marker("escalation", "escalation_id", eid):
                process.append({"kind": "escalation", "escalation_id": eid})

        elif etype == "escalation_resolved":
            # Settlement: flip this run's pending escalation. Wire status is
            # resolved | assumed | timed_out. Projected RunEscalation keeps
            # assumed/timed_out distinct; both leave answer null.
            run = run_by_id(p.get("run_id", ""))
            esc = (
                next((e for e in run["escalations"] if e.get("status") == "pending"), None)
                if run is not None
                else None
            )
            if esc is not None:
                raw = p.get("status")
                if raw == "resolved":
                    esc["status"] = "resolved"
                    esc["answer"] = p.get("answer", "")
                elif raw == "assumed":
                    esc["status"] = "assumed"
                    esc["answer"] = None
                else:
                    esc["status"] = "timed_out"
                    esc["answer"] = None
                if p.get("arbitrated_by") == "ceo":
                    esc["arbitrated_by"] = "ceo"
                    if "via_user" in p:
                        esc["via_user"] = bool(p.get("via_user"))

        elif etype == "debate_result":
            # 一场辩论收场：整段结构化产物（form/motion/rounds/brief/sides/各方 run_id）
            # verbatim 存入，前端辩论视图据此取简报 + 叙事线，从执行图辩手节点取发言全文。
            debate = p

        elif etype == "debate_round_started":
            # 一轮开场（发言前）：先给焦点，verdict=None 表示该轮进行中（仅定焦点未裁判，
            # clashes 恒空——交锋边由裁判步产出；cross_exam 恒空——质询 beat 尚未开始）。
            # 同事件权威声明本场是否开质询（缺字段→保持 False，向后兼容老 journal）。
            # opening 仅首轮非空：sticky 取第一个非空，不被后续轮空串覆盖。
            if p.get("cross_exam_enabled") is True:
                cross_exam_enabled = True
            raw_opening = (p.get("opening") or "").strip()
            if raw_opening and not debate_opening:
                debate_opening = raw_opening
            upsert_round(
                {
                    "round_no": p.get("round_no", 0),
                    "focus": p.get("focus", ""),
                    "summary": "",
                    "verdict": None,
                    "sides": [],
                    "clashes": [],
                    "cross_exam": [],
                    "witness_exam": [],
                    "findings": [],
                    "thread_turns": [],
                }
            )

        elif etype == "debate_round":
            # 一轮收尾（裁判+小结后）：焦点/小结/裁判/各方→辩手 run_id 映射/L3 交锋边/质询问答
            # + 证人答问（批 D1）+ 红队 finding / 圆桌线程（缺字段→[]，旧载荷降级）。
            upsert_round(
                {
                    "round_no": p.get("round_no", 0),
                    "focus": p.get("focus", ""),
                    "summary": p.get("summary", ""),
                    "verdict": p.get("verdict"),
                    "sides": list(p.get("sides") or []),
                    "clashes": list(p.get("clashes") or []),
                    "cross_exam": list(p.get("cross_exam") or []),
                    "witness_exam": list(p.get("witness_exam") or []),
                    "findings": list(p.get("findings") or []),
                    "thread_turns": list(p.get("thread_turns") or []),
                }
            )

        elif etype == "debate_pretrial_started":
            debate_pretrial = {
                "status": "running",
                "thorough": bool(p.get("thorough", True)),
                "skipReason": p.get("skip_reason"),
                "sides": list(p.get("sides") or []),
                "orders": [],
                "evidenceLedgerCount": 0,
                "fallbackSelfSearch": False,
                "evidenceReady": False,
                "completeness": "empty",
                "incomplete": True,
            }

        elif etype == "debate_pretrial_orders":
            if debate_pretrial is None:
                debate_pretrial = {
                    "status": "running",
                    "thorough": bool(p.get("thorough", True)),
                    "skipReason": None,
                    "sides": list(p.get("sides") or []),
                    "orders": [],
                    "evidenceLedgerCount": 0,
                    "fallbackSelfSearch": False,
                    "evidenceReady": False,
                    "completeness": "empty",
                    "incomplete": True,
                }
            debate_pretrial["orders"] = list(p.get("orders") or [])

        elif etype == "debate_pretrial_completed":
            completeness = p.get("completeness") or "empty"
            debate_pretrial = {
                "status": p.get("status") or "done",
                "thorough": bool(p.get("thorough", True)),
                "skipReason": p.get("skip_reason"),
                "sides": list(p.get("sides") or []),
                "orders": list(p.get("orders") or []),
                "evidenceLedgerCount": int(p.get("evidence_ledger_count") or 0),
                "fallbackSelfSearch": bool(p.get("fallback_self_search")),
                "evidenceReady": bool(p.get("evidence_ready")),
                "completeness": completeness,
                "incomplete": bool(p.get("incomplete", completeness != "full")),
            }
            if p.get("external_evidence_mode") is not None:
                debate_pretrial["externalEvidenceMode"] = p.get(
                    "external_evidence_mode"
                )
            if p.get("external_evidence_reason") is not None:
                debate_pretrial["externalEvidenceReason"] = p.get(
                    "external_evidence_reason"
                )

        elif etype == "team_note_posted":
            # 团队便签墙 (§2.2 通): a worker broadcast a one-line decision / heads-up to its
            # concurrent siblings. Fold it onto the turn's teamNotes (chronological), deduped by
            # noteId for replay safety (mirrors the desktop/mobile folds; conformance pins them
            # equal). The wall is engine-scoped; the panel just lists the turn's notes.
            note_id = p.get("note_id", "")
            supersedes = p.get("supersedes")
            if not any(n.get("noteId") == note_id for n in team_notes):
                team_notes.append(
                    {
                        "noteId": note_id,
                        "runId": p.get("run_id", ""),
                        "agentId": p.get("agent_id", ""),
                        "role": p.get("role", ""),
                        "kind": p.get("kind", ""),
                        "text": p.get("text", ""),
                        "ts": p.get("ts"),
                        # 便签会过期 → supersession (§2.2): a fresh note is active; this fold marks
                        # the TARGET stale below. `supersedes` is the note this one 改写/作废s (None
                        # for a fresh post) — kept so the panel can link an amendment to its origin.
                        "status": "active",
                        "supersedes": supersedes,
                    }
                )
                if p.get("source"):
                    team_notes[-1]["source"] = p["source"]
            # An amendment (carries `supersedes`) marks its TARGET superseded (改写) / voided
            # (作废) — `supersede_mode` is the single discriminator every fold shares. The target
            # was posted earlier, so it is already in the list (events replay in order).
            if supersedes:
                target = next((n for n in team_notes if n.get("noteId") == supersedes), None)
                if target is not None:
                    target["status"] = (
                        "voided" if p.get("supersede_mode") == "void" else "superseded"
                    )

        elif etype == "approval_required":
            # 统一时间线二期 D3/D5: positional `approval` marker at required 时刻；行渲染由
            # resolved 门控（pending 标记在、行不显）。Gate lifecycle → fold_interactions.
            aid = p.get("approval_id") or ""
            if aid and not has_marker("approval", "approval_id", aid):
                process.append({"kind": "approval", "approval_id": aid})

        elif etype == "approval_resolved":
            pass

        elif etype == "checkpoint_required":
            cid = p.get("checkpoint_id", "")
            # 检查点时间线落点: positional marker so the card replays at its real spot
            # (card body folds separately, keyed by id). Mirrors EventSink — do not
            # drop bubble text here. Absorb is ``content_reset(reason=ask_user)`` only
            # when the engine folded this round's prose into the card.
            if cid and not has_marker("checkpoint", "checkpoint_id", cid):
                process.append({"kind": "checkpoint", "checkpoint_id": cid})

        elif etype == "checkpoint_resolved":
            pass

        elif etype == "plan_review_required":
            cid = p.get("checkpoint_id", "")
            # 计划复核时间线落点: positional marker (card body folds separately).
            if cid and not has_marker("plan_review", "checkpoint_id", cid):
                process.append({"kind": "plan_review", "checkpoint_id": cid})
            run_ids = [s.get("run_id", "") for s in (p.get("steps") or [])]
            checkpoint_steps[cid] = run_ids
            for rid in run_ids:
                run = run_by_id(rid)
                if run is not None:
                    run["checkpoint"] = {"status": "pending", "decision": None}

        elif etype == "plan_review_resolved":
            cid = p.get("checkpoint_id", "")
            for rid in checkpoint_steps.get(cid, []):
                run = run_by_id(rid)
                if run is not None:
                    run["checkpoint"] = {
                        "status": "resolved",
                        "decision": p.get("decision"),
                    }

        elif etype in ("team_preview_required", "team_preview_resolved"):
            # Retired kickoff pair — skip (old journal segment may be absent).
            pass

        elif etype == "stage_card_required":
            # 阶段推进卡时间线落点：required 时刻锚点；生命周期仍由 fold_interactions 承载。
            # 跨回合流下 message_start 会清 process，故多回合向量的最终 projected.process
            # 通常不含此标记——标记落在宿主回合 journal，供历史回看。
            sid = p.get("stage_card_id") or ""
            if sid and not has_marker("stage_card", "stage_card_id", sid):
                process.append({"kind": "stage_card", "stage_card_id": sid})

        elif etype == "stage_card_resolved":
            pass

        elif etype == "error":
            saw_error = True
            code = str(p.get("code") or "").strip() or "LLM_ERROR"
            message = str(p.get("message") or "").strip()
            turn_error = {"code": code, "message": message}

        elif etype == "message_end":
            finish_reason = p.get("finish_reason")
            cost = p.get("cost")
            raw_outcome = p.get("outcome")
            if raw_outcome in ("ok", "partial", "paused", "error"):
                explicit_outcome = str(raw_outcome)

        elif etype == "turn_warning":
            msg = p.get("message")
            if isinstance(msg, str) and msg.strip():
                turn_warning = msg

        elif etype == "auto_folder_created":
            fid = p.get("folder_id")
            if isinstance(fid, str) and fid.strip():
                auto_folder = {"folderId": fid, "name": str(p.get("name") or "")}

        elif etype == "team_synthesis_preview":
            # 同 key 保最新（后写覆盖）。
            team_synthesis_preview = p

        elif etype == "delivery_status":
            # 交付状态：同 execution_id 保最新（后写覆盖；artifacts 已是各波声明且落盘并集）。
            delivery_status = p

        elif etype == "user_interjection":
            iid = str(p.get("interjection_id") or "").strip()
            if not iid:
                continue
            # 零宽 positional marker（与 team/ask/checkpoint 同构）：同 id 首次出现时
            # 按事件流顺序钉到 process 末尾；后续状态更新只改旁路，不重复落标记。
            if not has_marker("user_interjection", "interjection_id", iid):
                process.append({"kind": "user_interjection", "interjection_id": iid})
            leaf: dict[str, Any] = {
                "interjectionId": iid,
                "executionId": str(p.get("execution_id") or ""),
                "content": str(p.get("content") or ""),
                "status": str(p.get("status") or "received"),
                "note": p.get("note") if isinstance(p.get("note"), str) else None,
            }
            raw_atts = p.get("attachments")
            if isinstance(raw_atts, list) and raw_atts:
                atts_out: list[dict[str, Any]] = []
                for a in raw_atts:
                    if not isinstance(a, dict):
                        continue
                    name = a.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    entry: dict[str, Any] = {
                        "name": name,
                        "binary": bool(a.get("binary")),
                    }
                    wp = a.get("workspace_path")
                    if isinstance(wp, str) and wp.strip():
                        entry["workspacePath"] = wp
                    atts_out.append(entry)
                if atts_out:
                    leaf["attachments"] = atts_out
            raw_mentions = p.get("agent_mentions")
            if isinstance(raw_mentions, list) and raw_mentions:
                mentions_out: list[dict[str, Any]] = []
                for m in raw_mentions:
                    if not isinstance(m, dict):
                        continue
                    agent_id = str(m.get("agent_id") or "").strip()
                    role = str(m.get("role") or "").strip()
                    if not agent_id or not role:
                        continue
                    mentions_out.append({"agentId": agent_id, "role": role})
                if mentions_out:
                    leaf["agentMentions"] = mentions_out
            idx = _user_interjection_by_id.get(iid)
            if idx is None:
                _user_interjection_by_id[iid] = len(user_interjections)
                user_interjections.append(leaf)
            else:
                user_interjections[idx] = leaf

        else:
            # message_start / turn_saved / title_generated / followups_generated /
            # board_op_required / board_read_required / desktop_notify_required /
            # host_op_required / tool_progress / workspace_op_required /
            # handoff_* / turn_queued / turn_queue_started / turn_queue_cancelled /
            # interaction_orphaned / escalation_* (run escalations folded above) —
            # not part of the normalized turn judge state beyond interactions[] fold
            # (no-op here). Mirrored by the frontend folds' assertNever switch
            # so the set stays in lockstep.
            pass

    # Interactions[] — single fold implementation (runtime pending_interactions).
    interactions = [
        project_interaction_leaf(rec) for rec in fold_interactions(events)
    ]
    gate_pending = any(
        i.get("status") == "pending" and i.get("kind") in GATE_KINDS for i in interactions
    )
    if finish_reason is not None:
        status = _FINISH_TO_STATUS.get(finish_reason or "", "completed")
    elif saw_error:
        status = "failed"
    elif gate_pending:
        status = "paused"
    else:
        status = "running"

    # A cancelled OR failed turn may leave in-flight nodes with no terminal frame; freeze
    # them as cancelled (parity with projectExecution's freeze pass). The cancelled case is
    # the graceful one (workers get run_cancelled). The failed case is the defensive one: a
    # turn that errors out (hard crash / lost terminal frame) while a worker is still
    # in-flight would otherwise replay that node as a forever-spinning "running" on reload —
    # 避免假 working must cover the failed outcome too, not just the stop.
    if status in ("cancelled", "failed"):
        for r in runs:
            if r["status"] == "running":
                r["status"] = "cancelled"
                r.pop("phase", None)
                r.pop("phaseTool", None)
        for a in agents:
            if a["status"] == "working":
                a["status"] = "cancelled"

    # Turn terminal (completed / cancelled / failed): any plan-declared node that never got
    # a terminal run frame (old journals without run_skipped, or a grant-then-end vector)
    # closes as skipped —「未执行」instead of forever「排队中」. Live streams emit run_skipped
    # at wave close; this is the journal-compat / defensive finalize pass.
    if status in ("completed", "cancelled", "failed"):
        for r in runs:
            if r["status"] == "pending":
                r["status"] = "skipped"

    outcome = resolve_turn_outcome(
        events=events,
        finish_reason=finish_reason,
        has_error=saw_error,
        explicit=explicit_outcome,
        running=status == "running",
    )

    return {
        "status": status,
        "finishReason": finish_reason,
        "outcome": outcome,
        "error": turn_error,
        "content": content,
        "reasoning": reasoning,
        # 收到的上下文 · CEO 侧 (上下文传递可视化, 通道①): turn-level, present on every turn the
        # captain emitted run_context for (pure chat included), [] otherwise.
        "captainContext": captain_context,
        # The CEO's inline timeline — carried for single-agent AND multi-agent turns
        # (统一团队时间线): besides reasoning/content/tool steps it carries zero-width
        # POSITIONAL MARKERS — `team` (the collaboration graph slot, dropped at run_plan),
        # `checkpoint` / `ask` / `plan_review` (interaction cards), `user_interjection`
        # (mid-turn steer / 协调插话，同 id 首次 received 钉位) — fixing where each
        # non-text element renders in chronological order, worker activity rides
        # `runs`/`agents`. A pure reasoning/content turn builds no structural step (the
        # live persist gate then stores no process, matching the fold).
        "process": process,
        "citations": citations,
        "evidenceLedger": evidence_ledger,
        "citedIds": cited_ids,
        "agents": agents,
        "runs": runs,
        # 幕序列（批 A1）：旧 run_plan 无 act → 合成单幕；无协作图时 []。
        "acts": acts,
        "progress": {
            "completed": sum(1 for r in runs if r["status"] == "completed"),
            "total": len(runs),
        },
        "interactions": interactions,
        "cost": cost,
        "debate": debate,
        "debateRounds": debate_rounds,
        "debatePretrial": debate_pretrial,
        "crossExamEnabled": cross_exam_enabled,
        "debateOpening": debate_opening,
        "teamSynthesisPreview": team_synthesis_preview,
        # 交付状态（delivery_status）：结构化交付对账（已交付/缺口/待操作），null 当无。
        "deliveryStatus": delivery_status,
        "turnWarning": turn_warning,
        # 裸聊自动建文件夹（auto_folder_created）：{folderId, name}，null 当本回合没建。
        "autoFolder": auto_folder,
        # 团队便签墙 (§2.2 通): the turn's posted notes (chronological), [] when none.
        "teamNotes": team_notes,
        # 墙已升：仅真上线（旧 journal / 无墙批次缺省）。
        **({"noteWall": True} if note_wall else {}),
        # 协调中用户插话：同 interjectionId 保最新，[] when none.
        "userInterjections": user_interjections,
    }
