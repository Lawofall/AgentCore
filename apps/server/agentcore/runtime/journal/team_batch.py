"""本回合团队状态——由 turn journal 派生的一等结构量。

三态取代「None | Verdict」的二义：``no_batch`` 是确定的「本轮未派工」，不是信息缺失。
计数 = 本波 kickoff 编制（最新一张非 divert 的 ``run_plan``），不含 captain、不含
journal 里更早 execution 的历史队员。不复用 ``execution.progress`` /
``coordination_wait`` / ``workerProgress`` 三套口径。

消费口只有两条既有通路：``message_end``（前端）与 ``persist_turn_journal`` →
``obs.turn_spans``（云端观测）。本模块只做纯函数投影。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentcore.runtime.terminal import RUN_CLOSE_EVENT_TYPES

_CAPTAIN = "captain"
_STARTED = "run_started"
_PLAN = "run_plan"
_APPEND = "graph_append"
_DELIVERY = "delivery_status"

NO_BATCH: dict[str, Any] = {"kind": "no_batch"}


def _label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("kind") or item.get("type") or "")
    t = getattr(item, "type", None)
    if t is None:
        return ""
    return str(getattr(t, "value", t) or "")


def _payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        raw = item.get("payload")
        return raw if isinstance(raw, dict) else {}
    raw = getattr(item, "payload", None)
    return raw if isinstance(raw, dict) else {}


def _walk(entries: Iterable[Any] | None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for item in entries or ():
        kind = _label(item)
        if kind:
            out.append((kind, _payload(item)))
    return out


def _plan_worker_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for run in payload.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("kind") or "") == _CAPTAIN:
            continue
        rid = str(run.get("id") or "").strip()
        if rid:
            ids.add(rid)
    return ids


def team_batch_from_entries(entries: Iterable[Any] | None) -> dict[str, Any]:
    """Project this turn's team-batch status from journal / sink display events.

    Always returns a wire dict: ``no_batch`` / ``in_flight`` / ``settled``.
    """
    frames = _walk(entries)
    latest_eid = ""
    for kind, payload in frames:
        if (kind == _PLAN and not payload.get("host_message_id")) or kind == _APPEND:
            eid = str(payload.get("execution_id") or "").strip()
            if eid:
                latest_eid = eid

    worker_ids: set[str] = set()
    for kind, payload in frames:
        if kind == _PLAN:
            if payload.get("host_message_id"):
                continue
            eid = str(payload.get("execution_id") or "").strip()
            if latest_eid and eid and eid != latest_eid:
                continue
            worker_ids.update(_plan_worker_ids(payload))
        elif kind == _APPEND:
            for rid in payload.get("added_run_ids") or []:
                text = str(rid or "").strip()
                if text:
                    worker_ids.add(text)

    if not worker_ids:
        return dict(NO_BATCH)

    started: set[str] = set()
    terminal: set[str] = set()
    for kind, payload in frames:
        rid = str(payload.get("run_id") or "").strip()
        if not rid or rid not in worker_ids:
            continue
        if str(payload.get("kind") or "") == _CAPTAIN:
            continue
        if kind == _STARTED:
            started.add(rid)
        elif kind in RUN_CLOSE_EVENT_TYPES:
            terminal.add(rid)

    n = len(worker_ids)
    delivery_for_wave = False
    if latest_eid:
        for kind, payload in frames:
            if kind != _DELIVERY:
                continue
            if str(payload.get("execution_id") or "").strip() == latest_eid:
                delivery_for_wave = True
                break

    if not started and not terminal and not delivery_for_wave:
        # 开工预览 / 未真正 kickoff：编制在计划里，人还没派出。
        return dict(NO_BATCH)
    if delivery_for_wave or worker_ids <= terminal:
        return {"kind": "settled", "worker_count": n}
    return {"kind": "in_flight", "worker_count": n}
