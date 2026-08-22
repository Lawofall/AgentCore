"""Mid-coordination append overlap guard + C3 dispatch ownership.

When the live graph still has incomplete nodes, a secondary ``delegate`` that
claims the same **seat** (normalized role-name equality) is rejected.

**Seat model**: a seat is ``_norm_role(role)`` — whitespace-stripped lowercase
equality only. Shared job suffixes / CJK prefixes / edit distance do **not**
merge seats (痛点调研员 ≠ 定价调研员; 前端工程师 ≠ 测试工程师).

**Seat universe (new∪live)**: incomplete live holders **and** same-batch new
nodes share one seat map. Two new nodes whose roles collide after
``_norm_role`` without an ancestor edge **and** without disjoint deliverable
scopes (e.g.「V2专项测试员」vs「V2 专项测试员」with empty deliverables) are
rejected as ``sibling_role``. Serial ``depends_on`` and scoped fan-out
(same role, distinct deliverable names/artifacts) are legal — see
:mod:`append_sibling`.

**Seat reclaim**: FAILED / CANCELLED / SKIPPED (vacated) **and** successfully
COMPLETED same-seat terminals auto-fill ``replaces_run_id`` when a new node
reclaims the seat with no incomplete same-seat peer (file lock transfer +
depends_on rewrite via the existing replaces pipeline). Still-running holders
keep the seat; overlap still rejects.

**C3 file side**: deliverable artifacts consult the session ownership ledger.
**Completed** holders of a declared path are **not** append-rejected — dispatch
``declare_plan_artifacts`` handoffs those paths to the new node (审校→修订 /
同岗位补派). Still-running / incomplete holders keep blocking. Role-only
overlap still requires incomplete live nodes (plus same-batch peers above).

Same-batch sibling role / artifact crosses are rejected at dispatch (name the
pair), **before** durable ``run_plan`` emit (admit → commit → execute).

Cross-turn ``append_to_execution_id`` admits the **new batch only** against the
host plan + journal completed seed (auto-``replaces`` on free seats) — never
sibling-scan host∪new as one batch.

``replan.adds`` on an active coordination live plan reuses the same admit
(``admit_added_nodes``) + ``declare_plan_artifacts`` path as append merge.

Ownership keys are **desk × concrete file** ``artifacts`` only — directory
prefixes / ``artifact_dir`` / globs are acceptance coverage, never exclusive
claims. Same ``rel_path`` on different desks does not collide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.runtime.coordination.append_sibling import (
    AppendOverlap,
    _ancestors_for_plan,
    _norm_role,
    find_sibling_artifact_crosses,
    find_sibling_role_crosses,
    node_artifact_keys,
    node_artifact_paths,
    node_file_targets,
    node_ownership_desk,
    roles_overlap,
)
from agentcore.runtime.coordination.isomorphic import _node_role
from agentcore.workspace.write_claims import (
    WriteCoordinator,
    ownership_display_path,
)

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

# Re-export sibling gate + seat helpers for stable import paths.
__all__ = [
    "AppendOverlap",
    "admit_added_nodes",
    "append_overlap_reject_message",
    "apply_vacated_seat_replaces",
    "declare_nested_drive_artifacts",
    "declare_plan_artifacts",
    "find_append_overlaps",
    "find_sibling_artifact_crosses",
    "find_sibling_role_crosses",
    "handoff_owned_paths_on_complete",
    "has_incomplete_nodes",
    "node_artifact_keys",
    "node_artifact_paths",
    "node_file_targets",
    "node_ownership_desk",
    "roles_overlap",
]


def has_incomplete_nodes(
    live_plan: RunPlan | None,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
) -> bool:
    """True when the live graph still has nodes not yet terminal."""
    if live_plan is None or not live_plan.nodes:
        return False
    done = set(completed_run_ids or ())
    return any(n.run_id not in done for n in live_plan.nodes)


def apply_vacated_seat_replaces(
    new_plan: RunPlan,
    live_plan: RunPlan | None,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    vacated_run_ids: set[str] | frozenset[str] | None = None,
) -> list[tuple[str, str]]:
    """Auto-fill ``replaces_run_id`` when a new node reclaims a free seat.

    Free seat = vacated (FAILED / CANCELLED / SKIPPED) **or** successfully
    COMPLETED same-seat with no incomplete peer. Vacated candidates win over
    successful-complete when both exist for a seat. Explicit ``replaces_run_id``
    / ``continue_from_run_id`` are left untouched. Mutates matching new nodes;
    returns ``(new_run_id, old_run_id)`` pairs.
    """
    if live_plan is None or not new_plan.nodes:
        return []
    vacated = {str(x).strip() for x in (vacated_run_ids or ()) if str(x).strip()}
    done = {str(x).strip() for x in (completed_run_ids or ()) if str(x).strip()}
    if not vacated and not done:
        return []

    incomplete_seats: set[str] = set()
    vacated_by_seat: dict[str, list[Any]] = {}
    completed_by_seat: dict[str, list[Any]] = {}
    for live in live_plan.nodes:
        seat = _norm_role(_node_role(live))
        if not seat:
            continue
        rid = (live.run_id or "").strip()
        if not rid:
            continue
        if rid not in done:
            incomplete_seats.add(seat)
        elif rid in vacated:
            vacated_by_seat.setdefault(seat, []).append(live)
        else:
            # Successfully COMPLETED (token ceiling, normal finish, …) — same-seat
            # 续派/补派 inherits write locks without requiring explicit replaces.
            completed_by_seat.setdefault(seat, []).append(live)

    applied: list[tuple[str, str]] = []
    for nn in new_plan.nodes:
        if (getattr(nn, "replaces_run_id", None) or "").strip():
            continue
        if (getattr(nn, "continue_from_run_id", None) or "").strip():
            continue
        seat = _norm_role(_node_role(nn))
        if not seat or seat in incomplete_seats:
            continue
        # Prefer vacated (failed seat) over successful-complete for the same seat.
        pool = vacated_by_seat if seat in vacated_by_seat else completed_by_seat
        candidates = pool.get(seat) or []
        if not candidates:
            continue
        # Most recent holder of this seat (plan order).
        old = candidates.pop()
        nn.replaces_run_id = old.run_id
        applied.append((nn.run_id, old.run_id))
        if not candidates:
            pool.pop(seat, None)
    return applied


def find_append_overlaps(
    new_plan: RunPlan,
    live_plan: RunPlan | None,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    ownership: WriteCoordinator | None = None,
    birth_desk_id: str | None = None,
) -> list[AppendOverlap]:
    """Return overlaps between ``new_plan`` nodes and live / ownership holders."""
    if not new_plan.nodes:
        return []

    hits: list[AppendOverlap] = find_sibling_artifact_crosses(
        new_plan, birth_desk_id=birth_desk_id
    )

    if live_plan is None:
        return hits

    done = set(completed_run_ids or ())
    incomplete = [n for n in live_plan.nodes if n.run_id not in done]
    live_by_id = {n.run_id: n for n in live_plan.nodes}

    combined_ancestors = _ancestors_for_plan(live_plan)
    # New nodes may depend on live ids — fold their depends_on into ancestor sets.
    for nn in new_plan.nodes:
        deps = frozenset(getattr(nn, "depends_on", None) or ())
        extra = set(deps)
        for d in deps:
            extra |= set(combined_ancestors.get(d, frozenset()))
        combined_ancestors[nn.run_id] = frozenset(extra)

    for nn in new_plan.nodes:
        replaces = (getattr(nn, "replaces_run_id", None) or "").strip()
        continue_from = (getattr(nn, "continue_from_run_id", None) or "").strip()
        # Explicit replaces is plan surgery — skip overlap (transfer happens at declare).
        if replaces:
            continue
        n_role = _node_role(nn)
        n_desk = node_ownership_desk(nn, birth_desk_id=birth_desk_id)
        n_files = node_artifact_paths(nn)
        n_anc = set(combined_ancestors.get(nn.run_id, frozenset()))
        if continue_from:
            n_anc.add(continue_from)

        # --- Role overlaps: incomplete live nodes only (role gate retained) ---
        role_hit_live = None
        for live in incomplete:
            if roles_overlap(n_role, _node_role(live)):
                role_hit_live = live
                break

        # --- File overlaps ---
        # Completed holders are not append-rejected: declare_plan_artifacts will
        # dispatch_handoff those paths. Still-running owners keep blocking.
        file_hit_id: str | None = None
        file_hit_role = ""
        file_hit_path = ""
        if n_files and ownership is not None:
            for path in n_files:
                owner = ownership.owner_of(path, desk_id=n_desk)
                if owner is None:
                    continue
                if owner == nn.run_id or owner in n_anc:
                    continue
                if owner in done:
                    continue
                is_ended = getattr(ownership, "is_ended", None)
                if is_ended is not None and is_ended(owner):
                    continue
                file_hit_id = owner
                live_node = live_by_id.get(owner)
                file_hit_role = (_node_role(live_node) if live_node else "") or owner
                file_hit_path = path
                break

        if role_hit_live is None and file_hit_id is None:
            continue
        if role_hit_live is not None and file_hit_id is not None:
            role_id = role_hit_live.run_id
            if role_id == file_hit_id:
                hits.append(
                    AppendOverlap(
                        new_role=n_role or nn.run_id,
                        new_run_id=nn.run_id,
                        live_role=_node_role(role_hit_live) or role_hit_live.run_id,
                        live_run_id=role_id,
                        reason="role+deliverable",
                        path=file_hit_path,
                    )
                )
            else:
                # Different parties: report seat collision and file owner separately.
                hits.append(
                    AppendOverlap(
                        new_role=n_role or nn.run_id,
                        new_run_id=nn.run_id,
                        live_role=_node_role(role_hit_live) or role_hit_live.run_id,
                        live_run_id=role_id,
                        reason="role",
                    )
                )
                hits.append(
                    AppendOverlap(
                        new_role=n_role or nn.run_id,
                        new_run_id=nn.run_id,
                        live_role=file_hit_role,
                        live_run_id=file_hit_id or "",
                        reason="deliverable",
                        path=file_hit_path,
                    )
                )
        elif role_hit_live is not None:
            hits.append(
                AppendOverlap(
                    new_role=n_role or nn.run_id,
                    new_run_id=nn.run_id,
                    live_role=_node_role(role_hit_live) or role_hit_live.run_id,
                    live_run_id=role_hit_live.run_id,
                    reason="role",
                )
            )
        else:
            hits.append(
                AppendOverlap(
                    new_role=n_role or nn.run_id,
                    new_run_id=nn.run_id,
                    live_role=file_hit_role,
                    live_run_id=file_hit_id or "",
                    reason="deliverable",
                    path=file_hit_path,
                )
            )
    return hits


def admit_added_nodes(
    new_plan: RunPlan,
    live_plan: RunPlan | None,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    vacated_run_ids: set[str] | frozenset[str] | None = None,
    ownership: WriteCoordinator | None = None,
    force: bool = False,
    total_workers: int | None = None,
    birth_desk_id: str | None = None,
) -> str | None:
    """Seat reclaim + overlap gate shared by append merge and ``replan.adds``.

    Mutates ``new_plan`` nodes (auto-fills ``replaces_run_id`` for free seats).
    Returns the append-family reject message, or ``None`` when admitted.
    ``force`` still applies vacated-seat replaces but skips overlap rejection.
    """
    apply_vacated_seat_replaces(
        new_plan,
        live_plan,
        completed_run_ids=completed_run_ids,
        vacated_run_ids=vacated_run_ids,
    )
    if force:
        return None
    overlaps = find_append_overlaps(
        new_plan,
        live_plan,
        completed_run_ids=completed_run_ids,
        ownership=ownership,
        birth_desk_id=birth_desk_id,
    )
    if not overlaps:
        return None
    completed_k = len(set(completed_run_ids or ()))
    if total_workers is not None:
        total = int(total_workers)
    elif live_plan is not None:
        total = len(live_plan.nodes)
    else:
        total = 0
    return append_overlap_reject_message(
        overlaps, completed=completed_k, total=total
    )


def append_overlap_reject_message(
    overlaps: list[AppendOverlap],
    *,
    completed: int,
    total: int,
) -> str:
    """Structured rejection body for the delegate tool result."""
    if not overlaps:
        return (
            "【队员追加已拒绝·座位重叠】当前协作图仍有未完成节点"
            f"（已完成 {completed}/{total}），本次追加与现有计划冲突。"
            "请等待波次推进，或用 cancel_worker / replan / replaces_run_id "
            "显式调整现有计划后再派。"
            "已完成/已交接节点不能靠 cancel_worker 撤销，须 replaces_run_id 接手补派。"
        )
    all_sibling_artifact = all(o.reason == "sibling_artifact" for o in overlaps)
    all_sibling_role = all(o.reason == "sibling_role" for o in overlaps)
    all_same_batch = all(
        o.reason in ("sibling_artifact", "sibling_role") for o in overlaps
    )
    detail_parts: list[str] = []
    for o in overlaps:
        path_bit = f"`{o.path}`" if (o.path or "").strip() else ""
        if o.reason == "sibling_artifact":
            if path_bit:
                why = (
                    f"同批交付物交叉（路径 {path_bit}；"
                    f"`{o.live_run_id}` 与 `{o.new_run_id}`）"
                )
            else:
                why = f"同批交付物交叉（`{o.live_run_id}` 与 `{o.new_run_id}`）"
            detail_parts.append(f"【{o.new_role}】与【{o.live_role}】{why}")
        elif o.reason == "sibling_role":
            detail_parts.append(
                f"【{o.new_role}】与同批【{o.live_role}】（`{o.live_run_id}`）"
                "座位（角色名）重叠"
            )
        elif o.reason == "role":
            detail_parts.append(
                f"【{o.new_role}】与在图座位【{o.live_role}】（`{o.live_run_id}`）"
                "座位（角色名）重叠"
            )
        elif o.reason == "deliverable":
            path_note = f"（路径 {path_bit}）" if path_bit else ""
            detail_parts.append(
                f"【{o.new_role}】与文件主人【{o.live_role}】（`{o.live_run_id}`）"
                f"交付物/文件归属重叠{path_note}"
            )
        elif o.reason == "role+deliverable":
            path_note = f"（路径 {path_bit}）" if path_bit else ""
            detail_parts.append(
                f"【{o.new_role}】与在图【{o.live_role}】（`{o.live_run_id}`）"
                f"座位与文件归属均重叠{path_note}"
            )
        else:
            detail_parts.append(
                f"【{o.new_role}】与在图【{o.live_role}】（`{o.live_run_id}`）{o.reason}"
            )
    detail = "；".join(detail_parts)
    if all_sibling_artifact:
        return (
            "【同批交付物交叉已拒绝】"
            f"（本批 {total} 人）。冲突：{detail}。"
            "请为并行队员分配不同文件路径，或用 depends_on 标明交接关系后再派；"
            "跨文件夹同相对路径不互拦——确认是否误用同一 target_folder_id。"
        )
    if all_sibling_role:
        return (
            "【同批座位重叠已拒绝】"
            f"（本批 {total} 人）。冲突：{detail}。"
            "同一批内空白/大小写归一后相同的角色名视为同座位，不可并行双双入座；"
            "请改用不同角色名，或用 depends_on 标明串行交接 / 拆批 / "
            "replaces_run_id 接手后再派。"
        )
    if all_same_batch:
        return (
            "【同批座位/交付物重叠已拒绝】"
            f"（本批 {total} 人）。冲突：{detail}。"
            "请为并行队员使用不同角色名与文件路径，或用 depends_on / replaces_run_id "
            "标明交接后再派。"
        )
    return (
        "【队员追加已拒绝·座位/交付物重叠】"
        f"（已完成 {completed}/{total}）。冲突：{detail}。"
        "请等待波次推进，或显式 cancel_worker / replan / replaces_run_id 接手后再追加；"
        "已完成/已交接节点不能靠 cancel_worker 撤销，须 replaces_run_id 接手补派；"
        "勿为「闲着」重复派与计划或已占文件重合的队员；"
        "不要另起同名终稿抢写——应改自己的文件或等整合。"
    )


def declare_plan_artifacts(
    plan: RunPlan,
    ownership: WriteCoordinator,
    *,
    force: bool = False,
    only_run_ids: set[str] | frozenset[str] | None = None,
    ancestor_map: dict[str, frozenset[str]] | None = None,
    ancestor_handoff_at_declare: bool = False,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    birth_desk_id: str | None = None,
) -> list[tuple[str, str, str]]:
    """Reserve deliverable.artifacts for each node; apply replaces/continue transfers.

    By default (``ancestor_handoff_at_declare=False``) a downstream node that lists the
    same path as an ancestor **does not** steal the lock at dispatch — the ancestor
    keeps holding until write-time claim, completion handoff, or explicit transfer.
    Nested lead→child drives pass ``ancestor_handoff_at_declare=True``.

    When ``completed_run_ids`` is set, a hard conflict against a **completed** holder is
    treated as dispatch-time handoff (审校→修订跨波次)：新节点声明同路径即接手，无需
    用户点「移交写权」。``ended_owners`` on the ledger (nested terminal bypass) is
    treated the same. Still-running lock owners keep blocking.

    Returns list of ``(new_run_id, path, conflicting_owner)`` for hard conflicts
    when not force/transfer-eligible (caller should have rejected via overlaps first).
    ``path`` in the tuple is the bare ``rel_path``.
    """
    ancestors = ancestor_map if ancestor_map is not None else _ancestors_for_plan(plan)
    # Topological-ish: nodes with fewer deps first so ancestors register before intent.
    ordered = sorted(plan.nodes, key=lambda n: len(getattr(n, "depends_on", None) or ()))
    conflicts: list[tuple[str, str, str]] = []
    only = set(only_run_ids) if only_run_ids is not None else None
    done = {str(x).strip() for x in (completed_run_ids or ()) if str(x).strip()}
    dispatch_handoffs: list[tuple[str, str, str]] = []

    for node in ordered:
        rid = node.run_id
        if only is not None and rid not in only:
            continue
        replaces = (getattr(node, "replaces_run_id", None) or "").strip()
        continue_from = (getattr(node, "continue_from_run_id", None) or "").strip()
        if replaces:
            ownership.transfer_all_from(replaces, rid)
        if continue_from:
            # Same-author continuation: paths still held by the continued run move over.
            ownership.transfer_all_from(continue_from, rid)

        anc = set(ancestors.get(rid, frozenset()))
        if continue_from:
            anc.add(continue_from)
        if replaces:
            anc.add(replaces)
        anc_f = frozenset(anc)
        desk = node_ownership_desk(node, birth_desk_id=birth_desk_id)

        for path in node_artifact_paths(node):
            if force or replaces or continue_from:
                ownership.transfer(path, rid, desk_id=desk)
                continue
            owner = ownership.declare(
                path,
                rid,
                anc_f,
                force=False,
                allow_ancestor_handoff=ancestor_handoff_at_declare,
                desk_id=desk,
            )
            if owner is not None:
                ended = bool(
                    getattr(ownership, "is_ended", None) and ownership.is_ended(owner)
                )
                if owner in done or ended:
                    # 原主已完成/已结束、本协作会话内仍占位 → 新波次声明同 artifact 即接手。
                    ownership.transfer(path, rid, desk_id=desk)
                    dispatch_handoffs.append((path, owner, rid))
                    continue
                conflicts.append((rid, path, owner))
    if dispatch_handoffs:
        try:
            from agentcore.core.logging import get_logger

            get_logger(__name__).info(
                "file_ownership.dispatch_handoff",
                transfers=[
                    {"path": path, "from": old, "to": new}
                    for path, old, new in dispatch_handoffs
                ],
            )
        except Exception:  # noqa: BLE001 — never break dispatch
            pass
    return conflicts


def handoff_owned_paths_on_complete(
    plan: RunPlan,
    ownership: WriteCoordinator,
    completed_run_id: str,
    *,
    completed_run_ids: set[str] | frozenset[str] | None = None,
    ancestor_map: dict[str, frozenset[str]] | None = None,
    birth_desk_id: str | None = None,
) -> list[tuple[str, str]]:
    """Move completed worker's paths to the unique unfinished dependent listing them.

    Returns ``(bare_path, new_owner_run_id)`` pairs actually transferred. Ambiguous
    (0 or 2+ candidates) paths stay with the completed owner for write-time claim
    or explicit ``transfer_ownership``.
    """
    rid = (completed_run_id or "").strip()
    if not rid or not plan.nodes:
        return []
    owned_keys = ownership.owned_keys(rid)
    if not owned_keys:
        return []
    ancestors = ancestor_map if ancestor_map is not None else _ancestors_for_plan(plan)
    done = set(completed_run_ids or ())
    done.add(rid)
    moved: list[tuple[str, str]] = []
    for key in owned_keys:
        bare = ownership_display_path(key)
        candidates: list[str] = []
        direct: list[str] = []
        for node in plan.nodes:
            nid = (getattr(node, "run_id", None) or "").strip()
            if not nid or nid == rid or nid in done:
                continue
            node_keys = node_artifact_keys(node, birth_desk_id=birth_desk_id)
            if key not in node_keys:
                continue
            anc = ancestors.get(nid, frozenset())
            if rid not in anc:
                continue
            candidates.append(nid)
            deps = set(getattr(node, "depends_on", None) or ())
            if rid in deps:
                direct.append(nid)
        pool = direct if len(direct) == 1 else (candidates if len(candidates) == 1 else [])
        if len(pool) != 1:
            continue
        new_owner = pool[0]
        ownership.transfer(key, new_owner)
        moved.append((bare, new_owner))
    return moved


def declare_nested_drive_artifacts(
    tool: Any,
    plan: RunPlan,
    *,
    execution_id: str,
) -> list[tuple[str, str, str]]:
    """Path-level ownership handoff for nested (depth≥1) blocking drives.

    Nested sub-teams share the parent coordination ledger via
    :func:`~agentcore.workspace.write_claims.resolve_write_coordinator` but never
    enter :func:`try_start_coordination`, so they previously skipped dispatch-time
    declare. With ``parent_run_id`` in the ancestor map, declaring the child's
    artifacts transfers only those paths from the lead (not ``transfer_all_from``).
    Root depth-0 coordination already declares in ``host`` — skipped here.
    """
    from agentcore.runtime.delegate.force_scopes import GATE_SEAT_OVERLAP, force_allows
    from agentcore.workspace.write_claims import resolve_write_coordinator

    if int(getattr(tool, "_depth", 0) or 0) < 1:
        return []

    ownership = resolve_write_coordinator(execution_id=execution_id)
    force = force_allows(tool, GATE_SEAT_OVERLAP)
    completed: set[str] | frozenset[str] | None = None
    try:
        from agentcore.runtime.coordination.session import resolve_coordination_session

        sess = resolve_coordination_session(execution_id)
        if sess is not None:
            completed = sess.completed_run_ids
    except Exception:  # noqa: BLE001
        completed = None
    birth = getattr(tool, "_folder_id", None)
    conflicts = declare_plan_artifacts(
        plan,
        ownership,
        force=force,
        ancestor_map=_ancestors_for_plan(plan),
        ancestor_handoff_at_declare=True,
        completed_run_ids=completed,
        birth_desk_id=birth,
    )
    if conflicts:
        from agentcore.core.logging import get_logger

        get_logger(__name__).info(
            "coordination.nested_declare_conflicts",
            execution_id=execution_id,
            depth=int(getattr(tool, "_depth", 0) or 0),
            conflicts=[
                {"run_id": rid, "path": path, "owner": owner}
                for rid, path, owner in conflicts
            ],
        )
    return conflicts
