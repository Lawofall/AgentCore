"""项目级协作时间线 — 读时聚合投影（批 D+ · 项目级团队图 v1+v1.5）。

不新建文件夹级 execution / 边表。输入是会话级 ``turn_journal`` 事实 + 会话元数据；
输出是「会话 + 幕序列摘要 + 约定文档引用条」的只读投影。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Conversation, TurnJournalRow
from agentcore.workspace.stage_dirs import RESEARCH_DIR, RESEARCH_PREFIX

DossierSource = Literal["dossier_inject", "file_read"]

_DOSSIER_REFS_NOTE = (
    "路径级约定文档消费事实（本场辩论开赛注入或会话内 file_read），非跨会话过程边"
)

_KIND_FALLBACK_TITLE: dict[str, str] = {
    "debate": "辩论对抗",
    "multi_agent": "团队协作",
}

_FILE_READ_TOOLS = frozenset({"file_read"})


@dataclass
class TimelineAct:
    act_id: str
    kind: str
    title: str | None
    started_at: datetime | None = None


@dataclass
class DossierRef:
    path: str
    sources: list[DossierSource] = field(default_factory=list)


@dataclass
class TimelineItem:
    conversation_id: str
    title: str | None
    updated_at: datetime
    execution_id: str
    host_turn_id: str
    acts: list[TimelineAct]
    dossier_refs: list[DossierRef]


@dataclass
class TimelineResult:
    folder_id: str
    items: list[TimelineItem]
    total: int
    limit: int
    offset: int
    dossier_refs_note: str = _DOSSIER_REFS_NOTE


def display_act_title(*, kind: str, title: str | None) -> str | None:
    """Wire title when present; else kind fallback for UI chain labels."""
    t = (title or "").strip()
    if t:
        return t
    return _KIND_FALLBACK_TITLE.get(kind)


def _act_from_plan(p: dict[str, Any]) -> dict[str, Any]:
    """Resolve act declaration (parity with fold / conformance · 旧单幕合成 act-1)."""
    raw = p.get("act")
    if isinstance(raw, dict) and raw.get("act_id"):
        kind = raw.get("kind") or "multi_agent"
        if kind not in ("multi_agent", "debate"):
            kind = "multi_agent"
        return {
            "actId": str(raw["act_id"]),
            "kind": kind,
            "title": raw.get("title"),
        }
    plan_type = p.get("plan_type") or "multi_agent"
    kind = plan_type if plan_type in ("multi_agent", "debate") else "multi_agent"
    return {"actId": "act-1", "kind": kind, "title": None}


def _norm_path(raw: str) -> str:
    return (raw or "").strip().replace("\\", "/")


def _is_research_path(path: str) -> bool:
    p = _norm_path(path)
    return bool(p) and (p == RESEARCH_DIR or p.startswith(RESEARCH_PREFIX))


def _tool_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tool_name") or payload.get("name") or payload.get("tool") or ""
    ).strip().lower()


def _file_read_path(payload: dict[str, Any]) -> str:
    args = payload.get("arguments")
    if not isinstance(args, dict):
        args = {}
    blob = str(args.get("path") or args.get("file") or args.get("target") or "").strip()
    if blob:
        return _norm_path(blob)
    # Rare: path embedded in stringified args
    fallback = str(args) if args else ""
    norm = fallback.replace("\\", "/")
    if RESEARCH_PREFIX in norm:
        i = norm.find(RESEARCH_PREFIX)
        if i >= 0:
            frag = norm[i:].split()[0].strip("'\",)}")
            return _norm_path(frag)
    return ""


def _parse_ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def extract_acts_from_journal(entries: list[dict[str, Any]]) -> tuple[str, str, list[TimelineAct]]:
    """Scan journal facts → ``(execution_id, host_turn_id, acts)`` for the latest execution.

    - Groups ``run_plan`` by ``execution_id``; picks the execution with the newest plan ts.
    - Acts follow fold compatibility (:func:`_act_from_plan`); order = first-seen act_id.
    - ``host_turn_id`` = earliest turn carrying a ``run_plan`` for that execution
      (prefer plans without ``host_message_id`` divert).
    """
    # execution_id → {act_id → TimelineAct}, first-seen order, max_ts, host candidates
    by_exec: dict[str, dict[str, Any]] = {}

    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "").strip()
        if kind != "run_plan":
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        eid = str(payload.get("execution_id") or "").strip()
        if not eid:
            continue
        turn_id = str(entry.get("turn_id") or "").strip()
        ts = _parse_ts(entry.get("ts") or entry.get("created_at"))
        act_raw = _act_from_plan(payload)
        act_id = str(act_raw.get("actId") or "act-1")
        act_kind = str(act_raw.get("kind") or "multi_agent")
        title = act_raw.get("title")
        title_s = str(title).strip() if title is not None else None
        if title_s == "":
            title_s = None

        bucket = by_exec.setdefault(
            eid,
            {
                "acts": {},  # act_id → TimelineAct
                "order": [],
                "max_ts": None,
                "host_turn": None,
                "host_turn_diverted": None,
            },
        )
        if act_id not in bucket["acts"]:
            bucket["order"].append(act_id)
            bucket["acts"][act_id] = TimelineAct(
                act_id=act_id,
                kind=act_kind if act_kind in ("multi_agent", "debate") else "multi_agent",
                title=title_s,
                started_at=ts,
            )
        else:
            # Prefer a richer title / earlier started_at
            existing: TimelineAct = bucket["acts"][act_id]
            if existing.title is None and title_s:
                existing.title = title_s
            if existing.started_at is None or (ts and ts < existing.started_at):
                existing.started_at = ts
            if act_kind in ("multi_agent", "debate"):
                existing.kind = act_kind

        if ts is not None and (bucket["max_ts"] is None or ts > bucket["max_ts"]):
            bucket["max_ts"] = ts

        host_msg = str(payload.get("host_message_id") or "").strip()
        if turn_id:
            if not host_msg:
                if bucket["host_turn"] is None:
                    bucket["host_turn"] = turn_id
            elif bucket["host_turn_diverted"] is None:
                bucket["host_turn_diverted"] = turn_id

    if not by_exec:
        return "", "", []

    # Latest execution by max_ts (None ts sorts last)
    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, datetime]:
        eid, b = item
        ts = b["max_ts"]
        if ts is None:
            return (0, datetime.min)
        return (1, ts)

    eid, bucket = max(by_exec.items(), key=_sort_key)
    host = bucket["host_turn"] or bucket["host_turn_diverted"] or ""
    acts = [bucket["acts"][aid] for aid in bucket["order"]]
    return eid, host, acts


def extract_dossier_refs(entries: list[dict[str, Any]]) -> list[DossierRef]:
    """Path-level 约定文档消费事实 from journal (dossier inject + research/ file_read)."""
    sources_by_path: dict[str, set[DossierSource]] = {}

    def _add(path: str, source: DossierSource) -> None:
        p = _norm_path(path)
        if not _is_research_path(p):
            return
        # Normalize directory-only to skip; keep file paths
        if p.rstrip("/") == RESEARCH_DIR:
            return
        sources_by_path.setdefault(p, set()).add(source)

    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "").strip()
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}

        if kind == "evidence_ledger":
            for key in ("delta", "entries"):
                rows = payload.get(key)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    side = str(row.get("side_key") or "").strip()
                    dpath = str(row.get("dossier_path") or "").strip()
                    if dpath and (side == "dossier" or _is_research_path(dpath)):
                        _add(dpath, "dossier_inject")

        if (
            kind in {"tool_use_start", "tool_call_started"}
            and _tool_name(payload) in _FILE_READ_TOOLS
        ):
            path = _file_read_path(payload)
            if path:
                _add(path, "file_read")

    # Stable order: path sorted
    out: list[DossierRef] = []
    for path in sorted(sources_by_path):
        srcs = sources_by_path[path]
        ordered: list[DossierSource] = []
        if "dossier_inject" in srcs:
            ordered.append("dossier_inject")
        if "file_read" in srcs:
            ordered.append("file_read")
        out.append(DossierRef(path=path, sources=ordered))
    return out


def project_conversation_timeline(
    *,
    conversation_id: str,
    title: str | None,
    updated_at: datetime,
    entries: list[dict[str, Any]],
) -> TimelineItem | None:
    """Project one conversation's journal into a timeline item, or None if no execution."""
    execution_id, host_turn_id, acts = extract_acts_from_journal(entries)
    if not execution_id or not acts:
        return None
    if not host_turn_id:
        # Fallback: any turn_id on a run_plan for this execution
        for entry in entries:
            if str(entry.get("kind") or "") != "run_plan":
                continue
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            if str(payload.get("execution_id") or "") == execution_id:
                tid = str(entry.get("turn_id") or "").strip()
                if tid:
                    host_turn_id = tid
                    break
    if not host_turn_id:
        return None
    return TimelineItem(
        conversation_id=conversation_id,
        title=title,
        updated_at=updated_at,
        execution_id=execution_id,
        host_turn_id=host_turn_id,
        acts=acts,
        dossier_refs=extract_dossier_refs(entries),
    )


_FACT_KINDS = (
    "run_plan",
    "evidence_ledger",
    "tool_use_start",
    "tool_call_started",
)


async def list_folder_collaboration_timeline(
    session: AsyncSession,
    *,
    folder_id: str,
    user_id: str,  # noqa: ARG001 — caller already authorized; do not filter creator
    limit: int = 20,
    offset: int = 0,
) -> TimelineResult:
    """Desk-scoped read projection: folder → conversations with execution → act summary.

    ``user_id`` is the authorized caller (route already 404'd outsiders). Do not
    filter ``Conversation.user_id`` — member-opened threads belong on this desk.
    """
    lim = max(1, min(int(limit), 50))
    off = max(0, int(offset))

    has_plan = (
        select(TurnJournalRow.turn_id)
        .where(
            TurnJournalRow.conversation_id == Conversation.id,
            TurnJournalRow.kind == "run_plan",
        )
        .correlate(Conversation)
        .exists()
    )
    base = (
        select(Conversation)
        .where(
            Conversation.folder_id == folder_id,
            Conversation.deleted_at.is_(None),
            Conversation.archived_by_folder_delete.is_(False),
            Conversation.mode != "handoff",
            has_plan,
        )
    )

    total = int(
        (
            await session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        or 0
    )

    conv_result = await session.execute(
        base.order_by(Conversation.updated_at.desc()).limit(lim).offset(off)
    )
    conversations = list(conv_result.scalars().all())
    if not conversations:
        return TimelineResult(
            folder_id=folder_id, items=[], total=total, limit=lim, offset=off
        )

    ids = [c.id for c in conversations]
    fact_result = await session.execute(
        select(TurnJournalRow)
        .where(
            TurnJournalRow.conversation_id.in_(ids),
            TurnJournalRow.kind.in_(_FACT_KINDS),
        )
        .order_by(
            TurnJournalRow.conversation_id.asc(),
            TurnJournalRow.created_at.asc(),
            TurnJournalRow.seq.asc(),
        )
    )
    by_conv: dict[str, list[dict[str, Any]]] = {cid: [] for cid in ids}
    for row in fact_result.scalars().all():
        by_conv.setdefault(row.conversation_id, []).append(
            {
                "turn_id": row.turn_id,
                "kind": row.kind,
                "payload": row.payload or {},
                "ts": row.ts or row.created_at,
            }
        )

    items: list[TimelineItem] = []
    for c in conversations:
        item = project_conversation_timeline(
            conversation_id=c.id,
            title=c.title,
            updated_at=c.updated_at,
            entries=by_conv.get(c.id, []),
        )
        if item is not None:
            items.append(item)

    return TimelineResult(
        folder_id=folder_id, items=items, total=total, limit=lim, offset=off
    )
