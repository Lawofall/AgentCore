"""build_run_plan — raw delegate args → RunPlan (第一阶段：内联角色版).

Single / parallel / DAG stop being distinct *modes* and become one RunPlan whose
shape falls out of the ``depends_on`` edges (no deps + 1 task = single; no deps +
N = parallel; any deps = a DAG). Pure and dict-based.

第一阶段：每个 task 自带「内联角色」（role / tools /
…），无独立 Agent 实体与 allow-list。``agent_id`` 铸成 == ``run_id``，``agent_name``
取 ``role``，仅供 ``run_*`` 事件与图展示。

Run-id minting: a no-deps batch uses ``{prefix}_{raw}`` when the task declares a
non-empty ``id`` (same shape as DAG) and ``{prefix}_{n}`` when undeclared, so a
re-delegate in the same turn never reuses a counter id; a DAG always namespaces
each declared id ``{prefix}_{raw}`` and rewrites every ``depends_on`` ref the
same way, so intra-DAG and cross-batch (via ``existing_plan``) edges survive.

→ 见设计: docs/03-AI核心/执行引擎架构设计.md §八（Run 模型）
"""

from __future__ import annotations

import re
import time
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.runtime.runs.constants import (
    DEFAULT_ON_FAILURE,
    MAX_DELEGATION_TASKS,
    MAX_TASK_ROUNDS,
    VALID_ON_FAILURE,
)
from agentcore.runtime.runs.plan import RunPlan, RunPlanError
from agentcore.runtime.runs.types import (
    Deliverable,
    RunKind,
    RunOrigin,
    RunPolicy,
    RunSpec,
    normalize_deliverable_form,
)

# Debate/review opposition markers (前端UX设计.md §四): a display-only side tag the
# frontend pairs into a side-by-side comparison; anything else is dropped (lenient)
# so a stray value never leaks onto the graph.
_VALID_STANCES = frozenset({"pro", "con"})
_VALID_OUTPUT_FORMATS = frozenset({"text", "json"})
# DAG 节点可显式声明 timeout_ms；缺省不填 → ``apply_worker_budgets`` 填统一 backstop。
# Per-sibling excerpt caps in a fan-out awareness summary: a scope line (任务),
# kept tight so a wide fan-out's awareness block stays scannable and can't blow
# up a worker's context.
_SIBLING_TASK_CHARS = 150
# Consumer-oriented phrasing only — bare 「上游」「前置」 false-fire on seed tasks
# that *are* the upstream ("作为上游产出…"). Keep advisory; never a hard reject.
_UPSTREAM_HINTS = re.compile(
    r"(?:"
    r"基于.{0,40}(?:产出|结果|输出)|"
    r"见上游|"
    r"依赖.{0,20}(?:结果|产出)|"
    r"(?:请)?读取.{0,20}(?:产出|结果)|"
    r"(?:参考|根据|使用|等待|查看|拿到?|先看).{0,20}"
    r"(?:上游|前置).{0,12}(?:产出|结果|输出|产物)|"
    r"前置(?:结果|产出|产物)|"
    r"(?:from|read|use).{0,20}upstream|"
    r"based on|depends on"
    r")",
    re.IGNORECASE,
)
# Skip a match when the span is immediately preceded by a short negation.
_UPSTREAM_NEGATION_PREFIX = re.compile(r"(?:不|勿|无需|不必|不用|别)\s*$")
# Minted run_id = ``{del|add}_<uuid>_<raw>`` (see delegate prefix / build_added_nodes).
# Strip the casting prefix so cross-batch depends_on can use the CEO's tasks[].id literal.
_MINT_PREFIX_RE = re.compile(
    r"^(?:del|add)_"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"_(.+)$",
    re.IGNORECASE,
)

logger = get_logger(__name__)


def _task_suspects_missing_dep(task: str) -> bool:
    """True when task text reads like it *consumes* upstream work but deps may be missing."""
    for m in _UPSTREAM_HINTS.finditer(task):
        prefix = task[max(0, m.start() - 4) : m.start()]
        if _UPSTREAM_NEGATION_PREFIX.search(prefix):
            continue
        return True
    return False


def _format_available_dep_nodes(
    nodes: list[tuple[str, str]],
) -> str:
    """Human-readable catalog of (run_id_or_raw, role) for depends_on errors."""
    if not nodes:
        return "（当前无可用节点）"
    parts = [
        f"{rid}（{role}）" if role and role != rid else rid for rid, role in nodes
    ]
    return "、".join(parts)


def recoverable_raw_id(run_id: str) -> str | None:
    """Return the short raw suffix of a minted ``del_*`` / ``add_*`` run_id, else None."""
    m = _MINT_PREFIX_RE.match(run_id or "")
    return m.group(1) if m else None


def _host_short_raw_index(
    nodes: list[RunSpec],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Index recoverable short raws for host / existing_plan nodes.

    Returns ``(aliases, ambiguous)`` where ``aliases`` maps short raw → run_id for
    unambiguous cases, and ``ambiguous`` maps short raw → candidate run_ids when two
    or more host nodes strip to the same raw (same shape as role-name ambiguity).
    """
    aliases: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for n in nodes:
        short = recoverable_raw_id(n.run_id)
        if not short:
            continue
        if short in ambiguous:
            if n.run_id not in ambiguous[short]:
                ambiguous[short].append(n.run_id)
            continue
        prior = aliases.get(short)
        if prior is None:
            aliases[short] = n.run_id
            continue
        if prior == n.run_id:
            continue
        # Same short raw → different run_ids: demote to ambiguity (no silent setdefault).
        del aliases[short]
        ambiguous[short] = [prior, n.run_id]
    return aliases, ambiguous


def _resolve_dep_ref(
    raw: str,
    *,
    by_raw_id: dict[str, str],
    by_role: dict[str, list[str]],
    available_label: str,
    ambiguous_raw_ids: dict[str, list[str]] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve one depends_on token to a namespaced run_id.

    Order: exact raw id → unambiguous role / agent_name alias.
    Returns ``(run_id, None)`` on success or ``(None, error_message)`` on failure.
    """
    token = (raw or "").strip()
    if not token:
        return None, None
    if token in by_raw_id:
        return by_raw_id[token], None
    # Already-namespaced full id that happens to equal a minted value.
    minted_values = set(by_raw_id.values())
    if token in minted_values:
        return token, None
    next_step = (
        "下一步：填无歧义角色名，或本批 / 当前活跃图已有节点的 id 字面值。"
    )
    ambig = (ambiguous_raw_ids or {}).get(token) or []
    if len(ambig) > 1:
        return None, (
            f"depends_on `{token}` 短 id 有歧义（候选 run_id：{'、'.join(ambig)}）。"
            f"请改用完整 run_id。可用节点：{available_label}。{next_step}"
        )
    role_hits = by_role.get(token) or []
    if len(role_hits) == 1:
        return role_hits[0], None
    if len(role_hits) > 1:
        return None, (
            f"depends_on `{token}` 角色名有歧义（候选 run_id：{'、'.join(role_hits)}）。"
            f"请改用 id 字段字面值。可用节点：{available_label}。{next_step}"
        )
    return None, (
        f"depends_on `{token}` 无法解析为已知节点。"
        f"可用节点：{available_label}。{next_step}"
    )


def _nonempty_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _is_complete_task(item: Any) -> bool:
    """A real task node: object with both role and task (the delegate schema required pair)."""
    if not isinstance(item, dict):
        return False
    return bool(_nonempty_str(item.get("role")) and _nonempty_str(item.get("task")))


def _is_pure_stub(item: dict[str, Any]) -> bool:
    """Metadata-only row (id / depends_on / …) with neither role nor task."""
    return not _nonempty_str(item.get("role")) and not _nonempty_str(item.get("task"))


def _merge_task_fragments(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge two adjacent fragments the model split across array slots (id 行 + task 行)."""
    merged = dict(left)
    for key, value in right.items():
        if value is None or value == "":
            continue
        # Keep left's id / role / task when already set; fill only the missing halves.
        if key in ("id", "role", "task") and _nonempty_str(merged.get(key)):
            continue
        merged[key] = value
    return merged


def coalesce_split_tasks(tasks_raw: list[Any]) -> list[dict[str, Any]]:
    """Collapse model-emitted split rows into real task nodes before counting / planning.

    Some models put DAG ``id`` (or role-only / task-only halves) in a separate ``tasks[]``
    element from the ``role``+``task`` payload. That inflates ``len(tasks)`` past the
    single-call cap even when the intended node count is within budget (trace
    2f52c042: 10 intended → counted as 15). Merge only:
    - adjacent incompletes that together supply role+task; or
    - a pure id/metadata stub immediately before a complete task.
    A role-only hole between two complete tasks is NOT folded away — validation must
    still reject it. Non-dict slots are dropped (they cannot become nodes).
    """
    out: list[dict[str, Any]] = []
    i = 0
    n = len(tasks_raw)
    while i < n:
        cur = tasks_raw[i]
        if not isinstance(cur, dict):
            i += 1
            continue
        if _is_complete_task(cur):
            out.append(dict(cur))
            i += 1
            continue
        nxt = tasks_raw[i + 1] if i + 1 < n else None
        # role-only + task-only (or id+role + task) → one node.
        if isinstance(nxt, dict) and not _is_complete_task(nxt):
            merged = _merge_task_fragments(cur, nxt)
            if _is_complete_task(merged):
                out.append(merged)
                i += 2
                continue
        # Pure id stub right before a complete task → fold id/depends_on in.
        if isinstance(nxt, dict) and _is_complete_task(nxt) and _is_pure_stub(cur):
            out.append(_merge_task_fragments(cur, nxt))
            i += 2
            continue
        # Keep the incomplete row so later validation can report a precise field error.
        out.append(dict(cur))
        i += 1
    return out


def build_run_plan(
    tasks_raw: list[dict[str, Any]],
    *,
    valid_tools: set[str] | None = None,
    id_prefix: str = "",
    counter_start: int = 0,
    max_tasks: int = MAX_DELEGATION_TASKS,
    parent_run_id: str | None = None,
    depth: int = 1,
    complexity_hint: str = "standard",
    existing_plan: RunPlan | None = None,
    default_target_folder_id: str | None = None,
) -> tuple[RunPlan, list[str]]:
    """Build a RunPlan from raw delegate-tool task args.

    ``valid_tools`` is retained for call-site compat; 真纯丙下不再用它与
    ``tasks[].tools`` 做白名单交集（声明的 tools 一律忽略）。Returns the plan
    plus a list of validation errors; a non-empty error list means the batch is
    rejected and the plan must not be run (reject-on-error for both flat and DAG
    batches).

    ``parent_run_id`` / ``depth`` stamp every node with its place in the turn's Run
    tree (阶段2 嵌套子任务): the CEO's direct workers are ``depth=1`` parented to the
    captain root; a worker that re-delegates passes its own run id + depth so its
    sub-workers come out one level deeper. The executor reads ``depth`` to enforce
    the nesting cap.

    ``complexity_hint`` is forwarded to retrieval-budget apply for API compat
    (defaults are unified; hint no longer tiers). Worker token/timeout backstop
    is applied uniformly afterward (:mod:`agentcore.runtime.runs.worker_budget`).

    ``existing_plan`` (append / merge-into-host only): expand ``depends_on`` resolve
    known-set to host nodes ∪ this batch — same rule as :func:`build_added_nodes`.
    Returned plan still contains **only** the new batch nodes; pure new builds leave
    this ``None`` (behavior unchanged).

    ``default_target_folder_id``: nested sub-team inheritance (§4.2b·3) — when a
    task omits ``target_folder_id``, stamp the parent worker's target desk.
    """
    if not tasks_raw:
        return RunPlan(), ["'tasks' array is required and cannot be empty"]
    # Count / plan against real task nodes (coalesce id/task 拆行), not raw array length.
    tasks_raw = coalesce_split_tasks(list(tasks_raw))
    if not tasks_raw:
        return RunPlan(), ["'tasks' array is required and cannot be empty"]
    for item in tasks_raw:
        deps = item.get("depends_on")
        if deps is not None:
            item["depends_on"] = [d for d in deps if d and isinstance(d, str) and d.strip()]
    prefix = id_prefix or f"del_{int(time.time() * 1000)}"
    if any(item.get("depends_on") for item in tasks_raw):
        plan, errors = _dag_plan(
            tasks_raw,
            valid_tools,
            prefix,
            max_tasks,
            parent_run_id,
            depth,
            existing_plan=existing_plan,
            default_target_folder_id=default_target_folder_id,
        )
    else:
        plan, errors = _flat_plan(
            tasks_raw,
            valid_tools,
            prefix,
            counter_start,
            max_tasks,
            parent_run_id,
            depth,
            default_target_folder_id=default_target_folder_id,
        )
    # Fan-out awareness is computed ONCE here, after the shape-specific build, so a
    # flat batch and a DAG share one definition of「sibling」= nodes that fanned out
    # from the same point (same depends_on). The DAG case is the fix: its parallel
    # nodes (e.g. the 调研 workers a「research → writer」fan-out spawns) used to get
    # nothing and ran blind/overlapping. Skipped for a rejected plan (nodes may be
    # partial).
    if not errors:
        _apply_sibling_summaries(plan)
        from agentcore.runtime.runs.retrieval_budget import apply_retrieval_budgets
        from agentcore.runtime.runs.worker_budget import (
            apply_directed_search_tools,
            apply_verify_policies,
            apply_worker_budgets,
        )

        apply_retrieval_budgets(plan, valid_tools=valid_tools, complexity_hint=complexity_hint)
        apply_directed_search_tools(plan, valid_tools=valid_tools)
        apply_verify_policies(plan)
        apply_worker_budgets(plan)
        from agentcore.runtime.runs.artifact_dir import apply_artifact_dir_to_plan

        apply_artifact_dir_to_plan(plan)
    return plan, errors


def build_added_nodes(
    adds: list[dict[str, Any]],
    plan: RunPlan,
    *,
    valid_tools: set[str] | None = None,
    parent_run_id: str | None = None,
    depth: int = 1,
    max_tasks: int = MAX_DELEGATION_TASKS,
    default_target_folder_id: str | None = None,
) -> tuple[list[RunSpec], list[str]]:
    """Build the RunSpecs for a ``replan(add=[…])`` batch the CEO appends to a paused
    ``plan`` at a wave boundary (受监督的波循环 §7.1 续跑入口).

    Returns ``(specs, errors)``. A non-empty ``errors`` means the whole replan is
    rejected (all-or-nothing) and the caller must NOT mutate the plan; this function is
    pure (it never touches ``plan``) so rejection leaves no trace. On success the caller
    appends each spec via :meth:`RunPlan.add`.

    id 生成 + 依赖接线 (the bit that made ``add`` its own phase):
    - each added node gets a fresh collision-free id ``{add_<uuid>}_{raw}`` (a brand-new
      prefix per batch, so re-adds across multiple boundary yields never reuse an id);
    - each ``depends_on`` ref resolves against BOTH the existing plan nodes (full run_id,
      recoverable short raw after stripping ``del_*`` / ``add_*`` mint prefixes, or
      unambiguous role) AND the other raw ids in THIS batch (intra-edge), so the CEO can
      append a mini-DAG that hangs off the live graph;
    - role/task are required (like a DAG node); an unknown ``depends_on`` ref, a dup id,
      an over-cap batch, or a cycle introduced among the new nodes is a rejected error.
    """
    if not adds:
        return [], []
    adds = coalesce_split_tasks(list(adds))
    if not adds:
        return [], []
    if len(adds) > max_tasks:
        return [], [f"add 一次最多追加 {max_tasks} 个节点（收到 {len(adds)}）"]

    prefix = f"add_{new_id()}"
    existing_ids = {n.run_id for n in plan.nodes}
    # First pass: assign each item a raw id + mint its namespaced run_id, catching dup
    # raw ids up front (two added nodes can't share an id, and a mint must not collide
    # with an existing node — impossible given the fresh prefix, but checked anyway).
    raw_to_minted: dict[str, str] = {}
    minted_ids: list[str] = []
    errors: list[str] = []
    for i, item in enumerate(adds):
        if not isinstance(item, dict):
            errors.append(f"add[{i}] 必须是对象")
            minted_ids.append("")
            continue
        raw = (str(item.get("id")).strip() if item.get("id") is not None else "") or f"n{i}"
        if raw in raw_to_minted:
            errors.append(f"add[{i}]: 重复的 id `{raw}`")
            minted_ids.append("")
            continue
        minted = f"{prefix}_{raw}"
        if minted in existing_ids:
            errors.append(f"add[{i}]: 生成的 run_id `{minted}` 与现有节点冲突")
            minted_ids.append("")
            continue
        raw_to_minted[raw] = minted
        minted_ids.append(minted)
    if errors:
        return [], errors

    # Second pass: validate fields + resolve each depends_on ref (existing node id /
    # recoverable short raw OR a raw id / unambiguous role in THIS batch). Build the
    # specs reusing the same _inline_spec / _dag_policy the up-front builder uses.
    by_raw_id = dict(raw_to_minted)
    by_role: dict[str, list[str]] = {}
    for n in plan.nodes:
        by_raw_id.setdefault(n.run_id, n.run_id)
        role_key = (n.role or n.agent_name or "").strip()
        if role_key:
            by_role.setdefault(role_key, []).append(n.run_id)
    # Host short raws (strip del_/add_ mint prefix); this-batch raws already in
    # by_raw_id win via setdefault. Short-id collisions among host nodes → ambiguous.
    host_aliases, host_ambiguous = _host_short_raw_index(plan.nodes)
    for short, rid in host_aliases.items():
        by_raw_id.setdefault(short, rid)
    ambiguous_raw_ids = {
        k: v for k, v in host_ambiguous.items() if k not in by_raw_id
    }
    for j, add_item in enumerate(adds):
        if not isinstance(add_item, dict):
            continue
        minted_j = minted_ids[j]
        if not minted_j:
            continue
        add_role = str(add_item.get("role") or "").strip()
        if add_role:
            by_role.setdefault(add_role, []).append(minted_j)
    available_nodes = [
        (n.run_id, n.role or n.agent_name or "") for n in plan.nodes
    ] + [
        (minted_ids[j], str(adds[j].get("role") or "").strip())
        for j in range(len(adds))
        if isinstance(adds[j], dict) and minted_ids[j]
    ]
    available_label = _format_available_dep_nodes(available_nodes)

    specs: list[RunSpec] = []
    for i, item in enumerate(adds):
        minted = minted_ids[i]
        role = item.get("role")
        task = item.get("task")
        if not (isinstance(role, str) and role.strip()):
            errors.append(f"add[{i}]: 缺少 role")
            continue
        if not (isinstance(task, str) and task.strip()):
            errors.append(f"add[{i}]: 缺少 task")
            continue
        prose_err = prose_form_conflict_error(item)
        if prose_err:
            errors.append(f"add[{i}]: {prose_err}")
            continue
        resolved_deps: list[str] = []
        dep_ok = True
        for dep in item.get("depends_on") or []:
            dep_id = str(dep).strip()
            resolved, err = _resolve_dep_ref(
                dep_id,
                by_raw_id=by_raw_id,
                by_role=by_role,
                available_label=available_label,
                ambiguous_raw_ids=ambiguous_raw_ids,
            )
            if err:
                errors.append(f"add[{i}]: {err}")
                dep_ok = False
                continue
            if resolved:
                resolved_deps.append(resolved)
        if not dep_ok:
            continue
        specs.append(
            _inline_spec(
                {**item, "role": role.strip(), "task": task.strip()},
                run_id=minted,
                depends_on=resolved_deps,
                policy=_dag_policy(item),
                valid_tools=valid_tools,
                parent_run_id=parent_run_id,
                depth=depth,
                default_target_folder_id=default_target_folder_id,
            )
        )
    if errors:
        return [], errors

    # Topology pre-check on the combined graph: existing nodes never gain edges and the
    # existing plan is already acyclic, so the only new cycle risk is among the added
    # nodes — a throwaway combined RunPlan.waves() surfaces it without mutating `plan`.
    try:
        RunPlan(nodes=[*plan.nodes, *specs], origin=plan.origin).waves()
    except RunPlanError as e:
        return [], [f"add 拓扑无效：{e}"]
    from agentcore.runtime.runs.retrieval_budget import apply_retrieval_budgets_to_specs
    from agentcore.runtime.runs.worker_budget import (
        apply_directed_search_tools_to_specs,
        apply_verify_policies_to_specs,
        apply_worker_budgets_to_specs,
    )

    apply_retrieval_budgets_to_specs(specs, valid_tools=valid_tools, complexity_hint="standard")
    apply_directed_search_tools_to_specs(specs, valid_tools=valid_tools)
    apply_verify_policies_to_specs(specs)
    # replan add：token/超时走统一 backstop；检索额度走统一默认 + 硬例外。
    apply_worker_budgets_to_specs(specs)
    from agentcore.runtime.runs.artifact_dir import apply_artifact_dir_to_specs

    apply_artifact_dir_to_specs(specs)
    return specs, []


def _flat_plan(
    tasks_raw: list[dict[str, Any]],
    valid_tools: set[str] | None,
    prefix: str,
    counter_start: int,
    max_tasks: int,
    parent_run_id: str | None,
    depth: int,
    *,
    default_target_folder_id: str | None = None,
) -> tuple[RunPlan, list[str]]:
    """Single / parallel batch (no deps). Declared non-empty ``id`` mints
    ``{prefix}_{raw}`` (same shape as DAG); undeclared uses ``{prefix}_{counter}``.
    Duplicate declared ids reject the whole batch. Invalid items (missing role or
    task) or an over-cap batch also reject the whole plan."""
    if len(tasks_raw) > max_tasks:
        # 拒绝整批时把「怎么分」算好回给 CEO：无依赖批纯按数量装箱，指明本次传前 max_tasks
        # 个、其余下次 delegate 再传，让重来那一轮照做即可、不必重想编排（见 trace 4d715ea0：
        # CEO 首轮派 18 撞上限后要整轮重规划才拆两批）。
        # B1：闩锁超席 → 同回合收口须 PARTIAL + 缺口，禁『仍在进行』空悬。
        from agentcore.runtime.closing_posture import note_over_seat_reject

        note_over_seat_reject(task_count=len(tasks_raw), max_tasks=max_tasks)
        overflow = len(tasks_raw) - max_tasks
        batches = (len(tasks_raw) + max_tasks - 1) // max_tasks
        return RunPlan(), [
            f"任务数 {len(tasks_raw)} 超过单次委派上限 {max_tasks}。这些任务互相独立，"
            f"分 {batches} 次 delegate 调用完成：本次只传前 {max_tasks} 个，"
            f"其余 {overflow} 个在下一次 delegate 调用里传。"
        ]
    errors: list[str] = []
    seen_declared: set[str] = set()
    for i, item in enumerate(tasks_raw):
        role = item.get("role")
        task = item.get("task")
        if not (isinstance(role, str) and role.strip()) or not (
            isinstance(task, str) and task.strip()
        ):
            errors.append(f"tasks[{i}]: 'role' 和 'task' 字段必填")
            continue
        prose_err = prose_form_conflict_error(item)
        if prose_err:
            errors.append(f"tasks[{i}]: {prose_err}")
        raw_id = str(item.get("id", "")).strip()
        if raw_id:
            if raw_id in seen_declared:
                errors.append(f"tasks[{i}]: 重复的 id '{raw_id}'")
            seen_declared.add(raw_id)
    if errors:
        return RunPlan(), errors
    plan = RunPlan()
    counter = counter_start
    used_run_ids: set[str] = set()
    for i, item in enumerate(tasks_raw):
        raw_id = str(item.get("id", "")).strip()
        if raw_id:
            run_id = f"{prefix}_{raw_id}"
        else:
            counter += 1
            run_id = f"{prefix}_{counter}"
        if run_id in used_run_ids:
            errors.append(f"tasks[{i}]: 生成的 run_id '{run_id}' 与本批其它节点冲突")
            continue
        used_run_ids.add(run_id)
        plan.add(
            _inline_spec(
                item,
                run_id=run_id,
                policy=RunPolicy(
                    result_handling=item.get("result_handling") or "pass_through",
                    timeout_s=_explicit_timeout_s(item),
                ),
                valid_tools=valid_tools,
                parent_run_id=parent_run_id,
                depth=depth,
                default_target_folder_id=default_target_folder_id,
            )
        )
    if errors:
        return RunPlan(), errors
    return plan, []


def _dag_plan(
    tasks_raw: list[dict[str, Any]],
    valid_tools: set[str] | None,
    prefix: str,
    max_tasks: int,
    parent_run_id: str | None,
    depth: int,
    *,
    existing_plan: RunPlan | None = None,
    default_target_folder_id: str | None = None,
) -> tuple[RunPlan, list[str]]:
    """DAG batch (has deps). Per-run validation collects errors; topology
    (cycle / unknown edge) is checked via ``RunPlan.waves``.

    When ``existing_plan`` is set (append into a host graph), ``depends_on`` resolves
    against host nodes ∪ this batch — same known-set as :func:`build_added_nodes`.
    The returned plan still holds only this batch's nodes.
    """
    if len(tasks_raw) > max_tasks:
        # 有依赖批不能按数量硬切（会拆断依赖链），给依赖感知的分批指引：独立子团队拆到不同次
        # 调用、有依赖的留同一次或用 depends_on 跨批衔接，让 CEO 重来那轮一次到位。
        from agentcore.runtime.closing_posture import note_over_seat_reject

        note_over_seat_reject(task_count=len(tasks_raw), max_tasks=max_tasks)
        return RunPlan(), [
            f"任务数 {len(tasks_raw)} 超过单次委派上限 {max_tasks}。分多次 delegate 调用："
            f"把互相独立的子团队（彼此无 depends_on）拆到不同次调用、每次 ≤{max_tasks}；"
            f"有依赖的任务留在同一次调用内，或让后一次调用用 depends_on 衔接前一批已产出的节点。"
            f"本次只传 ≤{max_tasks} 个。"
        ]
    errors: list[str] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(tasks_raw):
        raw_id = str(item.get("id", "")).strip() or f"n{i}"
        if raw_id in seen_ids:
            errors.append(f"tasks[{i}]: 重复的 id '{raw_id}'")
        seen_ids.add(raw_id)
    if errors:
        return RunPlan(), errors

    plan = RunPlan(origin=RunOrigin.TEMPLATE)
    errors = []

    def _nsid(raw: str) -> str:
        return f"{prefix}_{raw}"

    # Index raw id → minted run_id and role → [minted] for tolerant depends_on resolve.
    raw_ids: list[str] = []
    roles_for_index: list[str] = []
    for i, item in enumerate(tasks_raw):
        raw_ids.append(str(item.get("id", "")).strip() or f"n{i}")
        roles_for_index.append(str(item.get("role") or "").strip())
    by_raw_id = {raw: _nsid(raw) for raw in raw_ids}
    by_role: dict[str, list[str]] = {}
    for raw, role in zip(raw_ids, roles_for_index, strict=True):
        if not role:
            continue
        by_role.setdefault(role, []).append(_nsid(raw))
    # Append/merge: host nodes join the known-set (all nodes, not only completed) —
    # mirrors build_added_nodes so cross-batch depends_on resolves the same way
    # (full run_id + recoverable short raw from del_/add_ mint prefix).
    available_nodes: list[tuple[str, str]] = list(
        zip(raw_ids, roles_for_index, strict=True)
    )
    ambiguous_raw_ids: dict[str, list[str]] = {}
    if existing_plan is not None:
        for n in existing_plan.nodes:
            by_raw_id.setdefault(n.run_id, n.run_id)
            role_key = (n.role or n.agent_name or "").strip()
            if role_key:
                by_role.setdefault(role_key, []).append(n.run_id)
        host_aliases, host_ambiguous = _host_short_raw_index(existing_plan.nodes)
        for short, rid in host_aliases.items():
            by_raw_id.setdefault(short, rid)
        ambiguous_raw_ids = {
            k: v for k, v in host_ambiguous.items() if k not in by_raw_id
        }
        available_nodes = [
            (n.run_id, n.role or n.agent_name or "") for n in existing_plan.nodes
        ] + available_nodes
    available_label = _format_available_dep_nodes(available_nodes)

    for i, item in enumerate(tasks_raw):
        raw_id = str(item.get("id", "")).strip() or f"n{i}"
        role = item.get("role", "")
        task = item.get("task", "")
        if not role:
            errors.append(f"Run '{raw_id}': missing role")
            continue
        if not task:
            errors.append(f"Run '{raw_id}': missing task")
            continue
        prose_err = prose_form_conflict_error(item)
        if prose_err:
            errors.append(f"tasks[{i}]（{role or raw_id}）: {prose_err}")
            continue
        resolved_deps: list[str] = []
        dep_ok = True
        for dep in item.get("depends_on") or []:
            dep_token = str(dep).strip()
            resolved, err = _resolve_dep_ref(
                dep_token,
                by_raw_id=by_raw_id,
                by_role=by_role,
                available_label=available_label,
                ambiguous_raw_ids=ambiguous_raw_ids,
            )
            if err:
                errors.append(f"tasks[{i}]（{role or raw_id}）: {err}")
                dep_ok = False
                continue
            if resolved:
                resolved_deps.append(resolved)
        if not dep_ok:
            continue
        plan.add(
            _inline_spec(
                item,
                run_id=_nsid(raw_id),
                depends_on=resolved_deps,
                policy=_dag_policy(item),
                valid_tools=valid_tools,
                parent_run_id=parent_run_id,
                depth=depth,
                default_target_folder_id=default_target_folder_id,
            )
        )

    if errors:
        return plan, errors
    try:
        # Cross-batch edges point at host nodes absent from `plan`; check the combined
        # graph (same as build_added_nodes) without mutating existing_plan.
        if existing_plan is not None:
            RunPlan(
                nodes=[*existing_plan.nodes, *plan.nodes],
                origin=existing_plan.origin,
            ).waves()
        else:
            plan.waves()
    except RunPlanError as e:
        # Defense in depth: waves() unknown-edge should be unreachable after resolve,
        # but keep the catalog so any residual message stays actionable.
        return plan, [f"{e}。可用节点：{available_label}"]
    for node in plan.nodes:
        if not node.depends_on and node.task and _task_suspects_missing_dep(node.task):
            logger.warning(
                "builder.suspect_missing_dep",
                run_id=node.run_id,
                role=node.role,
                hint="task 提及上游产出但 depends_on 为空",
            )
            # 搭车 CEO 注入通道（见 coordination/inject.py）：不再只写后台日志，让 CEO 可见。
            plan.advisories.append(
                f"「{node.role or node.run_id}」的任务提及上游产出，但 depends_on 为空"
                f"（run_id={node.run_id}）。若确需先拿上游结果，补 depends_on 或分批 delegate；"
                "本就独立可忽略。"
            )
    return plan, []


def _inline_spec(
    item: dict[str, Any],
    *,
    run_id: str,
    depends_on: list[str] | None = None,
    policy: RunPolicy,
    valid_tools: set[str] | None = None,
    parent_run_id: str | None = None,
    depth: int = 1,
    default_target_folder_id: str | None = None,
) -> RunSpec:
    """Assemble one RunSpec from a task item's inline-role fields (阶段1)."""
    from agentcore.runtime.delegate.target_desktop import effective_target_folder_id

    role = item["role"]
    thinking_raw = item.get("thinking")
    model_raw = item.get("model")
    stance_raw = item.get("stance")
    group_raw = item.get("group")
    round_raw = item.get("round")
    return RunSpec(
        run_id=run_id,
        agent_id=run_id,
        agent_name=role,
        kind=RunKind.AGENT,
        task=item["task"],
        role=role,
        system_prompt_supplement=item.get("system_prompt_supplement") or None,
        research_then_draft=bool(item.get("research_then_draft")),
        evidence_ledger_check=bool(item.get("evidence_ledger_check")),
        side_key=(
            item["side_key"].strip()
            if isinstance(item.get("side_key"), str)
            else ""
        ),
        draft_brief=(
            item["draft_brief"].strip()
            if isinstance(item.get("draft_brief"), str)
            else ""
        ),
        draft_system=(
            item["draft_system"].strip()
            if isinstance(item.get("draft_system"), str)
            else ""
        ),
        tools=_tools(item.get("tools"), valid_tools),
        # Explicit model override：路由键字符串（真·多模型辩手 / per-worker 目录身份编码后）。
        # 普通 worker 省略 → 空 = 跟组合 Worker 槽；执行器覆写 profile 并经路由器分发。
        model=model_raw.strip() if isinstance(model_raw, str) else "",
        thinking=thinking_raw if isinstance(thinking_raw, bool) else None,
        deliverable=_parse_deliverable(item),
        # 辩论/审查 呈现标记（display-only）：宽松解析，非法 stance 丢弃、group 取整后
        # 字符串、round 仅收正整数（bool 不算，否则 0）。执行器从不读它们，仅透传给
        # run_plan 供前端识别辩论 → 并排渲染 / 按轮次分层。
        stance=stance_raw if stance_raw in _VALID_STANCES else "",
        group=group_raw.strip() if isinstance(group_raw, str) else "",
        round=(
            round_raw
            if isinstance(round_raw, int) and not isinstance(round_raw, bool) and round_raw > 0
            else 0
        ),
        depends_on=depends_on or [],
        # 结构化挂起 2a：计划期挂起标记，宽松读取（非真值即 False），WaveScheduler
        # 在该节点完成后、其下游运行前挂起请用户 plan_review。已由 delegate schema 暴露为
        # 可设 task 字段、并由 on_boundary 消费；未接 on_boundary 的调度（自治/测试）下仍 inert。
        checkpoint_after=bool(item.get("checkpoint_after")),
        # 晚绑定标记（受监督的波循环）：宽松读取（非真值即 False），同 checkpoint_after 已激活。
        # 由 schema 暴露、replan 在波边界定稿消费；无 on_boundary hook 时 inert。
        bind_after_deps=bool(item.get("bind_after_deps")),
        parent_run_id=parent_run_id,
        depth=depth,
        replaces_run_id=_parse_replaces_run_id(item.get("replaces_run_id")),
        continue_from_run_id=_parse_continue_from_run_id(item.get("continue_from_run_id")),
        force_continue=bool(item.get("force_continue")),
        ceiling_priority=bool(item.get("ceiling_priority")),
        context_inject_files=_str_list(item.get("context_inject_files")),
        require_upstream=bool(item.get("require_upstream")),
        # 检索额度由 apply_retrieval_budgets 填统一默认；CEO/task 不可配置。
        # 辩手有约定文档等内部窄例外在 build 后补写 RunSpec.retrieval_budget。
        retrieval_budget=None,
        search_policy=_parse_search_policy(item.get("search_policy")),
        verify_policy=_parse_verify_policy(item.get("verify_policy")),
        max_rounds=_parse_max_rounds(item.get("max_rounds")),
        policy=policy,
        target_folder_id=effective_target_folder_id(
            item.get("target_folder_id"),
            default=default_target_folder_id,
        ),
    )


def _parse_max_rounds(raw: Any) -> int | None:
    """Optional per-task ReAct round cap (repair / light posture).

    Values ``<1`` (and non-ints) drop to ``None`` (profile default). Values
    above :data:`MAX_TASK_ROUNDS` are clamped — the CEO cannot request an
    unbounded per-segment budget.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw < 1:
        return None
    return min(raw, MAX_TASK_ROUNDS)


def _parse_replaces_run_id(raw: Any) -> str | None:
    """Normalise optional ``replaces_run_id`` (回落换人) → stripped id or None."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _parse_continue_from_run_id(raw: Any) -> str | None:
    """Normalise optional ``continue_from_run_id`` (同人续派) → stripped id or None."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _parse_search_policy(raw: Any) -> str:
    """Normalise optional ``search_policy``; recognised values only."""
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip()
    if cleaned in ("debate_evidence", "academic_literature"):
        return cleaned
    return ""


def _parse_verify_policy(raw: Any) -> str:
    """Normalise optional ``verify_policy``; ``inner`` / ``outer`` recognised."""
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip().lower()
    if cleaned in ("inner", "outer"):
        return cleaned
    return ""


def _tools(declared: Any, valid_tools: set[str] | None) -> list[str] | None:
    """真纯丙：忽略 ``tasks[].tools`` 收窄；始终 ``None``（全开相关工具面）。

    入参仍可带 ``tools``（旧客户端 / 剧本遗留 / 辩论装配），但不再写入 RunSpec
    白名单，也不再与 ``valid_tools`` 做交集。执行层以 ``allowed_tools=None`` 为准。
    """
    del declared, valid_tools
    return None


def _apply_sibling_summaries(plan: RunPlan) -> None:
    """Populate each node's ``sibling_summary`` with its fan-out siblings — the
    *other* nodes that fanned out from the SAME point (share the exact same
    ``depends_on`` set), so they run in parallel toward the same juncture.

    This is the precise「parallel sibling」notion: a「research → writer」fan-out's
    researchers share their dependency set (both have no deps, or both wait on the
    same upstream) and so see each other — the gap this fixes (a DAG used to give its
    parallel nodes nothing, so they ran blind/overlapping). It is deliberately
    NARROWER than「same wave」: two *independent* chains can land in one topological
    wave by coincidence (``s2`` deps ``[s1]`` and ``u2`` deps ``[u1]``) yet are not
    siblings — coupling those would bloat a worker's context with unrelated
    concurrent work and blur branch independence (cf. the checkpoint-steer isolation
    guarantee). A flat parallel batch is the degenerate case (every node shares the
    empty dep set → all siblings, unchanged); a node with no same-fan-out peer (a
    pipeline link, a lone writer) stays blank. A node never lists its own
    upstream/downstream — those arrive separately via ``depends_on``.

    Mutates specs in place; reads only ``depends_on`` so it is safe on any plan."""
    groups: dict[frozenset[str], list[RunSpec]] = {}
    for spec in plan.nodes:
        groups.setdefault(frozenset(spec.depends_on), []).append(spec)
    for group in groups.values():
        if len(group) < 2:
            continue
        for spec in group:
            spec.sibling_summary = _sibling_summary(group, spec)


def _sibling_summary(group: list[RunSpec], me: RunSpec) -> str:
    """Fan-out awareness body for ``me``: one bullet per *other* node in its
    fan-out group, carrying enough for a peer to draw its own boundary —

      ``- {role}：{task}``

    Scope is the sibling's ``task`` instruction (always present) so a peer is never
    blank. Excerpts are capped (:func:`_excerpt`). Assumes ``len(group) >= 2``
    (caller skips a lone node)."""
    lines: list[str] = []
    for other in group:
        if other.run_id == me.run_id:
            continue
        scope = _excerpt(other.task, _SIBLING_TASK_CHARS)
        lines.append(f"- {other.role}：{scope}")
    return "\n".join(lines)


def _excerpt(text: str, limit: int) -> str:
    """Head excerpt of ``text`` capped at ``limit`` chars (ellipsis when over) — a
    sibling overview only needs the gist, not the tail."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _explicit_timeout_s(item: dict[str, Any]) -> int | None:
    """CEO-explicit ``timeout_ms`` → seconds; omit → None (worker_budget fills)."""
    raw_timeout = item.get("timeout_ms")
    if isinstance(raw_timeout, bool):
        return None
    if isinstance(raw_timeout, int) and raw_timeout > 0:
        return max(1, raw_timeout // 1000)
    if isinstance(raw_timeout, float) and raw_timeout > 0:
        return max(1, int(raw_timeout) // 1000)
    return None


def _dag_policy(item: dict[str, Any]) -> RunPolicy:
    """Map a DAG node's declarative knobs onto a RunPolicy (the WaveScheduler
    reads on_failure; result_handling feeds the dep-context size).

    ``timeout_ms`` is CEO-explicit only: omit → ``None``, filled later by
    :func:`~agentcore.runtime.runs.worker_budget.apply_worker_budgets`.
    """
    raw_on_failure = item.get("on_failure", DEFAULT_ON_FAILURE)
    on_failure = raw_on_failure if raw_on_failure in VALID_ON_FAILURE else DEFAULT_ON_FAILURE
    return RunPolicy(
        on_failure=on_failure,  # type: ignore[arg-type]
        timeout_s=_explicit_timeout_s(item),
        result_handling=item.get("result_handling") or "pass_through",
    )


def _parse_deliverable(item: dict[str, Any]) -> Deliverable:
    """Parse a task's ``deliverable`` into a :class:`Deliverable`.

    Nodes always carry a Deliverable. Missing object / empty object / omitted
    or invalid ``form`` → ``files``. Playbook-internal knobs still parse; unknown
    keys are ignored.
    """
    raw = item.get("deliverable")
    if not isinstance(raw, dict):
        raw = {}
    return _deliverable_from_dict(raw)


def prose_form_conflict_error(item: dict[str, Any]) -> str | None:
    """Reject raw ``form=prose`` ∩ non-empty ``artifacts``.

    Must run on the CEO's raw task dict **before** :func:`_deliverable_from_dict`
    clears artifacts for prose — otherwise the gate never fires.
    """
    raw = item.get("deliverable")
    if not isinstance(raw, dict):
        return None
    if raw.get("form") != "prose":
        return None
    arts = raw.get("artifacts")
    has_artifacts = isinstance(arts, list) and any(
        isinstance(a, str) and a.strip() for a in arts
    )
    if not has_artifacts:
        return None
    return (
        "契约矛盾：deliverable.form=prose 不能同时声明 artifacts。"
        "纯文字交付请去掉 artifacts；若需落盘/钉路径请改 form=files 或 form=workspace。"
    )


def _deliverable_from_dict(raw: dict[str, Any]) -> Deliverable:
    required_sections = _str_list(raw.get("required_sections"))
    artifacts = _str_list(raw.get("artifacts"))
    fmt = raw.get("output_format")
    output_format = fmt if fmt in _VALID_OUTPUT_FORMATS else "text"
    native_in = bool(raw.get("workspace_native", False))
    form = normalize_deliverable_form(raw.get("form"), workspace_native=native_in)
    # form=prose ∩ artifacts is rejected upstream (:func:`prose_form_conflict_error`);
    # do not silently coerce that combo away.
    if form == "prose":
        artifacts = []  # path reconciliation meaningless for prose delivery
        artifact_dir = ""
        workspace_native = False  # 纯文字交付无落点可言
    else:
        artifact_dir_raw = raw.get("artifact_dir", "")
        artifact_dir = (
            artifact_dir_raw.replace("\\", "/").strip().rstrip("/")
            if isinstance(artifact_dir_raw, str)
            else ""
        )
        workspace_native = form == "workspace"
    web_seam_scope = raw.get("web_seam_scope", "")
    if not isinstance(web_seam_scope, str):
        web_seam_scope = ""
    placeholder_hard_exempt = bool(raw.get("placeholder_hard_exempt", False))
    placeholder_hard_exempt_artifacts = _str_list(
        raw.get("placeholder_hard_exempt_artifacts")
    )
    web_quality_scan = bool(raw.get("web_quality_scan", False))
    web_quality_soft_exempt = bool(raw.get("web_quality_soft_exempt", False))
    web_quality_soft_exempt_labels = _str_list(
        raw.get("web_quality_soft_exempt_labels")
    )
    visual_critic = bool(raw.get("visual_critic", False))
    citation_mode_raw = raw.get("citation_mode")
    citation_mode = citation_mode_raw if citation_mode_raw == "two_phase" else None
    return Deliverable(
        output_format=output_format,
        required_sections=required_sections,
        form=form,
        artifacts=artifacts,
        artifact_dir=artifact_dir,
        workspace_native=workspace_native,
        web_seam_scope=web_seam_scope.strip(),
        placeholder_hard_exempt=placeholder_hard_exempt,
        placeholder_hard_exempt_artifacts=placeholder_hard_exempt_artifacts,
        web_quality_scan=web_quality_scan,
        web_quality_soft_exempt=web_quality_soft_exempt,
        web_quality_soft_exempt_labels=web_quality_soft_exempt_labels,
        visual_critic=visual_critic,
        strict=bool(raw.get("strict", False)),
        citation_mode=citation_mode,  # type: ignore[arg-type]
        code_audit_gate=bool(raw.get("code_audit_gate", False)),
    )


def _str_list(value: Any) -> list[str]:
    """Normalise a declared list field to non-empty trimmed strings."""
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]
