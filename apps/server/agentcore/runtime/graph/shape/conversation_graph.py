"""One conversation, one collaboration graph.

Each turn still mints its own execution id and writes only this turn's new
segment. The reader folds every non-debate segment in the conversation into
one graph. A new segment's upstream is the leaves of the conclusions actually
present in this window — carried on the history row as ``graph_context``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

GRAPH_CONTEXT_KEY = "graph_context"

_OPEN = frozenset({"waiting", "running"})


def _label(entry: dict[str, Any]) -> str:
    return str(entry.get("kind") or entry.get("type") or "")


def _payload(entry: dict[str, Any]) -> dict[str, Any]:
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else {}


def _deps(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item)


def _cut_ids(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("cut_ids")
    if not isinstance(raw, list):
        raw = payload.get("cut_run_ids")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _kept_ids(payload: dict[str, Any]) -> list[str] | None:
    raw = payload.get("kept_ids")
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str) and item]
    conclusions = payload.get("conclusions")
    if not isinstance(conclusions, list):
        return None
    kept: list[str] = []
    for item in conclusions:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            kept.append(item["id"])
        elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str) and item[0]:
            kept.append(item[0])
    return kept or None


def _take_runs(
    order: list[str],
    deps: dict[str, tuple[str, ...]],
    payload: dict[str, Any],
    *,
    debate_ids: set[str],
) -> None:
    debate = str(payload.get("plan_type") or "") == "debate"
    for run in payload.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("id") or "")
        if not run_id:
            continue
        if debate:
            debate_ids.add(run_id)
            continue
        if run_id not in deps:
            order.append(run_id)
        deps[run_id] = _deps(run.get("depends_on"))


def visible_context(entries: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Sealed conclusions this turn's window still shows, in plan order.

    A rewind replaces the window with the kept conclusions, so later segments
    from that same turn are not context. Debate stays off this graph.
    """
    order: list[str] = []
    deps: dict[str, tuple[str, ...]] = {}
    sealed: set[str] = set()
    cut: set[str] = set()
    kept: list[str] | None = None
    debate_ids: set[str] = set()

    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        label = _label(entry)
        payload = _payload(entry)
        if label == "run_plan":
            _take_runs(order, deps, payload, debate_ids=debate_ids)
        elif label == "run_completed":
            run_id = str(payload.get("run_id") or "")
            if run_id:
                sealed.add(run_id)
        elif label == "message_final":
            run_id = str(payload.get("run_id") or "")
            text = str(payload.get("content") or "").strip()
            if run_id and text:
                sealed.add(run_id)
        elif label == "graph_rewind":
            cut.update(_cut_ids(payload))
            found = _kept_ids(payload)
            if found is not None:
                kept = found

    if kept is not None:
        visible = [run_id for run_id in kept if run_id in deps and run_id not in cut]
    else:
        visible = [
            run_id
            for run_id in order
            if run_id in sealed and run_id not in cut and run_id not in debate_ids
        ]
    return [{"id": run_id, "depends_on": list(deps[run_id])} for run_id in visible]


def _leaves(order: Sequence[str], deps: dict[str, tuple[str, ...]]) -> list[str]:
    present = set(deps)
    depended: set[str] = set()
    for parents in deps.values():
        for parent in parents:
            if parent in present:
                depended.add(parent)
    return [run_id for run_id in order if run_id not in depended]


def read_graph_context_ids(history: Sequence[dict[str, Any]] | None) -> list[str]:
    """Leaves of the conclusions still sitting in this window."""
    order: list[str] = []
    deps: dict[str, tuple[str, ...]] = {}
    for row in history or []:
        if not isinstance(row, dict):
            continue
        raw = row.get(GRAPH_CONTEXT_KEY)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            run_id = str(item.get("id") or "")
            if not run_id:
                continue
            if run_id not in deps:
                order.append(run_id)
            deps[run_id] = _deps(item.get("depends_on"))
    return _leaves(order, deps)


@dataclass(frozen=True, slots=True)
class ConversationSegment:
    id: str
    depends_on: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class ConversationGraph:
    segments: tuple[ConversationSegment, ...]

    @property
    def progress_denominator(self) -> int:
        """Only segments that are not sealed and not cut."""
        return sum(1 for segment in self.segments if segment.status in _OPEN)


def fold_conversation_graph(
    turns: Sequence[Sequence[dict[str, Any]] | None],
) -> ConversationGraph:
    """Fold every turn's graph events into one conversation graph.

    A new execution id does not start a new graph. Debate plans are left out.
    """
    order: list[str] = []
    deps: dict[str, tuple[str, ...]] = {}
    status: dict[str, str] = {}
    debate_ids: set[str] = set()

    for turn in turns:
        for entry in turn or []:
            if not isinstance(entry, dict):
                continue
            label = _label(entry)
            payload = _payload(entry)
            if label == "run_plan":
                before = len(order)
                _take_runs(order, deps, payload, debate_ids=debate_ids)
                for run_id in order[before:]:
                    status.setdefault(run_id, "waiting")
            elif label == "run_started":
                run_id = str(payload.get("run_id") or "")
                if status.get(run_id) == "waiting":
                    status[run_id] = "running"
            elif label == "run_completed":
                run_id = str(payload.get("run_id") or "")
                if run_id in status and status[run_id] != "cut":
                    status[run_id] = "sealed"
            elif label == "graph_rewind":
                for run_id in _cut_ids(payload):
                    if run_id in status:
                        status[run_id] = "cut"

    segments = tuple(
        ConversationSegment(id=run_id, depends_on=deps[run_id], status=status[run_id])
        for run_id in order
        if run_id not in debate_ids
    )
    return ConversationGraph(segments)
