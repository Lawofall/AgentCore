"""Worker / captain context blocks and opening messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.runs.constants import (
    CONTEXT_INJECT_CHARS,
    DEP_CONTEXT_BUDGET,
    DEP_SUMMARY_CHARS,
)
from agentcore.runtime.runs.contract import describe_deliverable
from agentcore.runtime.runs.executor.identities import (
    _WORKER_IDENTITY,
)
from agentcore.runtime.runs.fidelity import allocate, pointer_body, truncate_head_tail
from agentcore.runtime.runs.plan import RunPlan
from agentcore.runtime.runs.types import (
    ContextBlock,
    Deliverable,
    RunPhase,
    RunSpec,
    RunState,
)
from agentcore.workspace.stage_dirs import DRAFTS_DIR

logger = get_logger(__name__)


def _observe_worker_opening(
    *,
    worker_base: str,
    identity: str,
    role: str | None,
    supplement: str | None,
    working_set: str | None = None,
) -> None:
    """COST-004: log worker opening system sections (observe-only; join stays ``\\n\\n``)."""
    from agentcore.config import settings
    from agentcore.runtime.context import ContextAssembler, SectionOrder

    role_text = f"你的角色：{role}" if role else None
    (
        ContextAssembler()
        .add("worker_base", worker_base, SectionOrder.BASE)
        .add("identity", identity, SectionOrder.WORKER_IDENTITY)
        .add("role", role_text, SectionOrder.WORKER_ROLE)
        .add("supplement", supplement, SectionOrder.WORKER_SUPPLEMENT)
        .add("working_set", working_set, SectionOrder.WORKING_SET)
        .observe(scope="worker_turn", soft_cap=settings.prompt_budget_char_soft_cap)
    )


def _format_upstream_absence(
    dep_id: str,
    label: str,
    state: RunState | None,
) -> str:
    """One-liner explaining why an upstream did not deliver a usable product."""
    if state is None:
        return (
            f"上游「{label}」（`{dep_id}`）缺席：尚未产生可交接的产出"
            "（未执行或级联跳过）。请基于其余已交付上游继续，勿编造该路结论。"
        )
    phase = state.phase
    err = (state.error or "").strip()
    if phase is RunPhase.CANCELLED:
        reason = f"已取消{('：' + err) if err else ''}"
    elif phase is RunPhase.FAILED:
        reason = f"执行失败{('：' + err) if err else ''}"
    elif phase is RunPhase.SKIPPED:
        reason = "被跳过未执行"
    elif phase is RunPhase.COMPLETED:
        reason = "已完成但无交接正文/落盘为空"
    else:
        reason = f"状态={phase.value}{('：' + err) if err else ''}"
    return (
        f"上游「{label}」（`{dep_id}`）缺席：{reason}。"
        "请基于其余已交付上游继续，勿编造该路结论。"
    )


def _ancestors_by_id(plan: RunPlan) -> dict[str, frozenset[str]]:
    """Write-guard ancestors per node: ``depends_on`` transitive closure ∪ nested parent.

    A node may overwrite a file owned by an upstream it consolidates (DAG handoff)
    or by its nested ``parent_run_id`` (lead declared, child executes) — but never
    one held by an unrelated concurrent sibling. Missing dep ids (pruned plan) are
    skipped. Immediate ``parent_run_id`` is always included when set, even if that
    parent is outside this plan (typical nested sub-team). O(nodes + edges).
    """
    dep_only: dict[str, set[str]] = {}
    for node in plan.nodes:
        seen: set[str] = set()
        stack = list(node.depends_on)
        while stack:
            dep_id = stack.pop()
            if dep_id in seen:
                continue
            seen.add(dep_id)
            dep = plan.by_id(dep_id)
            if dep is not None:
                stack.extend(dep.depends_on)
        dep_only[node.run_id] = seen

    out: dict[str, frozenset[str]] = {}
    for node in plan.nodes:
        seen = set(dep_only[node.run_id])
        parent = (getattr(node, "parent_run_id", None) or "").strip()
        if parent:
            seen.add(parent)
            # Rare: parent also a node in this plan — inherit its depends_on closure.
            if parent in dep_only:
                seen |= dep_only[parent]
        out[node.run_id] = frozenset(seen)
    return out


def _build_messages(
    plan: RunPlan,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    system_prompt: str,
    user_message: str,
    deliverable: Deliverable | None = None,
    identity: str = _WORKER_IDENTITY,
    blocks_sink: list[ContextBlock] | None = None,
    team_brief: str | None = None,
    context_inject: Mapping[str, str] | None = None,
    conversation_id: str = "",
    working_set: str = "",
) -> list[LLMMessage]:
    """Assemble the worker's OPENING (system, user) messages from its inline role,
    the original request, its upstream dependency products, and its task.

    ``deliverable`` (when present) is stated up front as hard requirements so the
    worker aims to meet it on the first pass. This builds only the opening turn; a
    contract retry no longer rebuilds from scratch — the executor CONTINUES on this
    same transcript by appending the shortfall (:func:`_retry_message`), so the
    worker sees its own prior draft. ``identity`` is the worker's self-awareness
    preamble — the leaf-worker default, or the captain variant for a worker
    authorized to lead one nested sub-team.

    单一源 (上下文传递可视化): the user message is RENDERED from the ordered ContextBlock
    list :func:`_build_context_blocks` assembles; when ``blocks_sink`` is given, that exact
    list is handed back so the caller can ship it as the ``run_context`` event — what the
    user sees == what the LLM eats, one assembly, no second「展示」path to drift."""
    # Stable ``<身份>`` sits in front of the shared base so leaf workers share a
    # cacheable prefix; node contract (form / handoff) stays after the base.
    core, sep, rest = identity.partition("</身份>")
    if sep:
        sys_parts = [f"{core}{sep}", system_prompt]
        if rest.strip():
            sys_parts.append(rest.strip())
    else:
        sys_parts = [system_prompt, identity]
    if spec.role:
        sys_parts.append(f"你的角色：{spec.role}")
    if spec.system_prompt_supplement:
        sys_parts.append(spec.system_prompt_supplement)
    if working_set:
        sys_parts.append(working_set)
    system_content = "\n\n".join(p for p in sys_parts if p)
    _observe_worker_opening(
        worker_base=system_prompt,
        identity=identity,
        role=spec.role,
        supplement=spec.system_prompt_supplement,
        working_set=working_set,
    )

    blocks = _build_context_blocks(
        plan,
        spec,
        completed,
        user_message,
        deliverable,
        team_brief,
        context_inject=context_inject,
    )
    if blocks_sink is not None:
        blocks_sink.extend(blocks)
    user_content = "\n\n".join(f"## {b.heading}\n{b.body}" for b in blocks)
    return [
        LLMMessage(role="system", content=system_content),
        LLMMessage(role="user", content=user_content),
    ]


def _build_context_blocks(
    plan: RunPlan,
    spec: RunSpec,
    completed: Mapping[str, RunState],
    user_message: str,
    deliverable: Deliverable | None,
    team_brief: str | None = None,
    *,
    context_inject: Mapping[str, str] | None = None,
) -> list[ContextBlock]:
    """The ordered :class:`ContextBlock` list a worker's opening user message is rendered
    FROM — the structured single source behind both the prompt and the ``run_context``
    event (上下文传递可视化, 5 通道之 worker 侧). Each block becomes a「## {heading}\n{body}」
    section verbatim, so 用户看到的 == LLM 吃到的.

    团队位置（DAG 拓扑感知）: the worker sees the team-level 原始用户请求 verbatim; on its own
    that reads as a personal mandate, so an UPSTREAM link — blind to the writer downstream —
    used to chase the final artifact itself (上游越权写整篇 + 无文件名的空路径 file_write).
    The position block hands the node its TOPOLOGY (peers + where its output GOES), symmetric
    to :func:`_dep_context_blocks` handing a downstream node its upstream PRODUCTS; the
    request header is reframed as a team goal only when the node is actually on a team (a
    solo worker IS the whole job)."""
    blocks: list[ContextBlock] = []
    position = _team_position_block(plan, spec)
    if position:
        blocks.append(
            ContextBlock(
                channel="request",
                heading=(
                    "原始用户请求（老板交给整个团队的目标，不一定全是你的活；"
                    "你的具体职责见下方「你的任务」）"
                ),
                body=user_message,
            )
        )
        blocks.append(
            ContextBlock(channel="team_position", heading="你在团队中的位置", body=position)
        )
    else:
        blocks.append(ContextBlock(channel="request", heading="原始用户请求", body=user_message))
    blocks.extend(_dep_context_blocks(plan, spec.depends_on, completed))
    blocks.extend(_context_inject_blocks(context_inject))
    if team_brief:
        blocks.append(
            ContextBlock(
                channel="team_brief",
                heading="团队共识（主协调为本回合设定，全员遵循）",
                body=team_brief,
            )
        )
    blocks.append(ContextBlock(channel="task", heading="你的任务", body=spec.task))
    deliverable_text = describe_deliverable(deliverable or spec.deliverable)
    if deliverable_text:
        blocks.append(
            ContextBlock(channel="deliverable", heading="交付物规格", body=deliverable_text)
        )
    if spec.gate_notes:
        # plan_review CONTINUE：用户已放行的主 Agent llm 把关压缩要点（非否决）。
        # 在 steer 之前：用户「调整」备注仍最后、最高优先。
        blocks.append(
            ContextBlock(
                channel="gate_notes",
                heading="用户已放行的主 Agent 注意事项（非否决，勿停工另起炉灶）",
                body=spec.gate_notes,
            )
        )
    if spec.steer:
        # A mid-course user steer (plan_review adjust) injected after upstream work
        # was reviewed: stated last + highest-priority so it overrides the task
        # framing above when they conflict (结构化挂起 adjust).
        blocks.append(
            ContextBlock(
                channel="steer",
                heading="用户中途调整指示（执行中追加，优先级最高，请据此调整工作）",
                body=spec.steer,
            )
        )
    return blocks


def _format_captain_history(history: list[dict]) -> str:
    """Render the prior-turn messages the captain carries into「用户：… / CEO：…」prose for
    its ``history`` context block — the SAME turns fed to the LLM, made legible to the user
    (单一源: what the user sees == what the LLM eats). Empty for a first turn."""
    label = {"user": "用户", "assistant": "CEO", "system": "系统"}
    parts = [
        f"{label.get(m.get('role', ''), m.get('role') or '')}：{m.get('content') or ''}"
        for m in history
        if (m.get("content") or "").strip()
    ]
    return "\n\n".join(parts)


def _build_captain_context_blocks(
    chat_system_prompt: str,
    history: list[dict],
    user_message: str,
) -> list[ContextBlock]:
    """The ordered :class:`ContextBlock` list describing the CEO captain's OPENING context
    (上下文传递可视化, CEO 侧 通道①): its ``system`` prompt (决策②: 桌面按需弹窗对所有人可见 /
    手机恒隐藏, 旧 powerMode/usageDetail 门控已退役), the ``history`` it carries, and this
    turn's ``request``.

    Unlike a worker — whose single user message is *rendered FROM* its blocks — the captain
    is fed a real multi-message chat (system + history + user). So these blocks MIRROR that
    ``messages`` array (one per channel) rather than being the source it's rendered from;
    built from the SAME three inputs ``build_captain_executor`` assembles ``messages`` from,
    they can't drift (用户看到的 == LLM 吃到的). Every fold routes the captain's run_context
    turn-level (``captainContext`` on the chat bubble), never onto a graph node. 通道⑤ (the
    CEO reading workers' products back on resume) is a separate ratchet, not this opening."""
    blocks: list[ContextBlock] = [
        ContextBlock(
            channel="system",
            heading="CEO 系统提示（本回合实际遵循的系统指令）",
            body=chat_system_prompt,
        )
    ]
    history_text = _format_captain_history(history)
    if history_text:
        blocks.append(
            ContextBlock(
                channel="history",
                heading="对话历史（本回合之前的往来）",
                body=history_text,
            )
        )
    blocks.append(ContextBlock(channel="request", heading="原始用户请求", body=user_message))
    return blocks


# Per-block body cap for the run_context EVENT (决策④): the prompt feeds the LLM the FULL
# block, but the journaled/wired copy is head+tail capped so a huge pasted request / task
# can't bloat the journal. Reuses the dep-budget magnitude; the UI shows the capped body +
# a ``truncated`` flag. (Dependency bodies are already budgeted upstream and rarely hit it.)
_CONTEXT_BLOCK_BODY_CAP = DEP_CONTEXT_BUDGET


def _context_block_payloads(blocks: list[ContextBlock]) -> list[dict[str, Any]]:
    """Serialize ContextBlocks to the ``run_context`` wire shape, capping each body to
    :data:`_CONTEXT_BLOCK_BODY_CAP` (head+tail) so the journal stays bounded. ``chars`` is
    the ORIGINAL injected size; ``truncated`` records the budget cap OR this display cap.

    The captain ``system`` block is EXEMPT from the cap: it carries the bounded,
    internally-built CEO system prompt that the desktop「收到的上下文」dialog shows verbatim
    (having folded in the old「提示词」button), so it must stay full-fidelity — it is not the
    unbounded user/dep body 决策④'s cap guards against."""
    payloads: list[dict[str, Any]] = []
    for b in blocks:
        body = b.body
        truncated = b.truncated
        if b.channel != "system" and len(body) > _CONTEXT_BLOCK_BODY_CAP:
            body = truncate_head_tail(body, _CONTEXT_BLOCK_BODY_CAP)
            truncated = True
        payloads.append(
            {
                "channel": b.channel,
                "heading": b.heading,
                "body": body,
                "chars": len(b.body),
                "truncated": truncated,
                "source_role": b.source_role,
                "source_run_id": b.source_run_id,
                "fidelity": b.fidelity,
                "files": list(b.files),
            }
        )
    return payloads


def _upstream_intermediate_persist_hint(spec: RunSpec) -> str:
    """A1: where upstream links may park large intermediates for downstream ``file_read``.

    Playbook-pinned ``artifacts`` win (strict task-book paths). Otherwise free-form teams
    land under ``DRAFTS_DIR`` with a descriptive filename — never workspace-root
    ``findings-<role>.md``. Does not replace playbook pinning; only guides free teams.

    落点是「工作稿」而非 ``research/``：大中间产物正是「AI 干活的过程材料」的定义，
    而 ``research/`` 曾因这类默认指引沦为杂物入口 → [术语表 · 成品归位].
    """
    pinned = [
        p.strip().replace("\\", "/")
        for p in (spec.deliverable.artifacts if spec.deliverable else [])
        if isinstance(p, str) and p.strip()
    ]
    if pinned:
        paths = "、".join(f"`{p}`" for p in pinned)
        return (
            "中间产物怎么交：零散发现直接写进你的文字产出即可（会自动转交下游）；若产物较大、"
            "值得落盘供下游 file_read 取用，就调 file_write 并【严格按任务书路径】落盘"
            f"（{paths}），切勿用空路径或另起工作区根文件名。"
        )
    return (
        "中间产物怎么交：零散发现直接写进你的文字产出即可（会自动转交下游）；若产物较大、"
        "值得落盘供下游 file_read 取用，就调 file_write，落在"
        f" `{DRAFTS_DIR}/` 下【自起描述性文件名】"
        "（勿用工作区根 `findings-<角色>.md`），切勿用空路径。"
    )


def _team_position_block(plan: RunPlan, spec: RunSpec) -> str:
    """The worker's place on the team DAG: its parallel peers and — crucially — where
    its output GOES. Symmetric to :func:`_dep_context_blocks` (which hands a downstream
    node its upstream PRODUCTS): this hands a node its TOPOLOGY.

    Closes the「上游越权写最终交付物」gap: an upstream link sees the team-level
    原始用户请求 ("…保存一份报告…") but, blind to the writer downstream, used to chase the
    final artifact itself (and, lacking a filename, fire empty-path file_write). It now
    learns it is one link that hands off — and, when it does want to PERSIST a large
    intermediate product for the downstream to ``file_read``, A1 tells it either the
    task-book ``artifacts`` path (strict) or ``DRAFTS_DIR`` + a descriptive filename
    (free teams; never workspace-root ``findings-<role>.md``). A TERMINAL node instead
    learns it IS the final author (reinforcing structure ownership, the worker-side L3
    lever). Blank for a solo single worker (no team → the request simply is its whole job).

    Branches on shape, in priority order:
      - has dependents    → upstream link: hands off, "别自己产最终交付物" +
        中间产物落盘起名许可（A1）
      - else has upstream  → terminal node:  "你是终端环，据上游产出最终交付物"
    Parallel-peer awareness (``sibling_summary``, computed by the builder) is prepended
    in every team shape; a node with none (a lone pipeline link) skips that line."""
    roles = {n.run_id: (n.role or n.run_id) for n in plan.nodes}
    dependents = [roles[n.run_id] for n in plan.nodes if spec.run_id in n.depends_on]
    upstream = [roles[d] for d in spec.depends_on if d in roles]

    parts: list[str] = []
    if spec.sibling_summary:
        parts.append(
            "并行队友（正与你同时推进，各管一摊；据此划清职责边界，别与他们重复劳动、"
            "也别留下衔接空缺；若你们都要写文件，各自用不同的文件 / 子目录，避免互相覆盖）：\n"
            + spec.sibling_summary
        )
    if dependents:
        joined = "、".join(dependents)
        parts.append(
            f"你的产出去向：你是这条流水线的【上游一环】，你的产出是交给下游【{joined}】的"
            "【中间输入】，由其整合产出团队的最终交付物。做好你这一环、把发现 / 产物交给下游"
            "即可，【不要自己产出整个最终交付物】（如完整报告 / 最终文件）。"
            f"{_upstream_intermediate_persist_hint(spec)}"
        )
    elif upstream:
        joined = "、".join(upstream)
        parts.append(
            f"你的位置：你是这条流水线的【终端环】。上游【{joined}】的产出已在下方「前置结果」"
            "交给你，你的职责是据此整合、产出团队交给老板的【最终交付物】。"
            "「前置结果」若已列出工作区路径：先 file_read 这些路径再写总稿；"
            "【禁止】把开工做成全仓 glob / grep / 再调研一遍。"
            "路径含糊或列表缺文件时才 glob / file_list 补钉（见身份【找路径】）。"
        )
    if not parts:
        return ""
    return "## 你在团队中的位置\n" + "\n\n".join(parts)


def _context_inject_blocks(
    context_inject: Mapping[str, str] | None,
) -> list[ContextBlock]:
    """Wave3 B: opening blocks for forced artifact summaries (already truncated)."""
    if not context_inject:
        return []
    parts: list[str] = []
    for path, body in context_inject.items():
        text = (body or "").strip()
        if not text:
            continue
        parts.append(f"### `{path}`\n{text}")
    if not parts:
        return []
    return [
        ContextBlock(
            channel="dependency",
            heading="强制注入·骨架/契约摘要（优先用此，勿反复 file_read 同文件）",
            body="\n\n".join(parts),
            fidelity="inject",
            truncated=True,
            files=list(context_inject.keys()),
        )
    ]


async def load_context_inject_files(
    backend: object,
    paths: list[str] | None,
    *,
    per_file_chars: int = CONTEXT_INJECT_CHARS,
) -> dict[str, str]:
    """Best-effort read + head/tail trim of ``context_inject_files`` for worker opening."""
    if not paths:
        return {}
    read = getattr(backend, "read", None)
    if read is None:
        return {}
    out: dict[str, str] = {}
    for raw in paths:
        path = (raw or "").strip()
        if not path or path in out:
            continue
        try:
            content = await read(path)
        except Exception as e:  # noqa: BLE001 — inject is best-effort
            logger.debug("workspace.context_inject_failed", path=path, error=str(e))
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        original = len(content)
        trimmed = truncate_head_tail(content, per_file_chars)
        out[path] = trimmed
        if len(trimmed) < original:
            from agentcore.runtime.context_cap import log_context_capped

            log_context_capped(
                site="context_inject",
                original_chars=original,
                final_chars=len(trimmed),
            )
    return out


def _dep_context_blocks(
    plan: RunPlan, depends_on: list[str], completed: Mapping[str, RunState]
) -> list[ContextBlock]:
    """Render each upstream dependency's product into a ``dependency`` :class:`ContextBlock`,
    carrying its provenance (``source_role`` / ``source_run_id``), the ``fidelity`` chosen,
    a ``truncated`` flag, and the artifact ``files`` it points at — so the UI shows HOW a
    teammate's product was handed down, not just that it was (上下文传递可视化, 通道③).

    Three fidelity policies, in priority order:

    - A dep that WROTE FILES to the workspace (``files_touched`` non-empty) becomes a
      POINTER (``fidelity.pointer_body``): a tight prose digest + the artifact paths to
      ``file_read``. The product is already on disk and reachable, so re-shipping it
      whole through the prompt wastes tokens and risks tail-trimming (递指针不递全文,
      Agent协作模式.md). A pointer does NOT draw on the pass_through budget.
    - ``summarize`` deps (no files) get a tight head+tail digest (``DEP_SUMMARY_CHARS``),
      the large-fan-in token-saving case; no budget draw either.
    - ``pass_through`` PROSE deps (no file to point at — the default, for 分析/检索→写作
      链路 where 金额 / 法条编号 must survive) SHARE one per-worker total budget
      (``DEP_CONTEXT_BUDGET``), water-filled across them (``fidelity.allocate``) so a
      single rich upstream passes through whole while a wide fan-in stays bounded
      instead of multiplying. A dep that still overflows its share is HEAD+TAIL trimmed
      (``fidelity.truncate_head_tail``) so its tail isn't silently dropped.

    Order follows ``depends_on``. COMPLETED deps inject a product. FAILED /
    SKIPPED / CANCELLED / missing deps inject an **absence** annotation (reason
    + role) so lenient fan-in summarizers know which upstreams are missing —
    never consume a non-completed body as an upstream deliverable.
    COMPLETED + 空 content 但 debrief.summary 在 → 升格简报为 body，不当前置缺席。"""
    # mode ∈ {"pointer", "summarize", "pass_through"}
    # (dep_id, label, clean_content, files, mode, author_summary, promoted_from_brief)
    from agentcore.runtime.runs.research_quality import promote_brief_to_deliverable

    deps: list[tuple[str, str, str, list[str], str, str, bool]] = []
    absence_blocks: list[ContextBlock] = []
    for dep_id in depends_on:
        state = completed.get(dep_id)
        dep_spec = plan.by_id(dep_id)
        label = dep_spec.role if dep_spec and dep_spec.role else dep_id
        author_summary = ""
        key_points: object = None
        if state and state.debrief:
            author_summary = str((state.debrief or {}).get("summary") or "").strip()
            key_points = (state.debrief or {}).get("key_points")
        has_brief = bool(author_summary)
        if (
            not state
            or state.phase is not RunPhase.COMPLETED
            or (not state.content and not state.files_touched and not has_brief)
        ):
            absence_blocks.append(
                ContextBlock(
                    channel="dependency",
                    heading=f"前置缺席（来自 {label}）",
                    body=_format_upstream_absence(dep_id, label, state),
                    source_role=label,
                    source_run_id=dep_id,
                    fidelity="absent",
                    truncated=False,
                    files=[],
                )
            )
            continue
        # 完工交接简报: the content is already the pure deliverable (the brief rides the run's
        # structured ``debrief``, submitted via the handoff tool — never appended to the prose), so
        # the body sizes on the deliverable alone and the author's own 结论 can LEAD the block.
        # 同轮 0 字仅简报：升格为下游可读 body；升格路径不再 prepend 同一句结论。
        clean = state.content or ""
        promoted_from_brief = False
        if not clean.strip() and has_brief:
            clean = promote_brief_to_deliverable(author_summary, key_points)
            promoted_from_brief = True
        if state.files_touched:
            mode = "pointer"
        elif dep_spec and dep_spec.policy.result_handling == "summarize":
            mode = "summarize"
        else:
            mode = "pass_through"
        deps.append(
            (
                dep_id,
                label,
                clean,
                list(state.files_touched),
                mode,
                author_summary,
                promoted_from_brief,
            )
        )

    # Only PROSE pass_through deps draw on the shared budget; pointer / summarize deps
    # are already compact and sized independently.
    allowances = iter(
        allocate(
            [len(c) for (_, _, c, _, m, _, _) in deps if m == "pass_through"],
            DEP_CONTEXT_BUDGET,
        )
    )
    blocks: list[ContextBlock] = list(absence_blocks)
    for dep_id, label, content, files, mode, author_summary, promoted_from_brief in deps:
        if mode == "pointer":
            body = pointer_body(content, files)
            # full product is on disk (递指针); body is a digest, not a budget trim.
            truncated = False
        elif mode == "summarize":
            # The author's own 结论 beats a mechanical head-chop (作者最知道该留什么): use it as the
            # digest when present, else fall back to the blind summarize.
            if author_summary:
                body = author_summary
                truncated = len(content) > len(author_summary)
            else:
                # No authored 结论 → HEAD+TAIL digest (not head-only): keep the deliverable's
                # opening AND its tail (结论/取舍 often land last) instead of dropping the tail.
                body = truncate_head_tail(content, DEP_SUMMARY_CHARS)
                truncated = len(content) > DEP_SUMMARY_CHARS
                if truncated:
                    from agentcore.runtime.context_cap import log_context_capped

                    log_context_capped(
                        site="dep_context",
                        original_chars=len(content),
                        final_chars=len(body),
                        fidelity=mode,
                    )
        else:
            allowance = next(allowances)
            body = truncate_head_tail(content, allowance)
            truncated = len(content) > allowance
            if truncated:
                from agentcore.runtime.context_cap import log_context_capped

                log_context_capped(
                    site="dep_context",
                    original_chars=len(content),
                    final_chars=len(body),
                    fidelity=mode,
                )
        # Let the downstream see the upstream author's own 结论 FIRST — cheapest to read and the
        # one line that should survive even when the body below is budget-trimmed. Skip when
        # summarize already IS that line, or when the body was promoted from the same brief
        # (empty content → promote_brief_to_deliverable already starts with summary).
        if author_summary and mode != "summarize" and not promoted_from_brief:
            body = f"【上游交接结论】{author_summary}\n\n{body}"
        blocks.append(
            ContextBlock(
                channel="dependency",
                heading=f"前置结果（来自 {label}）",
                body=body,
                source_role=label,
                source_run_id=dep_id,
                fidelity=mode,
                truncated=truncated,
                files=files,
            )
        )
    return blocks


async def _safe_index_files(backend: object) -> list[str]:
    """Best-effort flat file index of the shared workspace, for the worker manifest.

    Wraps ``backend.index_files`` so a listing failure (a dropped desktop in local
    mode, an I/O hiccup) degrades the manifest to teammate products instead of failing
    the run — workspace awareness is an enhancement, never a hard dependency. Returns
    the paths (dropping the truncation flag — the manifest caps independently).
    """
    index = getattr(backend, "index_files", None)
    if index is None:
        return []
    try:
        # newest-first: in a big workspace the manifest's budget should spend on the
        # most-recently-touched files (uploads / latest outputs), not whatever sorts
        # alphabetically first.
        paths, _truncated = await index(order="recent")
        return list(paths)
    except Exception as e:  # noqa: BLE001 — manifest is best-effort, never fail a run
        logger.debug("workspace.index_failed", error=str(e))
        return []


async def _load_artifact_contents(
    backend: object,
    patterns: list[str],
    workspace_paths: list[str],
) -> dict[str, str]:
    """Best-effort read of workspace texts matching artifact patterns (JSON file gate).

    Used when ``output_format=json`` + ``artifacts`` so the contract can verify
    parseability of landed files. Missing / unreadable paths are omitted; the
    contract reports unread failures when no readable match parses.
    """
    from agentcore.runtime.runs.contract import matching_artifact_paths

    read = getattr(backend, "read", None)
    if read is None:
        return {}
    out: dict[str, str] = {}
    for pattern in patterns:
        for path in matching_artifact_paths(pattern, workspace_paths):
            if path in out:
                continue
            try:
                out[path] = await read(path)
            except Exception as e:  # noqa: BLE001 — contents are best-effort
                logger.debug("workspace.artifact_read_failed", path=path, error=str(e))
    return out
