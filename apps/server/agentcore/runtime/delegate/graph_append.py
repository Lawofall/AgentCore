"""协作图身份辅助：解析上一张图、加载宿主 journal、CEO 回显。

跨回合 divert（同 execution 续写宿主 journal / ``graph_append`` 事件）已退役。
新回合 = 新 ``execution_id`` + 可选 ``prev_execution_id`` 图间链。

与同回合二次 ``delegate``（协调 session merge / ``_last_graph_*`` 内存合入）正交。
``continue_from_run_id`` / ``replaces_run_id`` 出现时引擎自动写 ``prev_execution_id``，
仍不合入旧图。
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.events.types import EventType

logger = get_logger(__name__)

# execution_id → host assistant message_id（进程内；首张 run_plan 登记，DB 冷查兜底）
_host_by_execution: dict[str, str] = {}


def register_graph_host(execution_id: str, host_message_id: str) -> None:
    """Remember which assistant message owns ``execution_id`` (first run_plan wins)."""
    eid = (execution_id or "").strip()
    mid = (host_message_id or "").strip()
    if not eid or not mid:
        return
    _host_by_execution.setdefault(eid, mid)


def clear_graph_host_registry() -> None:
    """Test helper: drop the process-local host map."""
    _host_by_execution.clear()


def peek_graph_host(execution_id: str) -> str | None:
    eid = (execution_id or "").strip()
    return _host_by_execution.get(eid) if eid else None


async def resolve_host_message_id(
    *,
    conversation_id: str,
    execution_id: str,
) -> str | None:
    """Resolve the assistant message that owns ``execution_id``.

    Order: process-local registry → Postgres ``turn_journal`` scan.
    Used to load prior-graph journal (debate host attach / existence check), not divert.
    """
    cached = peek_graph_host(execution_id)
    if cached:
        return cached
    eid = (execution_id or "").strip()
    cid = (conversation_id or "").strip()
    if not eid or not cid:
        return None
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            found = await repo.find_turn_id_for_execution(
                conversation_id=cid, execution_id=eid
            )
            if found:
                register_graph_host(eid, found)
            return found
    except Exception as exc:  # noqa: BLE001 — resolve miss is a soft reject for CEO
        logger.warning(
            "graph_append.host_resolve_failed",
            execution_id=eid,
            conversation_id=cid,
            error=str(exc),
        )
        return None


async def resolve_latest_appendable_execution(
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
    prefer_message_id: str | None = None,
) -> str | None:
    """Resolve ``append_to_execution_id="latest"``: newest appendable graph (本回合优先).

    可追加 = 本对话内、``plan_type='multi_agent'`` 的团队协作图（辩论图不可追加）；宿主消息
    可解析等深校验仍由调用方把关。跨回合命中后作为 ``prev_execution_id``（新图 + 链），
    同回合 / adopt 热图仍合入同一 ``execution_id``。

    ``prefer_message_id``：该回合上已有 multi_agent 图则用之（同 turn 第一波收口后再
    ``latest`` 续派不得静默挂到跨 message 旧宿主）。``exclude_message_id`` 仅 prompt
    回显等场景排除本回合——delegate append 路径应传 prefer、勿 exclude。
    ``None`` = 无候选或查询失败——调用方必须把失败显式回给 CEO，禁止静默新建图。
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    prefer = (prefer_message_id or "").strip() or None
    exclude = (exclude_message_id or "").strip() or None
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            resolved: str | None = None
            via = "none"
            if prefer:
                preferred = await repo.find_latest_multi_agent_execution(
                    conversation_id=cid,
                    prefer_turn_id=prefer,
                    prefer_only=True,
                )
                if preferred:
                    resolved = preferred
                    via = "prefer_turn"
            if not resolved:
                resolved = await repo.find_latest_multi_agent_execution(
                    conversation_id=cid,
                    exclude_turn_id=exclude,
                )
                if resolved:
                    via = "conversation_excluded" if exclude else "conversation"
        logger.info(
            "delegate.graph_append_latest",
            conversation_id=cid,
            resolved=resolved,
            prefer_message_id=prefer,
            exclude_message_id=exclude,
            via=via,
        )
        return resolved
    except Exception as exc:  # noqa: BLE001 — resolve miss → None；tool 层自动降级新建
        logger.warning(
            "delegate.graph_append_latest",
            conversation_id=cid,
            resolved=None,
            prefer_message_id=prefer,
            exclude_message_id=exclude,
            via="error",
            error=str(exc),
        )
        return None


async def resolve_latest_mlr_execution(*, conversation_id: str) -> str | None:
    """Newest MLR-shaped ``multi_agent`` execution (含 ``synthesizer`` run) in the conversation.

    辩论第二幕 prev 链专用：不排除当前回合（同回合 MLR→开辩须命中本回合宿主）。
    分层：SQL synthesizer 形态优先 → 与 ``graph_append_latest`` 同池的 multi_agent
    候选再经 journal ``synthesizer_run_id`` 复核（对齐两套宿主查找，避免「appendable
    找得到、MLR 找不到」）。
    ``None`` = 无候选或查询失败——调用方回落独立辩论图。
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository
        from agentcore.runtime.kickoff.debate_host import synthesizer_run_id

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            resolved = await repo.find_latest_mlr_execution(conversation_id=cid)
            via = "mlr_sql"
            if not resolved:
                # 与 appendable 同池：最近 multi_agent 图 + journal 汇总员形态复核。
                candidate = await repo.find_latest_multi_agent_execution(
                    conversation_id=cid
                )
                if candidate:
                    host_mid = await resolve_host_message_id(
                        conversation_id=cid, execution_id=candidate
                    )
                    if host_mid:
                        entries = await repo.load(host_mid)
                        if synthesizer_run_id(entries):
                            resolved = candidate
                            via = "appendable_journal"
        logger.info(
            "debate.mlr_host_resolve",
            conversation_id=cid,
            resolved=resolved,
            via=via if resolved else "none",
        )
        return resolved
    except Exception as exc:  # noqa: BLE001 — miss → independent debate graph
        logger.warning(
            "debate.mlr_host_resolve",
            conversation_id=cid,
            resolved=None,
            error=str(exc),
        )
        return None


async def load_host_journal_entries(host_message_id: str) -> list[dict[str, Any]]:
    """Load host turn journal entries (``[]`` on miss / no DB)."""
    mid = (host_message_id or "").strip()
    if not mid:
        return []
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            return await repo.load(mid)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_append.host_journal_load_failed",
            host_message_id=mid,
            error=str(exc),
        )
        return []


# CEO-only volatile-tail note (跨回合可见回显通道): history replays only user/assistant
# text, so the tool-result echo alone would be dropped next turn. This block rides the
# CEO system prompt's volatile tail (assemble.py) — never the shared worker base.
#
# Facts only: worker count / role / terminal-or-running status / task brief. No talk-
# script, completion heuristics, or "don't lie" instructions (intercept-discipline).
_MAX_RECENT_GRAPH_WORKERS = 12
_MAX_RECENT_GRAPH_TASK_CHARS = 80

_RECENT_GRAPH_APPEND_NOTE = (
    "用户显式要求往这支团队继续加人 / 接着干时，delegate 可传 "
    'append_to_execution_id="latest"（引擎自动解析到最近一张图），'
    "或在 tasks[] 上填 continue_from_run_id / replaces_run_id："
    "跨回合会新开一队、接续上一张图；同回合二次派发合入同一张图。新任务默认仍新建图。"
    "要动上面某个具体队员（含本图已收口后）：在 tasks[] 上填其 run_id——"
    "让原作者带现场接着干用 continue_from_run_id，接手 failed/skipped 缺口用 "
    "replaces_run_id。"
)


def _worker_status_label(phase: Any) -> str:
    """Map a RunPhase (or missing → running) to the CEO-facing status token."""
    from agentcore.runtime.runs.types import RunPhase

    if phase is None:
        return "running"
    value = phase.value if isinstance(phase, RunPhase) else str(phase).strip().lower()
    if value in ("completed", "failed", "cancelled", "skipped"):
        return value
    # queued / running / retrying / unknown → still in flight from the next turn's view
    return "running"


def _task_brief(task: str) -> str:
    text = " ".join((task or "").split())
    if not text:
        return "—"
    if len(text) > _MAX_RECENT_GRAPH_TASK_CHARS:
        return text[:_MAX_RECENT_GRAPH_TASK_CHARS] + "…"
    return text


def format_recent_graph_worker_facts(
    plan: Any,
    completed: dict[str, Any] | None,
) -> str:
    """Minimal worker fact lines (role / status / task) for ``<recent_team_graph>``.

    ``plan`` is a :class:`~agentcore.runtime.runs.plan.RunPlan` (or None).
    Returns ``""`` when there are no worker nodes to list.
    """
    if plan is None:
        return ""
    nodes = getattr(plan, "nodes", None) or []
    if not nodes:
        return ""
    seed = completed or {}
    lines: list[str] = []
    for node in nodes[:_MAX_RECENT_GRAPH_WORKERS]:
        role = (
            str(getattr(node, "role", "") or "").strip()
            or str(getattr(node, "agent_name", "") or "").strip()
            or str(getattr(node, "run_id", "") or "").strip()
            or "—"
        )
        run_id = str(getattr(node, "run_id", "") or "").strip()
        state = seed.get(run_id) if run_id else None
        phase = getattr(state, "phase", None) if state is not None else None
        status = _worker_status_label(phase)
        task = _task_brief(str(getattr(node, "task", "") or ""))
        # run_id 是续派 / 补缺口入口的必填项——不给出来，收口后就只剩会被拒的冷派。
        lines.append(f"- run_id={run_id or '—'}; role={role}; status={status}; task={task}")
    extra = len(nodes) - _MAX_RECENT_GRAPH_WORKERS
    if extra > 0:
        lines.append(f"- …另有 {extra} 名 worker 未列出")
    return f"workers={len(nodes)}：\n" + "\n".join(lines)


def render_recent_graph_context(
    *,
    execution_id: str,
    worker_facts: str = "",
) -> str:
    """Assemble the ``<recent_team_graph>`` block (facts + append channel note)."""
    eid = (execution_id or "").strip()
    if not eid:
        return ""
    parts = [
        "<recent_team_graph>",
        "本对话最近一张协作图（团队执行）。",
    ]
    facts = (worker_facts or "").strip()
    if facts:
        parts.append(facts)
    parts.append(_RECENT_GRAPH_APPEND_NOTE)
    parts.append("</recent_team_graph>")
    return "\n".join(parts)


async def build_recent_graph_context(
    *,
    conversation_id: str,
    exclude_message_id: str | None = None,
) -> str:
    """The ``<recent_team_graph>`` prompt note, or ``""`` when the conversation has no graph.

    Resolves the newest appendable execution, then loads its host journal for a
    compact worker roster (count / role / status / task brief). Missing journal
    still yields the append channel note so ``latest`` stays usable. Does not
    print the graph execution_id to the model.
    """
    execution_id = await resolve_latest_appendable_execution(
        conversation_id=conversation_id,
        exclude_message_id=exclude_message_id,
    )
    if not execution_id:
        return ""
    worker_facts = ""
    host_mid = await resolve_host_message_id(
        conversation_id=conversation_id,
        execution_id=execution_id,
    )
    if host_mid:
        plan, completed = await load_host_plan_and_completed(host_mid)
        worker_facts = format_recent_graph_worker_facts(plan, completed)
    return render_recent_graph_context(
        execution_id=execution_id,
        worker_facts=worker_facts,
    )


def parse_host_captain_run_id(entries: list[dict[str, Any]] | None) -> str | None:
    """Parse the scene-level captain run id from host journal ``run_plan`` frames.

    Prefers frames without ``host_message_id`` (original host plan). Legacy growth /
    append frames may still carry a captain; those are a fallback only.
    Returns ``None`` when no ``kind=captain`` run is present.
    """
    if not entries:
        return None
    run_plan_label = EventType.RUN_PLAN.value
    fallback: str | None = None
    for entry in entries:
        label = entry.get("kind") or entry.get("type") or ""
        if label != run_plan_label:
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        captain_id: str | None = None
        for run in payload.get("runs") or []:
            if not isinstance(run, dict):
                continue
            if run.get("kind") != "captain":
                continue
            rid = run.get("id")
            if isinstance(rid, str) and rid.strip():
                captain_id = rid.strip()
                break
        if not captain_id:
            continue
        if not (payload.get("host_message_id") or "").strip():
            return captain_id
        if fallback is None:
            fallback = captain_id
    return fallback


async def load_host_plan_and_completed(
    host_message_id: str,
) -> tuple[Any | None, dict[str, Any]]:
    """Load host ``RunPlan`` + completed seed from the host turn journal."""
    from agentcore.runtime.journal.fold import completed_from_journal, plan_from_journal

    entries: list[dict[str, Any]] = []
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository

        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            entries = await repo.load(host_message_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_append.host_journal_load_failed",
            host_message_id=host_message_id,
            error=str(exc),
        )
        return None, {}
    plan = plan_from_journal(entries)
    completed = completed_from_journal(entries)
    return plan, completed
