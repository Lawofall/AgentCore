"""Multi-agent run SSE event factories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.core.types import new_id
from agentcore.runtime.events.types import EventType, SSEEvent

if TYPE_CHECKING:
    from agentcore.runtime.events.payloads.chat import ResetReason


def _wire_cost(cost: dict[str, Any] | None) -> dict[str, Any]:
    """SSE cost object: money keys + pricing_source; strip ledger-only fields."""
    if cost is None:
        return {
            "input": 0,
            "cached": 0,
            "output": 0,
            "total": 0,
            "currency": "CNY",
            "pricing_source": "curated",
        }
    out: dict[str, Any] = {
        "input": int(cost.get("input", 0) or 0),
        "cached": int(cost.get("cached", 0) or 0),
        "output": int(cost.get("output", 0) or 0),
        "total": int(cost.get("total", 0) or 0),
        "currency": str(cost.get("currency") or "CNY"),
        "pricing_source": str(cost.get("pricing_source") or "curated"),
    }
    if cost.get("estimated_total") is not None:
        out["estimated_total"] = int(cost["estimated_total"])
        out["estimated_currency"] = str(cost.get("estimated_currency") or out["currency"])
    return out


def run_plan(
    *,
    execution_id: str,
    plan_type: str,
    task_summary: str,
    agents: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    host_message_id: str | None = None,
    prev_execution_id: str | None = None,
    act: dict[str, Any] | None = None,
    note_wall: bool | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "plan_type": plan_type,
        "task_summary": task_summary,
        "agents": agents,
        "runs": runs,
    }
    # host_message_id：仅兼容旧 journal / 测试回放；生产新路径不写。
    if host_message_id:
        payload["host_message_id"] = host_message_id
    if prev_execution_id:
        payload["prev_execution_id"] = prev_execution_id
    if act:
        payload["act"] = act
    if note_wall:
        payload["note_wall"] = True
    return SSEEvent(
        type=EventType.RUN_PLAN,
        payload=payload,
    )


def graph_append(
    *,
    execution_id: str,
    host_message_id: str,
    append_message_id: str,
    added_count: int,
    roles: list[str] | None = None,
    added_run_ids: list[str] | None = None,
    act_id: str | None = None,
    act_kind: str | None = None,
    authorized_by: str | None = None,
) -> SSEEvent:
    """已停发：旧跨回合同图追加锚点（兼容旧 journal / 测试回放）。新路径用 prev_execution_id。"""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "host_message_id": host_message_id,
        "append_message_id": append_message_id,
        "added_count": int(added_count),
        "roles": list(roles or []),
        "added_run_ids": list(added_run_ids or []),
    }
    if act_id:
        payload["act_id"] = act_id
    if act_kind:
        payload["act_kind"] = act_kind
    if authorized_by:
        payload["authorized_by"] = authorized_by
    return SSEEvent(
        type=EventType.GRAPH_APPEND,
        payload=payload,
    )


def plan_revised(
    *,
    execution_id: str,
    revisions: list[dict[str, Any]],
) -> SSEEvent:
    """The CEO autonomously adjusted a paused plan via ``replan`` (受监督的波循环). Carries
    the affected run_ids + per-node ``kind`` (``bind`` = a late-bound placeholder finalised
    from upstream evidence; ``steer`` = a not-yet-run node re-steered after a scope deviation)
    so every end folds a non-interrupting「计划已调整」trace onto those graph nodes (设计 §7.2
    「计划已调整」轻痕迹). Emitted only when something actually changed (a no-op resume sends
    nothing); journaled, so the trace replays on reload."""
    return SSEEvent(
        type=EventType.PLAN_REVISED,
        payload={
            "execution_id": execution_id,
            "revisions": revisions,
        },
    )


def run_started(
    run_id: str,
    agent_id: str,
    *,
    parent_run_id: str | None = None,
    kind: str = "agent",
    continues_run_id: str | None = None,
    stance: str | None = None,
    group: str | None = None,
    round_no: int = 0,
    side_key: str | None = None,
    replaces_run_id: str | None = None,
) -> SSEEvent:
    """A run began. A 续写 (CEO 续派 / redirect 热修 / 辩手后续轮 / 证人答问) carries
    ``continues_run_id`` pointing at the session root (星型), while ``parent_run_id``
    stays the true delegation parent (captain / moderator). Debate continuations
    additionally carry ``stance``/``group`` + TRUE ``round`` + ``side_key`` so every
    fold projects 第几轮/哪一方 from the wire (no run_id regex). Optional fields
    ride the payload ONLY when set.

    ``group`` 权威分流：计划内节点（取证员 / 首轮辩手 / …）以 ``run_plan.runs[].group``
    为准——``execute_agent_node`` 的首跑 ``run_started`` **不**重复携带；仅
    ``continue_run`` 路径（后轮辩手 / 质询 / 结辩 / 证人）在 ``run_started`` 上带
    ``group``，供未入 plan 的续写出生。三端 fold 对计划内 run 不从 ``run_started``
    覆盖 ``group``。

    ``replaces_run_id`` (冷回落接手): a mid-flight ``_redir`` spawn that takes over a
    redirected worker — orthogonal to continuation."""
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "parent_run_id": parent_run_id,
        "kind": kind,
    }
    if continues_run_id:
        payload["continues_run_id"] = continues_run_id
    if stance:
        payload["stance"] = stance
    if group:
        payload["group"] = group
    if round_no:
        payload["round"] = round_no
    if side_key:
        payload["side_key"] = side_key
    if replaces_run_id:
        payload["replaces_run_id"] = replaces_run_id
    return SSEEvent(type=EventType.RUN_STARTED, payload=payload)


def run_context(run_id: str, agent_id: str, blocks: list[dict[str, Any]]) -> SSEEvent:
    return SSEEvent(
        type=EventType.RUN_CONTEXT,
        payload={"run_id": run_id, "agent_id": agent_id, "blocks": blocks},
    )


def run_output_delta(
    run_id: str, agent_id: str, delta: str, *, replace: bool = False
) -> SSEEvent:
    """Grow this worker's 正文 by ``delta``.

    ``replace`` is set ONLY by the attach replay builders (see
    :mod:`agentcore.runtime.events.attach_replay`): ``delta`` is then the whole current
    text of this run's last still-open output block, not an increment.
    """
    payload: dict[str, Any] = {"run_id": run_id, "agent_id": agent_id, "delta": delta}
    if replace:
        payload["replace"] = True
    return SSEEvent(type=EventType.RUN_OUTPUT_DELTA, payload=payload)


def run_output_reset(run_id: str, agent_id: str, reason: ResetReason) -> SSEEvent:
    """清掉这个 worker 卡片已流式累积的草稿正文（``content_reset`` 的 worker 对偶）。

    done 轮正文已逐 token 经 ``run_output_delta`` emit 到 run 节点，无法「收回」，故引擎
    丢弃草稿时发本事件——前端清该 agent 的 ``outputChunks``，重写版重新流式，呈现为一次
    干净替换而非追加。``reason`` 必填（见 payloads.chat.ResetReason）：仅 ``finish_guard``
    （交付前核验回炉）折出「已按交付规范重写」痕迹（didRework），其余 reason（retry /
    narration / …）只清正文、不留 chip。transport-only、不进 journal（重载时 worker 产出由
    ``message_final`` fact 重建；rework 痕迹经 run_process_* 的 rework 步持久化）。"""
    return SSEEvent(
        type=EventType.RUN_OUTPUT_RESET,
        payload={"run_id": run_id, "agent_id": agent_id, "reason": reason},
    )


def run_reasoning_delta(
    run_id: str, agent_id: str, delta: str, *, replace: bool = False
) -> SSEEvent:
    """Grow this worker's 思考 by ``delta`` (``replace`` — see :func:`run_output_delta`)."""
    payload: dict[str, Any] = {"run_id": run_id, "agent_id": agent_id, "delta": delta}
    if replace:
        payload["replace"] = True
    return SSEEvent(type=EventType.RUN_REASONING_DELTA, payload=payload)


def run_phase(
    run_id: str,
    agent_id: str,
    phase: str,
    *,
    tool_name: str | None = None,
) -> SSEEvent:
    """Emit a worker mid-flight activity phase (thinking / tool / waiting_children / winding_down).

    Orthogonal to lifecycle ``RunStatus`` (pending/running/skipped/…). EPHEMERAL —
    fold keeps the latest per ``run_id``; reload falls back to status. ``tool_name``
    is only meaningful when ``phase=="tool"``.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "phase": phase,
    }
    if phase == "tool" and tool_name:
        payload["tool_name"] = tool_name
    return SSEEvent(type=EventType.RUN_PHASE, payload=payload)


def run_tool_progress(run_id: str, agent_id: str, tool_name: str, chars: int) -> SSEEvent:
    return SSEEvent(
        type=EventType.RUN_TOOL_PROGRESS,
        payload={
            "run_id": run_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "chars": chars,
        },
    )


def escalation_raised(
    run_id: str,
    agent_id: str,
    *,
    question: str,
    assumption: str,
    blocking: bool,
    kind: str = "normal",
    escalation_id: str | None = None,
    source: str | None = None,
) -> SSEEvent:
    """非阻塞 raised 升级（DURABLE, 统一时间线二期 D6）。

    ``escalation_id`` 键给 raised 轻行的时间线标记（attach replay 幂等去重）；
    生产调用点缺省即自动生成，conformance 向量传固定值保 golden 稳定。

    ``source`` 仅早停 / 打转收口路径写入（如 ``validation_thrash`` /
    ``ceiling_backstop``）；真·边干边上报省略，保持 wire 形状不变。
    """
    payload: dict[str, Any] = {
        "escalation_id": escalation_id or new_id(),
        "run_id": run_id,
        "agent_id": agent_id,
        "question": question,
        "assumption": assumption,
        "blocking": blocking,
        "kind": kind if kind in ("normal", "scope", "dep") else "normal",
    }
    if source:
        payload["source"] = source
    return SSEEvent(
        type=EventType.RUN_ESCALATION,
        payload=payload,
    )


def run_escalation_gate(
    run_id: str,
    agent_id: str,
    *,
    layer: str,
    action: str,
    signals: list[dict[str, Any]],
) -> SSEEvent:
    """Escalation Gate 判定结果（方案层 → action=escalate）。

    Live diagnostic twin of a gate trip; durable substance still lands in
    ``RunState.escalations`` / ``escalation_raised`` when the executor surfaces it.
    """
    return SSEEvent(
        type=EventType.RUN_ESCALATION_GATE,
        payload={
            "run_id": run_id,
            "agent_id": agent_id,
            "layer": layer,
            "action": action,
            "signals": signals,
        },
    )


def team_note_posted(
    *,
    execution_id: str,
    note_id: str,
    run_id: str,
    agent_id: str,
    role: str,
    kind: str,
    text: str,
    ts: float,
    supersedes: str | None = None,
    supersede_mode: str | None = None,
    source: str | None = None,
) -> SSEEvent:
    """A worker pinned a note to the batch 便签墙 (§2.2 通). Carries the author (run/agent/
    role), the ``kind`` (``decision`` 我定了 / ``heads_up`` 提个醒) and the one-line ``text``,
    scoped by ``execution_id`` so the team-notes panel groups a turn's notes. Journaled (rides
    the delegate turn), so it replays on reload; folded onto the ProjectedTurn so both ends
    render it. ``note_id`` is the stable key (dedup).

    便签会过期 → supersession (§2.2): an AMENDMENT note also carries ``supersedes`` (the note_id
    it 改写/作废s) + ``supersede_mode`` (``update`` → target superseded / ``void`` → target
    voided). Those two are the single signal every fold uses to mark the TARGET stale — a fresh
    post omits them (kept off the payload so its shape is unchanged)."""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "note_id": note_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "role": role,
        "kind": kind,
        "text": text,
        "ts": ts,
    }
    # Only present on an amendment — a fresh post keeps its original payload shape (and existing
    # fixtures stay byte-identical for non-amendment notes).
    if supersedes is not None:
        payload["supersedes"] = supersedes
    if supersede_mode is not None:
        payload["supersede_mode"] = supersede_mode
    if source is not None:
        payload["source"] = source
    return SSEEvent(type=EventType.TEAM_NOTE_POSTED, payload=payload)


def run_completed(
    run_id: str,
    agent_id: str,
    *,
    output_summary: str,
    duration_ms: int,
    role: str = "member",
    model: str = "",
    usage: dict[str, int] | None = None,
    cost: dict[str, Any] | None = None,
    debrief: dict[str, Any] | None = None,
    output_files: list[str] | None = None,
    gaps: list[dict[str, Any]] | None = None,
    execution_id: str = "",
) -> SSEEvent:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "output_summary": output_summary,
        "duration_ms": duration_ms,
        "role": role,
        "model": model,
        "usage": usage
        if usage is not None
        else {"input": 0, "output": 0, "reasoning": 0, "cache_hit": 0, "cache_miss": 0},
        "cost": _wire_cost(cost),
    }
    # 完工交接简报 (surfacing): the worker's authored 交接简报 — {summary(结论) / key_points /
    # assumptions / next_steps}, each present only when non-empty — carried VERBATIM so the
    # run-detail 摘要 becomes the author's own wrap-up, not a machine truncation of raw prose.
    # Added ONLY when present (a 辩手 / trivial worker / the CEO writes none), so no-debrief
    # fixtures stay byte-identical and the client folds default it to null.
    if debrief:
        payload["debrief"] = debrief
    # Workspace file deliverables (files_touched at run finish) — lets clients like the
    # whiteboard crystallize a `file` artifactCard instead of text-only outputSummary.
    if output_files:
        payload["output_files"] = list(output_files)
    # First-class delivery gaps (缺章软放行 / 超时缩水) — additive; absent on clean completes
    # so old fixtures stay byte-identical. Clients badge known ``reason`` codes.
    if gaps:
        payload["gaps"] = list(gaps)
    # Host-journal routing after turn teardown (pillar A): non-empty only — keeps old
    # fixtures byte-identical when callers omit it.
    if execution_id:
        payload["execution_id"] = execution_id
    return SSEEvent(type=EventType.RUN_COMPLETED, payload=payload)


def run_failed(
    run_id: str,
    agent_id: str,
    error: str,
    *,
    failure_kind: str | None = None,
    debrief: dict[str, Any] | None = None,
    execution_id: str = "",
    product_landed: bool | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
    retry_after: float | None = None,
) -> SSEEvent:
    payload: dict[str, Any] = {"run_id": run_id, "agent_id": agent_id, "error": error}
    # Additive machine-readable face class (quality/format/model/call). Omit when unknown so
    # old fixtures stay byte-identical and clients fall back to「失败」/空 error「调用失败」.
    if failure_kind:
        payload["failure_kind"] = failure_kind
    # 完工交接简报 on a FAILED run: a worker that produced a product + authored a 交接简报 but
    # missed its contract still has a useful wrap-up (结论/关键假设/建议下一步) — carried so the
    # run-detail shows the author's own conclusion next to the failure. Added ONLY when present
    # (infra-failure paths and the captain carry none), so no-debrief fixtures stay byte-identical
    # and the client folds default it to null.
    if debrief:
        payload["debrief"] = debrief
    if execution_id:
        payload["execution_id"] = execution_id
    # True when product files already landed before the terminal failure (e.g. write ok,
    # then upstream 503). Face →「产出已落盘」.
    if product_landed:
        payload["product_landed"] = True
    # Additive transient/terminal signals (AgentCoreError.code / retryable / retry_after).
    # Omit when unknown so old fixtures stay byte-identical.
    if error_code:
        payload["error_code"] = error_code
    if retryable is not None:
        payload["retryable"] = bool(retryable)
    if retry_after is not None:
        payload["retry_after"] = float(retry_after)
    return SSEEvent(type=EventType.RUN_FAILED, payload=payload)


def run_cancelled(
    run_id: str,
    agent_id: str,
    *,
    reason: str = "stop",
    execution_id: str = "",
) -> SSEEvent:
    """A run was interrupted mid-flight (跑一半改方向 / 整轮停止 / 只停这项工作).

    ``reason``:
    - ``redirect`` — user「立即改此人」hard-stopped this worker only; salvage may follow
      with a hot ``continue_run`` or cold ``_redir`` handoff.
    - ``stop`` — whole-turn abort (停止整轮); no per-worker redirect follow-up.
    - ``user_stop`` — user「只停这项工作」; wave absorbs like redirect but **no** hot/cold
      follow-up — drive converges so the CEO keeps the turn.
    - ``worker_timeout`` — 硬超时强杀: the run blew past its wall-clock ceiling and was
      killed after the grace round (nobody re-tasked it). Absorbed + salvaged exactly
      like ``redirect`` — the CEO may 续派 the partial 现场 — but the cause is the
      timeout, so the face must not read「已改方向」.

    Orthogonal to ``run_failed`` (error terminal). Durable so reload doesn't leave the
    node stuck ``running`` / agent ``working``.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "reason": reason,
    }
    if execution_id:
        payload["execution_id"] = execution_id
    return SSEEvent(
        type=EventType.RUN_CANCELLED,
        payload=payload,
    )


def run_skipped(
    run_id: str,
    agent_id: str,
    *,
    reason: str = "cascade",
) -> SSEEvent:
    """A plan node never ran and was materialised as SKIPPED (级联跳过 / graceful abort).

    ``reason``:
    - ``cascade`` — a dependency failed with ``on_failure=skip``; this node (and further
      dependents) were never dispatched.
    - ``abort`` — scheduling ended via graceful abort (``on_failure=abort``, plan_review
      stop, supervised ``replan(stop=true)``, or terminal cancel of the wave — parent
      force_cancel / nested drive abort / user_stop); the un-run tail was materialised
      SKIPPED so the graph shows「未执行」instead of a forever-pending queue.

    Orthogonal to ``run_cancelled`` (mid-flight interrupt). Durable so reload doesn't leave
    the node stuck ``pending`` / 「排队中」after the turn has closed.
    """
    return SSEEvent(
        type=EventType.RUN_SKIPPED,
        payload={"run_id": run_id, "agent_id": agent_id, "reason": reason},
    )


def run_progress(completed: int, total: int) -> SSEEvent:
    return SSEEvent(
        type=EventType.RUN_PROGRESS,
        payload={"completed": completed, "total": total},
    )


def coordination_wait(
    *,
    execution_id: str,
    waiting: bool,
    completed: int,
    total: int,
) -> SSEEvent:
    """CEO 协调等待：captain 空等团队事件时的前端 UX 信号。

    Emitted from ``await_coordination_injection`` when a real ``wait_events`` blocks
    (enter ``waiting=true``; exit ``waiting=false``; long waits may refresh ≤15s).
    EPHEMERAL — transport-only; must NOT ride ``content_delta`` / journal.
    """
    return SSEEvent(
        type=EventType.COORDINATION_WAIT,
        payload={
            "execution_id": execution_id,
            "waiting": waiting,
            "completed": completed,
            "total": total,
        },
    )


def workspace_lock_wait(*, conversation_id: str, waiting: bool) -> SSEEvent:
    """同 folder 写锁短等：争锁前 ``waiting=true``，acquire 后 ``waiting=false``。

    Emitted via ``ServerWorkspace`` mutation-lock ``on_waiting`` (bound in
    ``build_turn_backend``) when a write contends. A′: kickoff no longer holds the
    folder lock. EPHEMERAL — no journal; clients must not render empty 「Thinking…」
    while this is true（不得静默等锁）.
    """
    return SSEEvent(
        type=EventType.WORKSPACE_LOCK_WAIT,
        payload={"conversation_id": conversation_id, "waiting": waiting},
    )


def team_synthesis_preview(
    *,
    execution_id: str,
    completed: int,
    total: int,
    headline: str,
    text: str,
    workers: list[dict[str, Any]],
    in_progress: bool = True,
) -> SSEEvent:
    """CEO 协调模式 Phase 1：多 worker 委派期间的确定性团队进展摘要。

    Emitted from ``drive._progress`` after each worker finishes when the plan has ≥2
    nodes. Template-only (no LLM) — verifies progressive visibility without changing
    ReAct / delegate blocking. DURABLE (P2)：落 journal；前端 fold 同 key 保最新，
    刷新后 StatusStrip / ProjectedTurn.teamSynthesisPreview 可重建。Must NOT reuse
    ``content_delta`` (would pollute the final CEO bubble).

    → 见 docs/03-AI核心/编排器与CEO主Agent.md §协调模式（合成通道）
    """
    return SSEEvent(
        type=EventType.TEAM_SYNTHESIS_PREVIEW,
        payload={
            "execution_id": execution_id,
            "completed": completed,
            "total": total,
            "headline": headline,
            "text": text,
            "workers": workers,
            "in_progress": in_progress,
        },
    )


def delivery_status(
    *,
    execution_id: str,
    state: str,
    summary: str,
    delivered_files: list[str],
    gaps: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]] | None = None,
    promoted: list[dict[str, str]] | None = None,
) -> SSEEvent:
    """交付状态（能力闸门与交付诚实性）：delegate 批次收尾的结构化交付对账。

    Deterministic (template-only, no LLM), built from the wrap-up signals the engine
    already has — path-level ``file_acceptance`` / ``artifacts``, contract /
    handoff gaps (含 degraded 交接、artifacts 对账缺口、soft overlay notes),
    and derived user actions (如云端无执行环境 → ``bind_local_folder``). ``state`` ∈
    delivered / partial / blocked / notes（软自注 unverified_note 不单独 → notes；
    path_mismatch 为声明未落盘的 blocking gap，不得 delivered；未声明落盘不进 artifacts）.
    ``artifacts`` = path acceptance rows；``delivered_files`` = accepted only.
    ``gaps`` items are ``{role, description}`` plus optional ``reason`` /
    ``severity`` / ``paths``；``actions`` 已知 kind 含 ``bind_local_folder`` /
    ``website_verify`` / ``continue_skipped_runs``（已撤 ``continue_writing``）.
    ``promoted`` = 历史 ``{from, to}`` 归位行（``promote_product`` 已撤销，新回合不再写入）。
    零归位是合法状态，此时整条 key 不上 wire（客户端按缺省空数组读）。
    DURABLE：落 journal；folds 同 ``execution_id`` 保最新。
    Must NOT ride ``content_delta``（终稿正文与交付对账分离）。
    """
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "state": state,
        "summary": summary,
        "delivered_files": delivered_files,
        "gaps": gaps,
        "actions": actions,
        "artifacts": list(artifacts or []),
    }
    if promoted:
        payload["promoted"] = list(promoted)
    return SSEEvent(type=EventType.DELIVERY_STATUS, payload=payload)


def user_interjection(
    *,
    interjection_id: str,
    execution_id: str,
    content: str,
    status: str = "received",
    note: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
) -> SSEEvent:
    """运行中用户插话（经典 steer + 协调插话共用契约）。

    ``status=received`` 入队确认；真正写入模型上下文 → ``injected``；
    协调图内处置 → ``addressed``；转 FIFO / 收口升格 → ``queued``；真失败 → ``failed``
    （同 ``interjection_id`` 保最新）。经典无 ``addressed``（``injected`` 即终态）。
    DURABLE——落 journal，刷新可回看。``attachments`` 为名字 + 路径 + 二进制标记。
    ``agent_mentions`` 为软点名芯片（``{agent_id, role}``）；空则不上 wire。
    """
    from agentcore.conversation.mentions import wire_agent_mentions

    payload: dict[str, Any] = {
        "interjection_id": interjection_id,
        "execution_id": execution_id,
        "content": content,
        "status": status,
    }
    if note is not None and note.strip():
        payload["note"] = note.strip()
    if attachments:
        payload["attachments"] = attachments
    mentions = wire_agent_mentions(agent_mentions)
    if mentions:
        payload["agent_mentions"] = mentions
    return SSEEvent(type=EventType.USER_INTERJECTION, payload=payload)


def turn_queued(
    *,
    queue_id: str,
    position: int,
    queue_depth: int,
    conversation_id: str,
    degraded_from: str | None = None,
) -> SSEEvent:
    """同对话 FIFO 排队 ack（D9 · 发送即有流）——取代退役的 HTTP 202 queued JSON。

    ``degraded_from="steer"`` when classic in-flight could not soft-insert
    (无 accepting 窗口 / 回合已收口 → 回落 FIFO).
    """
    payload: dict[str, Any] = {
        "queue_id": queue_id,
        "position": position,
        "queue_depth": queue_depth,
        "conversation_id": conversation_id,
    }
    if degraded_from is not None:
        payload["degraded_from"] = degraded_from
    return SSEEvent(type=EventType.TURN_QUEUED, payload=payload)


def turn_queue_started(
    *,
    queue_id: str,
    conversation_id: str,
    remaining_depth: int,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    agent_mentions: list[dict[str, Any]] | None = None,
) -> SSEEvent:
    """同对话 FIFO 出队开跑——新回合 sink 首帧（先于 ``message_start``）。

    自描述时间线入场（正文在帧上）。``attachments`` / ``agent_mentions`` 空则不上 wire。
    EPHEMERAL——不落 journal；reload 靠 REST。
    """
    from agentcore.conversation.mentions import wire_agent_mentions

    payload: dict[str, Any] = {
        "queue_id": queue_id,
        "conversation_id": conversation_id,
        "remaining_depth": remaining_depth,
        "content": content,
    }
    if attachments:
        payload["attachments"] = attachments
    mentions = wire_agent_mentions(agent_mentions)
    if mentions:
        payload["agent_mentions"] = mentions
    return SSEEvent(type=EventType.TURN_QUEUE_STARTED, payload=payload)


def turn_queue_cancelled(*, queue_id: str, conversation_id: str) -> SSEEvent:
    """同对话排队项取消 ack（多端清 UI）。"""
    return SSEEvent(
        type=EventType.TURN_QUEUE_CANCELLED,
        payload={"queue_id": queue_id, "conversation_id": conversation_id},
    )


def execution_detached(
    *,
    execution_id: str,
    conversation_id: str,
    completed: int,
    total: int,
    reason: str | None = None,
    host_turn_id: str | None = None,
) -> SSEEvent:
    """执行转后台：附着回合已收口，团队继续跑（批次 1 · 异步团队产出投递）。"""
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "conversation_id": conversation_id,
        "completed": completed,
        "total": total,
    }
    if reason and reason.strip():
        payload["reason"] = reason.strip()
    if host_turn_id and host_turn_id.strip():
        payload["host_turn_id"] = host_turn_id.strip()
    return SSEEvent(type=EventType.EXECUTION_DETACHED, payload=payload)


def execution_completed(
    *,
    execution_id: str,
    conversation_id: str,
    completed: int,
    total: int,
    status: str = "completed",
    host_turn_id: str | None = None,
    error: str | None = None,
) -> SSEEvent:
    """后台执行终态：drive 到齐，收割者可发起系统收口回合。

    ``status`` mirrors harvest closing kind (success→completed / failure→failed /
    cancelled→cancelled) so clients fold UI to cancelled/failed instead of
    「团队完成」when the batch was stopped or failed.
    """
    st = (status or "completed").strip().lower()
    if st not in ("completed", "failed", "cancelled"):
        st = "completed"
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "conversation_id": conversation_id,
        "completed": completed,
        "total": total,
        "status": st,
    }
    if host_turn_id and host_turn_id.strip():
        payload["host_turn_id"] = host_turn_id.strip()
    if error and error.strip():
        payload["error"] = error.strip()
    return SSEEvent(type=EventType.EXECUTION_COMPLETED, payload=payload)


def batch_metrics(*, execution_id: str, metrics: dict[str, Any]) -> SSEEvent:
    """WaveScheduler 观测快照（调度埋点量化）→ 桌面诊断模式。

    ``metrics`` 是 :class:`~agentcore.runtime.runs.types.BatchMetrics` 的 asdict
    （nodes / width / wall_ms / busy_ms / slot_starved / 受监督波循环 + escalate）。
    每段调度一条，fold 累成 ``Execution.batches``；journaled，手机 fold 空操作。
    """
    return SSEEvent(
        type=EventType.BATCH_METRICS,
        payload={"execution_id": execution_id, **metrics},
    )
