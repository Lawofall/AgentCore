"""Rewind projects the model window. The journal stays the full record.

A rewind keeps the original user request, the target segment and its ancestors'
conclusions, and one scar line. Cut segments stay in the record and are marked
已切出 for the graph. They are not shown to the model again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAX_MODEL_REWINDS = 2
_GOAL_CLIP = 24
_CONCLUSION_CLIP = 500

NOT_SEALED_MSG = "只能回到已经封口的一段。"
UNKNOWN_TARGET_MSG = "图上没有这一段。"
CAP_MSG = "自己回转已到上限。把判断交给用户。"


class RewindRejected(Exception):
    """The target cannot be rewound to. ``message`` is what the caller shows."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RewindNode:
    id: str
    depends_on: tuple[str, ...]
    goal: str
    conclusion: str
    sealed: bool


@dataclass(frozen=True, slots=True)
class RewindPlan:
    target_id: str
    kept_ids: tuple[str, ...]
    cut_ids: tuple[str, ...]
    scar: str
    conclusions: tuple[tuple[str, str], ...]
    reason: str
    original_request: str


def clip_label(text: str, *, limit: int = _GOAL_CLIP) -> str:
    body = " ".join((text or "").split())
    if not body:
        return ""
    if len(body) <= limit:
        return body
    return body[:limit] + "…"


def scar_line(labels: Sequence[str], reason: str) -> str:
    names = "、".join(label for label in labels if label) or "没有丢掉的段"
    why = " ".join((reason or "").split()) or "未写原因"
    return f"已切出：{names}。原因：{why}"


def conclusions_text(conclusions: Sequence[tuple[str, str]]) -> str:
    lines = ["这段以及前面各段的结论："]
    for node_id, text in conclusions:
        body = (text or "").strip() or "（这段没有写下结论）"
        lines.append(f"- {node_id}：{body}")
    return "\n".join(lines)


def memory_text(conclusions: Sequence[tuple[str, str]], scar: str) -> str:
    return conclusions_text(conclusions) + "\n\n" + scar


def plan_rewind(
    nodes: Sequence[RewindNode],
    target_id: str,
    reason: str,
    *,
    original_request: str = "",
) -> RewindPlan:
    """Keep the target and its ancestors. Everything else on the graph is cut."""
    by_id = {node.id: node for node in nodes}
    target = by_id.get(target_id)
    if target is None:
        raise RewindRejected("unknown", UNKNOWN_TARGET_MSG)
    if not target.sealed:
        raise RewindRejected("not_sealed", NOT_SEALED_MSG)

    kept: list[str] = []
    seen: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in seen or node_id not in by_id:
            return
        seen.add(node_id)
        for dep in by_id[node_id].depends_on:
            walk(dep)
        kept.append(node_id)

    walk(target_id)
    kept_set = set(kept)
    cut_ids = tuple(node.id for node in nodes if node.id not in kept_set)
    labels = [
        clip_label(by_id[node_id].goal) or node_id
        for node_id in cut_ids
    ]
    conclusions = tuple(
        (node_id, by_id[node_id].conclusion.strip()) for node_id in kept
    )
    return RewindPlan(
        target_id=target_id,
        kept_ids=tuple(kept),
        cut_ids=cut_ids,
        scar=scar_line(labels, reason),
        conclusions=conclusions,
        reason=reason,
        original_request=original_request,
    )


def model_rewind_count(entries: Sequence[dict[str, Any]] | None) -> int:
    count = 0
    for entry in entries or []:
        if (entry.get("kind") or "") != "graph_rewind":
            continue
        payload = entry.get("payload") or {}
        if isinstance(payload, dict) and payload.get("source") == "model":
            count += 1
    return count


def latest_rewind_payload(entries: Sequence[dict[str, Any]] | None) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for entry in entries or []:
        if (entry.get("kind") or "") != "graph_rewind":
            continue
        payload = entry.get("payload") or {}
        if isinstance(payload, dict):
            last = payload
    return last


def history_row(payload: dict[str, Any]) -> dict[str, str]:
    """One assistant row: conclusions plus the scar. The user request stays the prior message."""
    conclusions = _conclusions_from_payload(payload)
    scar = str(payload.get("scar") or "")
    return {"role": "assistant", "content": memory_text(conclusions, scar)}


def nodes_from_journal(
    entries: Sequence[dict[str, Any]] | None,
) -> tuple[list[RewindNode], str]:
    """Graph nodes and the turn's original user request, from an append-only journal."""
    original = ""
    order: list[str] = []
    specs: dict[str, tuple[str, tuple[str, ...]]] = {}
    conclusions: dict[str, str] = {}
    sealed: set[str] = set()

    for entry in entries or []:
        kind = entry.get("kind") or ""
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if kind == "turn_started" and not original:
            original = str(payload.get("user_message") or "")
        elif kind == "plan_snapshot":
            _take_specs(order, specs, payload.get("nodes") or [], id_key="run_id")
        elif kind == "run_plan":
            _take_specs(order, specs, payload.get("runs") or [], id_key="id")
        elif kind == "run_completed":
            rid = str(payload.get("run_id") or "")
            if not rid:
                continue
            sealed.add(rid)
            text = str(payload.get("output_summary") or "").strip()
            debrief = payload.get("debrief") or {}
            if not text and isinstance(debrief, dict):
                text = str(debrief.get("summary") or "").strip()
            if text:
                conclusions[rid] = text
        elif kind == "message_final":
            rid = str(payload.get("run_id") or "")
            if not rid or rid in conclusions:
                continue
            text = str(payload.get("content") or "").strip()
            if not text:
                continue
            conclusions[rid] = text[:_CONCLUSION_CLIP]
            sealed.add(rid)

    nodes = [
        RewindNode(
            id=node_id,
            depends_on=specs[node_id][1],
            goal=specs[node_id][0],
            conclusion=conclusions.get(node_id, ""),
            sealed=node_id in sealed,
        )
        for node_id in order
    ]
    return nodes, original


def seal_snapshot_id(entries: Sequence[dict[str, Any]] | None, run_id: str) -> str:
    found = ""
    for entry in entries or []:
        if (entry.get("kind") or "") != "seal_snapshot":
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("run_id") or "") != run_id:
            continue
        found = str(payload.get("snapshot_id") or "")
    return found


def project_journal_window(
    messages: list[Any],
    entries: Sequence[dict[str, Any]] | None,
    run_id: str,
) -> list[Any]:
    """Resume fold: same projection as the live captain window, from journal facts."""
    payload = latest_rewind_payload(entries)
    if not messages or not payload or not run_id:
        return messages
    if str(payload.get("window_run_id") or "") != run_id:
        return messages
    return _project_live(messages, payload)


def project_captain_window(messages: list[Any], run_id: str) -> list[Any]:
    """Replace a captain window after a model rewind. Other runs are unchanged."""
    if not run_id or not messages:
        return messages
    from agentcore.runtime.facts import current_fact_log

    log = current_fact_log.get()
    if log is None:
        return messages
    payload = latest_rewind_payload(log.entries())
    if not payload or str(payload.get("window_run_id") or "") != run_id:
        return messages
    return _project_live(messages, payload)


def _project_live(messages: list[Any], payload: dict[str, Any]) -> list[Any]:
    from agentcore.llm.provider.protocol import LLMMessage

    systems: list[Any] = []
    index = 0
    while index < len(messages) and getattr(messages[index], "role", None) == "system":
        systems.append(messages[index])
        index += 1
    tail_at = _rewind_call_index(messages)
    tail = messages[tail_at:] if tail_at is not None else []
    original = str(payload.get("original_request") or "").strip()
    conclusions = _conclusions_from_payload(payload)
    scar = str(payload.get("scar") or "")
    out: list[Any] = list(systems)
    if original:
        out.append(LLMMessage(role="user", content=original))
    out.append(LLMMessage(role="assistant", content=conclusions_text(conclusions)))
    if scar:
        out.append(LLMMessage(role="user", content=scar))
    out.extend(tail)
    return out


def _rewind_call_index(messages: Sequence[Any]) -> int | None:
    last: int | None = None
    for index, message in enumerate(messages):
        if getattr(message, "role", None) != "assistant":
            continue
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            if getattr(function, "name", None) == "rewind":
                last = index
    return last


def _conclusions_from_payload(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    raw = payload.get("conclusions") or []
    rows: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or "")
        if node_id:
            rows.append((node_id, str(item.get("text") or "")))
    return tuple(rows)


def _take_specs(
    order: list[str],
    specs: dict[str, tuple[str, tuple[str, ...]]],
    raw_nodes: Any,
    *,
    id_key: str,
) -> None:
    if not isinstance(raw_nodes, list):
        return
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get(id_key) or "")
        if not node_id:
            continue
        if node_id not in specs:
            order.append(node_id)
        deps_raw = node.get("depends_on") or []
        deps = tuple(str(dep) for dep in deps_raw if dep) if isinstance(deps_raw, list) else ()
        goal = str(node.get("task") or node.get("goal") or node.get("role") or node_id)
        specs[node_id] = (goal, deps)
