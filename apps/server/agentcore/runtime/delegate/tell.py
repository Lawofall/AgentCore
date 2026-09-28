"""Classify ``replan.tell``: answer someone who stopped, or note someone not started.

The engine picks from the person's state. A running person is not a tell target.
"""

from __future__ import annotations

from typing import Any


def classify_tells(
    tells: list,
    *,
    session: Any | None,
    plan: Any | None,
    completed_ids: set[str],
) -> tuple[list[str], list[tuple[str, str]], list[dict[str, str]]]:
    """Return ``(errors, answers, steers)``.

    ``answers`` are ``(run_id, note)`` for workers parked on wait.
    ``steers`` are ``{run_id, note}`` for people who have not started.
    No mutation.
    """
    errors: list[str] = []
    answers: list[tuple[str, str]] = []
    steers: list[dict[str, str]] = []
    if not isinstance(tells, list):
        return ["replan 的 tell / add 必须是数组。"], [], []
    active = session is not None and bool(getattr(session, "active", False))
    running_ids = (
        set(getattr(session, "_running_workers", {}) or {}) if active else set()
    )

    for i, item in enumerate(tells):
        if not isinstance(item, dict):
            errors.append(f"tell[{i}] 必须是对象")
            continue
        raw = str(item.get("run_id") or "").strip()
        note = str(item.get("note") or "").strip()
        if not raw:
            errors.append(f"tell[{i}] 需要 run_id")
            continue
        if not note:
            errors.append(f"tell[{i}] 需要 note")
            continue

        if active:
            waiting = session.resolve_arbitration_target(raw)
            if waiting.reason == "ambiguous":
                listing = "；".join(waiting.candidates) or "（无）"
                errors.append(
                    f"tell[{i}]：「{raw}」同时匹配多个停着等拍板的人。"
                    f"请改用完整 run_id。候选：{listing}。"
                )
                continue
            if waiting.run_id:
                answers.append((waiting.run_id, note))
                continue

            running = session.resolve_cancel_target(raw)
            if running.reason == "ambiguous":
                listing = "；".join(running.candidates) or "（无）"
                errors.append(
                    f"tell[{i}]：「{raw}」同时匹配多个正在干的人。"
                    f"请改用完整 run_id。候选：{listing}。"
                )
                continue
            if running.run_id:
                errors.append(
                    f"tell[{i}]：{running.run_id} 正在干。要改方向用 cancel_worker。"
                )
                continue

            ended = session.resolve_ended_worker(raw)
            if ended.reason == "ambiguous":
                errors.append(
                    f"tell[{i}]：「{raw}」对应多个已做完的人。请改用完整 run_id。"
                )
                continue
            if ended.run_id:
                errors.append(f"tell[{i}]：{ended.run_id} 已经做完。")
                continue

            pending = session.resolve_pending_worker(raw)
            if pending.reason == "ambiguous":
                errors.append(
                    f"tell[{i}]：「{raw}」对应多个还没开始的人。请改用完整 run_id。"
                )
                continue
            if pending.run_id:
                steers.append({"run_id": pending.run_id, "note": note})
                continue

        run_id, plan_error = _plan_target(plan, raw, completed_ids, running_ids)
        if plan_error:
            errors.append(f"tell[{i}]：{plan_error}")
            continue
        if run_id:
            steers.append({"run_id": run_id, "note": note})
            continue
        errors.append(f"tell[{i}]：找不到「{raw}」。")
    return errors, answers, steers


def _plan_target(
    plan: Any | None,
    raw: str,
    completed_ids: set[str],
    running_ids: set[str],
) -> tuple[str | None, str | None]:
    """Exact run_id, or the only not-yet-started node with this role."""
    if plan is None or not raw:
        return None, None
    nodes = list(getattr(plan, "nodes", ()) or ())
    by_id = getattr(plan, "by_id", None)
    exact = by_id(raw) if callable(by_id) else None
    if exact is None:
        exact = next(
            (n for n in nodes if str(getattr(n, "run_id", "") or "") == raw),
            None,
        )
    if exact is not None:
        rid = str(getattr(exact, "run_id", "") or raw)
        if rid in completed_ids:
            return None, f"{raw} 已经做完。"
        if rid in running_ids:
            return None, f"{raw} 正在干。要改方向用 cancel_worker。"
        return rid, None
    role_hits = [
        str(getattr(n, "run_id", "") or "")
        for n in nodes
        if str(getattr(n, "role", "") or "") == raw
        and str(getattr(n, "run_id", "") or "") not in completed_ids
        and str(getattr(n, "run_id", "") or "") not in running_ids
    ]
    role_hits = [rid for rid in role_hits if rid]
    if len(role_hits) == 1:
        return role_hits[0], None
    if len(role_hits) > 1:
        return None, f"「{raw}」对应多个还没开始的人。请改用完整 run_id。"
    return None, None


def append_steer_notes(plan: Any, steers: list[dict[str, str]]) -> None:
    """Append pre-start notes onto live plan nodes. Missing ids are skipped."""
    for item in steers:
        rid = item.get("run_id") or ""
        note = item.get("note") or ""
        if not rid or not note or plan is None:
            continue
        by_id = getattr(plan, "by_id", None)
        node = by_id(rid) if callable(by_id) else None
        if node is None:
            continue
        current = str(getattr(node, "steer", "") or "")
        node.steer = f"{current}\n- {note}" if current else f"- {note}"
