"""CEO-facing synthesis input formatting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.runtime.delegate.plan_events import emit_captain_readback
from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT

if TYPE_CHECKING:
    from agentcore.runtime.runs.plan import RunPlan

DelegateTool = Any

logger = get_logger(__name__)


def _inline_path_manifest(
    files: list[str],
    rejected: list[tuple[str, str]],
    *,
    cap: int,
) -> str:
    """CEO body suffix: accepted / rejected paths, remainder counted not listed."""
    chunks: list[str] = []
    if files:
        listed = files[:cap]
        produced = "、".join(f"`{p}`" for p in listed)
        extra = len(files) - len(listed)
        if extra:
            produced += f"（另有 {extra} 个）"
        chunks.append(f"> 文件产出（路径已核）：{produced}")
    if rejected:
        shown = rejected[:cap]
        bits = [
            f"`{p}`" + (f"（{detail}）" if detail else "") for p, detail in shown
        ]
        line = f"> 路径未核：{'、'.join(bits)}"
        extra = len(rejected) - len(shown)
        if extra:
            line += f"（另有 {extra} 个）"
        chunks.append(line)
    return ("\n\n" + "\n\n".join(chunks)) if chunks else ""


def escalation_block(tool: DelegateTool, plan: RunPlan, results: dict) -> str:
    """The CEO-facing「队员升级」section, or "" when no worker escalated."""
    pending: list[tuple[bool, str]] = []
    answered: list[str] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if not state or not state.escalations:
            continue
        label = node.role or node.run_id
        for e in state.escalations:
            question = str(e.get("question") or "").strip()
            if not question:
                continue
            if str(e.get("status") or "raised") == "resolved":
                answer = str(e.get("answer") or "").strip()
                answered.append(f"- {label}：{question} → 用户已答：{answer}")
                continue
            blocking = bool(e.get("blocking"))
            # 卡在缺输入·依赖缺口 (§2.4 变·worker 的「拉」): if this run got here (synthesis) rather
            # than being settled at the reactive wave boundary, mark it so the CEO补 a producer.
            is_dep = e.get("kind") == "dep"
            mark = "【关键阻塞】" if blocking else ("【缺输入·依赖缺口】" if is_dep else "")
            line = f"- {mark}{label}：{question}"
            assumption = str(e.get("assumption") or "").strip()
            if assumption:
                line += f"（其暂用假设：{assumption}）"
            pending.append((blocking, line))
    if not pending and not answered:
        return ""
    out = ""
    if pending:
        pending.sort(key=lambda it: not it[0])
        out += (
            "\n### ⚠️ 队员升级了待决问题（请先处理再收尾）\n"
            "以下是队员无法独自拍板、需要你定夺的关键岔路 / 缺失信息。它们已按各自的暂定假设"
            "继续交付，但你应先处理这些问题：能自己答的就在概览里给出并据此判断相关产物是否需"
            "返工；确需用户拍板的就用 ask_user 问（可把问题 near-verbatim 转给用户）；需要原"
            "作者据答案重做的就用 delegate 设 continue_from_run_id 带现场续派；标【缺输入·依赖缺口】的是队员卡在缺一个还不存在"
            "的输入——用 delegate 补一个产出它的步骤，再设 continue_from_run_id 把结果交回原作者据此续写。\n"
            + "\n".join(line for _, line in pending)
        )
    if answered:
        out += (
            "\n### ✅ 已当场答复的升级（用户在执行中已拍板，无需再问）\n"
            "以下升级队员已直接问到用户、拿到答复并据此续跑；把这些结论纳入你的收尾叙事即可，"
            "不要再用 ask_user 重复问同样的问题。\n" + "\n".join(answered)
        )
    return out


def worker_products(tool: DelegateTool, plan: RunPlan, results: dict) -> list[dict[str, Any]]:
    """Each worker's product folded back to the CEO — SINGLE SOURCE for synthesis + run_context."""
    from agentcore.runtime.runs.constants import (
        CEO_SYNTHESIS_BUDGET,
        CEO_SYNTHESIS_FILE_LIST_MAX,
        CEO_SYNTHESIS_POINTER_CHARS,
    )
    from agentcore.runtime.runs.contract import node_has_dependents
    from agentcore.runtime.runs.fidelity import allocate, truncate_head_tail
    from agentcore.runtime.runs.types import RunPhase

    # Hot-redirect revisions (``{run_id}_revN``) are not plan nodes; map original → revision
    # so a CANCELLED+redirected worker surfaces its continued product, not「无输出」.
    hot_by_original: dict[str, tuple[str, Any]] = {}
    plan_ids = {n.run_id for n in plan.nodes}
    for rid, st in results.items():
        if rid in plan_ids or st is None:
            continue
        if st.phase is not RunPhase.COMPLETED:
            continue
        # Empty prose + files_touched is still a real product (pointer path).
        if not (st.content or "").strip() and not st.files_touched:
            continue
        # Naming: continue_run / redirect use ``{target}_rev{n}``
        if "_rev" in rid:
            orig = rid.rsplit("_rev", 1)[0]
            if orig in plan_ids:
                hot_by_original[orig] = (rid, st)

    # 完工交接简报: the content is already the pure deliverable (each worker's brief rides its
    # structured ``debrief`` from the handoff tool — never appended to the prose), so the body
    # sizes on the deliverable alone, the author's own 结论 LEADS the body, and 建议下一步 is
    # surfaced separately (format_for_ceo) for the CEO to relay to the user.
    # Empty ``content`` must NOT drop ``debrief`` — file producers often hand off via
    # summary/key_points only (pointer fidelity).
    cleaned: dict[str, tuple[str, dict[str, Any] | None]] = {}
    for node in plan.nodes:
        st = results.get(node.run_id)
        if node.run_id in hot_by_original:
            _rid, hot_st = hot_by_original[node.run_id]
            cleaned[node.run_id] = (hot_st.content or "", hot_st.debrief)
        elif st:
            cleaned[node.run_id] = (st.content or "", st.debrief)
        else:
            cleaned[node.run_id] = ("", None)

    def _mode(node) -> str:
        if node.run_id in hot_by_original:
            _rid, hot_st = hot_by_original[node.run_id]
            if hot_st.files_touched:
                return "pointer"
            return "pass_through" if hot_st.content else "none"
        st = results.get(node.run_id)
        # Only COMPLETED products are pass_through / pointer. FAILED nodes often
        # still carry a body (contract miss after retries) — that must surface as
        # 「失败：…」via the error branch below, never as a fake delivered product.
        if not st or st.phase is not RunPhase.COMPLETED:
            return "none"
        # File producers: empty prose + files_touched still → pointer (brief / paths).
        if st.files_touched:
            return "pointer"
        if not st.content:
            return "none"
        return "pass_through"

    modes = {node.run_id: _mode(node) for node in plan.nodes}
    allowances = iter(
        allocate(
            [
                len(cleaned[node.run_id][0])
                for node in plan.nodes
                if modes[node.run_id] == "pass_through"
            ],
            CEO_SYNTHESIS_BUDGET,
        )
    )
    # Cold handoff: a CANCELLED original that a ``_redir`` (replaces_run_id) took over
    # must not surface as「失败/被跳过」— the handoff node is the product.
    replaced_ids = {n.replaces_run_id for n in plan.nodes if n.replaces_run_id}

    products: list[dict[str, Any]] = []
    for node in plan.nodes:
        state = results.get(node.run_id)
        if (
            state is not None
            and state.phase is RunPhase.CANCELLED
            and node.run_id in replaced_ids
            and node.run_id not in hot_by_original
        ):
            continue
        hot = hot_by_original.get(node.run_id)
        if hot is not None:
            status = "completed"
            state = hot[1]
        else:
            status = state.phase.value if state else "unknown"
        label = node.role or node.run_id
        mode = modes[node.run_id]
        clean, debrief = cleaned[node.run_id]
        author_summary = (debrief or {}).get("summary", "") if debrief else ""
        raw_points = (debrief or {}).get("key_points") if debrief else None
        key_points = (
            [str(p).strip() for p in raw_points if str(p).strip()]
            if isinstance(raw_points, list)
            else []
        )
        fidelity = ""
        truncated = False
        if mode == "pointer":
            # Prefer structured handoff (summary + key_points + files) over a long
            # prose digest when this node has dependents — full artifact is on disk
            # / in the UI, and CEO only needs the brief. Leaves: captain reads the
            # pointer body / landed paths; a missing brief is not a hole.
            body = _compact_worker_body(
                clean=clean,
                author_summary=author_summary,
                key_points=key_points,
                prose_limit=CEO_SYNTHESIS_POINTER_CHARS,
                prefer_brief=node_has_dependents(plan, node.run_id),
            )
            fidelity, truncated = "pointer", True
        elif mode == "pass_through":
            allowance = next(allowances)
            # Leaves (no files, no downstream): conclusion lives in the body —
            # include it, sized to the shared budget (do not clip to
            # CEO_SYNTHESIS_POINTER_CHARS). Intermediate nodes: downstream already
            # read the full body via the 16k dep-context pool; CEO only needs the brief.
            prefer_brief = bool(author_summary or key_points) and node_has_dependents(
                plan, node.run_id
            )
            if author_summary or key_points:
                body = _compact_worker_body(
                    clean=clean,
                    author_summary=author_summary,
                    key_points=key_points,
                    prose_limit=allowance,
                    prefer_brief=prefer_brief,
                )
                # Mid-node prefer_brief drops the body — that is a cut, not "intact".
                truncated = (
                    bool(clean.strip())
                    if prefer_brief
                    else len(clean) > allowance
                )
            else:
                body = truncate_head_tail(clean, allowance)
                truncated = len(clean) > allowance
            fidelity = "pass_through"
        elif state and state.error:
            body = f"（失败：{state.error}）"
        else:
            body = "（无输出）"
        if node.replaces_run_id:
            body = (
                f"【接替】本节点 replaces_run_id=`{node.replaces_run_id}`"
                f"（接手原失败/取消队员）\n\n{body}"
            )
        if state and state.warnings:
            warns = "；".join(state.warnings)
            body += f"\n\n> 质检提醒（未完全达标，请判断是否需要返工）：{warns}"
        if state and state.escalations:
            body += (
                f"\n\n> 已升级 {len(state.escalations)} 项待决问题（见顶部「队员升级了"
                "待决问题」，请先处理再据此判断本产物是否需返工）"
            )
        files = []
        rejected_files: list[tuple[str, str]] = []
        # 与 delivery_status 同源：COMPLETED 只认 file_acceptance 戳；FAILED 未盖戳但已落盘
        # 的产物仍计入（勿用 COMPLETED 的 files_touched 冒充验收）。
        if state:
            from agentcore.runtime.delegate.delivery_status import _collect_artifacts

            for row in _collect_artifacts({node.run_id: state}, plan):
                path = str(row.get("path") or "").strip()
                if not path:
                    continue
                if row.get("status") == "accepted":
                    files.append(path)
                elif row.get("status") == "rejected":
                    detail = str(row.get("detail") or row.get("reason") or "").strip()
                    rejected_files.append((path, detail))
        body += _inline_path_manifest(
            files, rejected_files, cap=CEO_SYNTHESIS_FILE_LIST_MAX
        )
        tool_failures = (
            [dict(row) for row in state.tool_failures if isinstance(row, dict)]
            if state and state.tool_failures
            else []
        )
        products.append(
            {
                "role": label,
                "run_id": hot[0] if hot else node.run_id,
                "status": status,
                "body": body,
                "fidelity": fidelity,
                "truncated": truncated,
                "files": files,
                "replaces_run_id": node.replaces_run_id,
                "tool_failures": tool_failures,
            }
        )
    return products


def _compact_worker_body(
    *,
    clean: str,
    author_summary: str,
    key_points: list[str],
    prose_limit: int,
    prefer_brief: bool,
) -> str:
    """Brief-first body: summary + short bullets; optional prose when not prefer_brief."""
    from agentcore.runtime.runs.fidelity import truncate_head_tail

    parts: list[str] = []
    if author_summary:
        parts.append(f"交接结论：{author_summary}")
    if key_points:
        bullets = "\n".join(f"- {p}" for p in key_points[:6])
        parts.append(f"要点：\n{bullets}")
    if prefer_brief and (author_summary or key_points):
        # Pointer / mid-node: structured brief present — skip dumping the full body.
        return "\n\n".join(parts) if parts else "（无摘要）"
    if clean.strip():
        parts.append(truncate_head_tail(clean, prose_limit))
    return "\n\n".join(parts) if parts else "（无输出）"


def _roster_facts(
    plan: RunPlan, results: dict, products: list[dict[str, Any]]
) -> dict[str, Any]:
    """Deterministic per-run roster counters so CEO synthesis cannot invent「全部交付」.

    Ground truth, not prose. Kept structured so the harvest path can render it
    *after* worker prose is truncated — a roster that lost a truncation race is
    worse than useless: the closing discipline still tells the CEO to check it.
    """
    from agentcore.runtime.runs.types import RunPhase

    replaced_ids = {n.replaces_run_id for n in plan.nodes if n.replaces_run_id}
    replace_by: dict[str, str] = {
        n.replaces_run_id: (n.role or n.run_id) for n in plan.nodes if n.replaces_run_id
    }
    completed = failed = skipped = cancelled = other = 0
    failed_lines: list[str] = []
    replaced_lines: list[str] = []
    cancel_cascade_lines: list[str] = []
    budget_skip_lines: list[str] = []
    for node in plan.nodes:
        st = results.get(node.run_id)
        label = node.role or node.run_id
        phase = st.phase if st is not None else None
        if node.replaces_run_id:
            replaced_lines.append(
                f"- {label}（`{node.run_id}`）接替失败/取消节点 `{node.replaces_run_id}`"
            )
        if phase is RunPhase.COMPLETED:
            completed += 1
        elif phase is RunPhase.FAILED:
            failed += 1
            err = (st.error or "").strip() if st else ""
            successor = replace_by.get(node.run_id)
            note = f"；已被 {successor} 接替" if successor else ""
            failed_lines.append(
                f"- {label}（`{node.run_id}`）失败{('：' + err) if err else ''}{note}"
            )
        elif phase is RunPhase.SKIPPED:
            skipped += 1
            budget_skip = False
            if st is not None:
                for gap in st.delivery_gaps or []:
                    if isinstance(gap, dict) and gap.get("reason") == "turn_token_budget":
                        budget_skip = True
                        break
            if budget_skip:
                budget_skip_lines.append(
                    f"- {label}（`{node.run_id}`）因回合/子团队额度未跑；"
                    "下一回合请 append 同图或 replan/点名续派该角色，"
                    "禁止本回合假装已全部完成"
                )
            # Cancel / hard-absence skips under strict extra-key fan-in, or
            # lenient zero-success: tip CEO to replace, not to fill a join knob.
            upstream_cancel = any(
                (results.get(d) is not None and results[d].phase is RunPhase.CANCELLED)
                for d in (node.depends_on or [])
            )
            if upstream_cancel and not budget_skip:
                cancel_cascade_lines.append(
                    f"- {label}（`{node.run_id}`）因上游取消未跑；"
                    "其它上游已交付时下游默认可继续；"
                    "零上游成功仍要补人：用 replaces_run_id 重派"
                )
        elif phase is RunPhase.CANCELLED:
            # Cold handoff originals that a replaces_run_id took over stay cancelled —
            # count them as replaced, not as open cancellations.
            if node.run_id in replaced_ids:
                continue
            cancelled += 1
        else:
            other += 1
    # Surface product-level status (hot-redirect may mark completed even if original cancelled).
    product_failed = sum(1 for p in products if p.get("status") not in ("completed",))
    # 条数同源：本波 file_acceptance（用户面卡片另按 execution 并集，见 delivery_status）。
    from agentcore.runtime.delegate.delivery_status import acceptance_counts

    accepted_n, rejected_n = acceptance_counts(results)
    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "cancelled": cancelled,
        "other": other,
        "products": len(products),
        "product_failed": product_failed,
        "accepted_files": accepted_n,
        "rejected_files": rejected_n,
        "failed_lines": failed_lines,
        "budget_skip_lines": budget_skip_lines,
        "cancel_cascade_lines": cancel_cascade_lines,
        "replaced_lines": replaced_lines,
    }


def render_roster_block(facts: dict[str, Any]) -> str:
    """Render :func:`_roster_facts` counters into the CEO-facing roster section."""
    completed = int(facts.get("completed") or 0)
    failed = int(facts.get("failed") or 0)
    skipped = int(facts.get("skipped") or 0)
    cancelled = int(facts.get("cancelled") or 0)
    other = int(facts.get("other") or 0)
    products_n = int(facts.get("products") or 0)
    product_failed = int(facts.get("product_failed") or 0)
    accepted_n = int(facts.get("accepted_files") or 0)
    rejected_n = int(facts.get("rejected_files") or 0)
    failed_lines = list(facts.get("failed_lines") or [])
    budget_skip_lines = list(facts.get("budget_skip_lines") or [])
    cancel_cascade_lines = list(facts.get("cancel_cascade_lines") or [])
    replaced_lines = list(facts.get("replaced_lines") or [])

    lines = [
        "\n### 队员终态名册（地面真相——写终稿必须对照，禁止编造「全部交付」）\n"
        f"计划节点：完成 {completed} · 失败 {failed} · 跳过 {skipped} · 取消 {cancelled}"
        + (f" · 其他 {other}" if other else "")
        + f"；综述可见产物 {products_n} 条"
        + (f"（其中非完成 {product_failed}）" if product_failed else "")
        + f"；路径核对：已核 {accepted_n} · 未通过 {rejected_n}"
        + "。"
    ]
    if failed_lines:
        lines.append("失败节点：\n" + "\n".join(failed_lines))
    if budget_skip_lines:
        lines.append("因额度跳过、从未开跑（下一回合可续）：\n" + "\n".join(budget_skip_lines))
    if cancel_cascade_lines:
        lines.append(
            "上游取消导致下游未跑（其它路都没交上时）：\n"
            + "\n".join(cancel_cascade_lines)
        )
    if replaced_lines:
        lines.append("接替关系（replaces_run_id）：\n" + "\n".join(replaced_lines))
    if failed or skipped or product_failed or replaced_lines or cancel_cascade_lines:
        lines.append(
            "【叙事铁律】终稿必须如实写清部分失败与接替：点名失败角色/run、是否已被谁接替、"
            "用户可见影响；禁止「N 位队员全部交付 / 全部完成 / 全员成功」类措辞——"
            "协作图上的失败节点与此名册不一致时，以名册为准。"
            + (
                "额度跳过节点须写明「未跑、可下一回合续」，禁止装成已交付。"
                if budget_skip_lines
                else ""
            )
        )
    return "\n".join(lines)


def _roster_block(plan: RunPlan, results: dict, products: list[dict[str, Any]]) -> str:
    return render_roster_block(_roster_facts(plan, results, products))


@dataclass(frozen=True)
class CeoSynthesis:
    """One drive-terminal synthesis, split by what may and may not be truncated.

    ``text`` is the CEO's in-turn ToolResult read (unchanged). ``prose`` is the
    lossy part alone: the harvest path trims that and re-attaches ``roster_text``
    / ``closing_text`` afterwards, so ground truth never loses a budget race
    against worker prose — the closing discipline orders the CEO to check the
    roster, and a silently elided roster turns that check into a rubber stamp.
    """

    text: str
    prose: str
    roster_text: str
    roster_facts: dict[str, Any]
    closing_text: str


def build_ceo_synthesis(
    tool: DelegateTool,
    plan: RunPlan,
    results: dict,
    *,
    call_idx: int | None = None,
) -> CeoSynthesis:
    """Render the workers' products as the CEO's overview input, split by loss policy."""
    lines = ["## 团队执行结果（据此写一段简短概览交给用户；完整详情用户自行查看）"]
    escalation = escalation_block(tool, plan, results)
    if escalation:
        lines.append(escalation)

    from agentcore.runtime.delegate.completion import (
        collect_worker_gaps,
        format_worker_gaps_block,
    )
    from agentcore.runtime.runs.research_quality import collect_thin_review_gaps

    gaps = collect_worker_gaps(plan, results)
    # 案 thin-review A′：已声明 reviews/ 契约未对齐 → 并入 CEO 契约缺口表（与 delivery 同谓词）。
    thin_by_role: dict[str, list[dict[str, str]]] = {}
    for row in collect_thin_review_gaps(plan.nodes, results):
        role = str(row.get("role") or "").strip() or "验收"
        thin_by_role.setdefault(role, []).append(
            {
                "description": str(row.get("description") or ""),
                "reason": str(row.get("reason") or "thin_review"),
            }
        )
    if thin_by_role:
        # Merge into existing worker rows when role matches; else append.
        existing_roles = {label: i for i, (label, _) in enumerate(gaps)}
        for role, rows in thin_by_role.items():
            if role in existing_roles:
                gaps[existing_roles[role]][1].extend(rows)
            else:
                gaps.append((role, rows))
    gaps_block = format_worker_gaps_block(gaps)
    if gaps_block:
        lines.append(gaps_block)

    products = worker_products(tool, plan, results)
    from agentcore.runtime.tool_failures import format_team_tool_failures_block

    tool_failures_block = format_team_tool_failures_block(products)
    if tool_failures_block:
        lines.append(tool_failures_block)
    roster_facts = _roster_facts(plan, results, products)
    roster_text = render_roster_block(roster_facts)
    head_lines = list(lines)
    lines = []
    emit_captain_readback(tool, products)
    for wp in products:
        lines.append(
            f"\n### {wp['role']}（{wp['status']}） · run_id: `{wp['run_id']}`\n{wp['body']}"
        )
    # Lean footer: product-format facts the model cannot invent. HOW
    # (过程简述 / 粘名册 / replan) lives on wait / replan description.
    closing_text = (
        "\n---\n以上为团队产出。「文件产出（路径已核）」= 落盘且路径核对通过的地面真相。\n"
        "⚠️ 防幻觉铁律：是否真交付文件只看「文件产出（路径已核）」行——"
        "正文声称写了却无此行 = 未真正落盘【未达成】；"
        "「路径未核」不得当已交付；纯文本无文件属正常。"
        "路径已核 ≠ 脚本已跑通 / 内容已校验。\n"
        "相互依赖时【语义边界对账】（冲突/缺口/重复）；"
        "【完工核验】对照用户请求：未达成就补，已达成则短概览收口。\n"
        "【终稿纪律】对照【队员终态名册】："
        "失败/跳过/接替必须写入，禁止编造「全部交付」。"
    )
    if any(wp["status"] != "completed" for wp in products) or any(
        n.replaces_run_id for n in plan.nodes
    ):
        closing_text += (
            "\n---\n**有队员失败/被跳过/被接替。** 终稿须点名说明，不得写成全员成功。"
        )
    prose = "\n".join([*head_lines, *lines])
    from agentcore.runtime.delegate.terminal_output import compose_all_completed_output

    output = compose_all_completed_output(
        prose, roster_text, closing_text, limit=DELEGATE_OUTPUT_LIMIT
    )
    raw_chars = sum(len(s.content) for s in results.values() if s and s.content)
    naive_len = len(
        "\n".join(p for p in (prose, roster_text, closing_text) if (p or "").strip())
    )
    capped = naive_len > DELEGATE_OUTPUT_LIMIT
    execution_id = str(
        getattr(getattr(tool, "_base_tool_context", None), "execution_id", "") or ""
    )
    fingerprint = f"{len(plan.nodes)}:{raw_chars}:{len(output)}:{output[:128]}:{output[-128:]}"
    prev = getattr(tool, "_ceo_synthesis_emitted", None)
    if prev != (execution_id, fingerprint):
        tool._ceo_synthesis_emitted = (execution_id, fingerprint)
        logger.info(
            "delegate.synthesis",
            call=call_idx if call_idx is not None else tool._calls,
            workers=len(plan.nodes),
            pointers=sum(1 for p in products if p["fidelity"] == "pointer"),
            prose=sum(1 for p in products if p["fidelity"] == "pass_through"),
            raw_chars=raw_chars,
            final_chars=len(output),
            ratio=round(len(output) / raw_chars, 2) if raw_chars else 1.0,
            capped=capped,
            synthesis_cap=DELEGATE_OUTPUT_LIMIT,
            ratio_capped=False,
        )
    return CeoSynthesis(
        text=output,
        prose=prose,
        roster_text=roster_text,
        roster_facts=roster_facts,
        closing_text=closing_text,
    )


def format_for_ceo(
    tool: DelegateTool, plan: RunPlan, results: dict, *, call_idx: int | None = None
) -> str:
    """Render the workers' products as the CEO's overview input."""
    return build_ceo_synthesis(tool, plan, results, call_idx=call_idx).text
