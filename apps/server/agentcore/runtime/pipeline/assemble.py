"""Fresh-turn Phase 2: approval gate, CEO toolset, chat system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentcore.config import settings
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes
from agentcore.llm.profiles import TurnProfiles
from agentcore.runtime.approvals import ApprovalGate
from agentcore.runtime.context import (
    ContextAssembler,
    SectionOrder,
    build_workspace_overview,
    resolve_channel_profile,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore, format_registered_sources_prompt
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.resolve.prompt import (
    attachment_material_scene,
    compose_ceo_chat_prompt,
)
from agentcore.runtime.sessions import SessionLoader, SessionSaver, default_session_registry
from agentcore.runtime.suspension import SuspensionDeleter, SuspensionSaver
from agentcore.tools.builtin import (
    approval_class_tool_names,
    delegation_grantable_tool_names,
    per_call_tool_names,
)
from agentcore.tools.registration import host_class_tool_names, register_board_ceo_tools
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.protocol import WorkspaceBackend

from .prepare import PreparedTurn, _timed_phase


@dataclass
class AssembledTurn:
    """Phase-2 outputs: wired CEO tools + assembled chat prompt."""

    approval_gate: ApprovalGate | None
    permission_axes: PermissionAxes
    delegate_tool: Any
    debate_tool: Any
    chat_tools: ToolRegistry
    chat_system_prompt: str
    # Assemble-time cues for settle-side 阶梯 2 count (no intercept).
    had_prior_delivery_gaps: bool = False
    had_recent_team_graph: bool = False


def build_chat_system_prompt(
    *,
    ceo_prompt: str,
    working_set: str,
    recent_team_graph: str,
    prior_delivery_gaps: str,
    prior_delegate_retry: str,
    attachment_context: str,
    registered_sources: str,
    soft_cap: int | None,
) -> str:
    """Render the turn's CEO system prompt from its sections — the ONE assembly point.

    Variable tail AFTER the stable hint stack (working set + recent graph +
    attachments + 来源台账) so the CEO prefix (base + hints, including ``<工作区>``
    with the CEO file index already spliced) stays byte-identical across turns
    except when those facts themselves change. Empty sections are dropped, so a
    turn with none is byte-identical to the bare CEO prompt.

    EVERY section the CEO's system prompt carries must come in through here. A fragment
    appended to the returned string instead lands outside ``assembly_hash`` /
    ``total_chars`` / ``section_digests``, so the prefix-drift signal and the soft cap
    silently under-report by that whole block — and the fragments tempting enough to
    append late (``registered_sources``: hydrated from the whole conversation) are
    precisely the ones that grow every turn (CTX-A3).

    COST-004 (仅观测起步): ``observe`` logs per-section chars + whether the soft cap is
    exceeded, 攒据用、零行为改动。此处是「易变尾」与稳定前缀 (ceo_prompt) 同框的 choke
    point, 正是未来「仅裁易变尾」软闸的作用点 (项目审计-成本性能专项 §九)。
    """
    return (
        ContextAssembler()
        .add("ceo_prompt", ceo_prompt, SectionOrder.BASE)
        .add("working_set", working_set, SectionOrder.WORKING_SET)
        .add("recent_team_graph", recent_team_graph, SectionOrder.RECENT_TEAM_GRAPH)
        .add("prior_delivery_gaps", prior_delivery_gaps, SectionOrder.PRIOR_DELIVERY_GAPS)
        .add("prior_delegate_retry", prior_delegate_retry, SectionOrder.PRIOR_DELEGATE_RETRY)
        .add("attachment_context", attachment_context, SectionOrder.ATTACHMENT)
        .add("registered_sources", registered_sources, SectionOrder.REGISTERED_SOURCES)
        .observe(scope="ceo_turn", soft_cap=soft_cap)
        .render()
    )


async def assemble_ceo_turn(
    *,
    prepared: PreparedTurn,
    conversation_id: str,
    user_message: str,
    history: list[dict],
    evidence_ledger: EvidenceLedgerCore | None = None,
    sink: EventSink,
    backend: WorkspaceBackend,
    folder_id: str | None,
    approvals_enabled: bool,
    permission_axes: PermissionAxes | None,
    profiles: TurnProfiles,
    captain_run_id: str,
    message_id: str,
    session_saver: SessionSaver | None,
    session_loader: SessionLoader | None,
    suspension_saver: SuspensionSaver | None,
    suspension_deleter: SuspensionDeleter | None,
    x_client_platform: str | None,
) -> AssembledTurn:
    """Assemble the CEO coordinator toolset and the turn's chat system prompt."""
    # The CEO owns the conversation and replies directly, but it is a
    # COORDINATOR: it carries only the read / retrieval built-ins
    # (``build_ceo_tool_registry`` — web_search/read_url/file_read/file_list/
    # grep) plus the on-demand orchestration primitive ``delegate``. It holds
    # NONE of the production / mutation tools (file_write/str_replace/
    # file_delete/file_move/code_execute); any work that produces or changes an
    # artifact is handed to a worker. There is no mandatory pre-turn
    # orchestrator pass — the CEO itself decides when/at what granularity to
    # delegate. ``delegate`` is NON-terminal: workers' products return to the
    # CEO's own ReAct loop, which writes a short user-facing overview in its own
    # voice (D3 / 决策①: per-worker detail is shown separately in the UI).
    # Workers get the full production ``worker_tools`` plus, for any worker with
    # ``depth < MAX_DELEGATION_DEPTH``, ``delegate``+``replan`` (delegation is on by
    # default within the depth cap — worker captains may nest; workers at the cap
    # are leaves; per-captain fan-out capped at ``MAX_WORKER_SUBDELEGATIONS``).
    # Approval gate (one per turn so an "allow for the rest of this turn" grant
    # is scoped to this message and does not leak across turns). It is wired into
    # the CEO's loop, but with the coordinator boundary the CEO holds no
    # GRANTABLE tools — so approvals now bite at the WORKER layer: the SAME
    # instance is handed to the delegate tool and forwarded to every worker
    # (双模式工作区 P2d 执行门), so a delegated worker can't run code / mutate files
    # on the user's real machine without consent. Which calls raise a card is
    # narrowed per call in tool_exec (a cloud team stays un-gated for
    # server-sandbox tools) — never by withholding the gate object here.
    # ``None`` = this turn has nobody to ask (ops kill switch / unattended job).
    if permission_axes is None:
        permission_axes = DEFAULT_PERMISSION_AXES
    approval_gate = (
        ApprovalGate(
            sink=sink,
            conversation_id=conversation_id,
            registry=default_interaction_registry(),
            timeout_seconds=settings.approval_timeout_seconds,
            timeout_overrides=settings.approval_timeout_overrides,
            file_op_tools=approval_class_tool_names(),
            per_call_tools=per_call_tool_names(),
            delegation_grantable_tools=delegation_grantable_tool_names(),
            host_class_tools=host_class_tool_names(),
            permission_axes=permission_axes,
        )
        if (settings.approval_gate_enabled and approvals_enabled)
        else None
    )
    # The conversation's live roster (留人, 乙 热修): delegate registers each
    # COMPLETED worker here as a recoverable RunSession, and revise recalls one to
    # continue on its own draft. Conversation-scoped (P2) — fetched from the
    # process-wide registry so it SURVIVES across turns ("改下刚才那个" works next
    # turn); bounded by TTL + count + byte caps, idle conversations reaped. An
    # expiry / miss falls back to 甲 (re-delegate). Cross-process persistence: P3.
    session_store = default_session_registry().get_or_create(conversation_id)
    # Structured DAG checkpoints (结构化挂起 2a) share the SAME gate as ask_user
    # (a live interactive user): an autonomous handoff job has no client to
    # answer, so a checkpoint there would only ever time out. Computed here —
    # before the delegate tool — because delegate consumes it too (it suspends
    # the WaveScheduler at a wave boundary when a step is marked checkpoint_after).
    checkpoint_enabled = settings.checkpoint_gate_enabled and approvals_enabled
    # The delegate tool gets the worker base prompt — the CLEAN base (no CEO chat
    # hints, reused verbatim by workers in runs/executor/ — they must not be told
    # about a delegate tool they do not hold) plus this turn's attachment block.
    # message_id + the suspension closures arm durable plan_review pauses (结构化
    # 挂起 2b) on the top-level delegate.
    # Look up via ``pipeline.run`` so governance tests can monkeypatch the seam.
    from agentcore.runtime.pipeline import run as run_mod

    # ChannelProfile is orthogonal to workspace location (no local lift).
    channel = resolve_channel_profile(x_client_platform)
    delegate_tool, debate_tool, chat_tools = run_mod._assemble_ceo_toolset(
        llm=prepared.llm,
        sink=sink,
        base_system_prompt=prepared.worker_base_prompt,
        user_message=user_message,
        history=history,
        worker_tools=prepared.worker_tools,
        base_tool_context=prepared.base_tool_context,
        profiles=profiles,
        approval_gate=approval_gate,
        session_store=session_store,
        session_saver=session_saver,
        session_loader=session_loader,
        conversation_id=conversation_id,
        captain_run_id=captain_run_id,
        checkpoint_enabled=checkpoint_enabled,
        message_id=message_id,
        suspension_saver=suspension_saver,
        suspension_deleter=suspension_deleter,
        backend_location=backend.location,
        skill_registry=prepared.skill_registry,
        folder_id=folder_id,
        permission_axes=permission_axes,
        # Same live-user gate as ask_user itself, plus desktop-only: web/mobile omit.
        advertise_bind_local_folder=checkpoint_enabled and channel.can_bind_folder,
        desktop_online=channel.desktop_online,
    )
    from agentcore.runtime.resolve.prepare import _wire_conversation_log_tools
    from agentcore.tools.ceo_toolset import wire_ceo_consult
    from agentcore.tools.mcp import register_mcp_tools
    from agentcore.tools.on_demand import offer_tools_from_window

    register_mcp_tools(chat_tools, prepared.mcp_discover)
    _wire_conversation_log_tools(chat_tools, folder_id=folder_id)

    await wire_ceo_consult(
        chat_tools,
        skill_registry=prepared.skill_registry,
        folder_id=folder_id,
        user_id=prepared.base_tool_context.user_id,
    )
    offer_tools_from_window(chat_tools, history)
    offer_tools_from_window(prepared.worker_tools, history)

    # AI 协作白板: in a 白板会话, hand the CEO the board tools so it can draw on
    # (``board_ops``, §六 M2) and read (``board_read``, §九) the user's open canvas.
    # Registered AFTER the coordinator toolset is assembled and BEFORE ``ceo_tool_names``
    # is read, so they join the LLM's function catalog this turn. Only here (board-bound
    # runs) — every other chat never sees them.
    if prepared.board_channel is not None:
        register_board_ceo_tools(chat_tools)

    # The entry chat agent gets the SLIM CEO core + the unified ``<按需目录>``.
    # Advanced HOW detail is pulled via ``consult``.
    ceo_tool_names = {schema.name for schema in chat_tools.list_all()}
    ceo_offered_names = set(chat_tools.offered_names)
    on_demand_entries: list = []
    consult_tool = chat_tools.get_optional("consult")
    if consult_tool is not None and getattr(consult_tool, "source", None) is not None:
        on_demand_entries = list(
            await consult_tool.source.list_directory(prepared.base_tool_context.user_id)
        )
    explore_reason: str | None = None
    folder_nav_stale = False
    folder_profile_empty_soft = False
    if folder_id:

        async def _run_explore_gates() -> tuple[str | None, bool, bool]:
            from agentcore.conversation.scratch import resolve_conversation_local_binding
            from agentcore.memory.explore_profile import (
                compute_workspace_explore_fingerprint,
                evaluate_explore_fingerprint_drift,
                folder_profile_explore_reason,
                resolve_folder_workspace_key,
                resolve_hard_explore_reason,
            )

            reason: str | None = None
            nav_stale = False
            profile_empty_soft = False
            mem_store = run_mod.default_memory_store()
            ctx = prepared.base_tool_context
            injected_binding = None
            if ctx.folder_binding_injected:
                injected_binding = resolve_conversation_local_binding(
                    local_root_id=ctx.folder_local_root_id,
                    local_subpath=ctx.folder_local_subpath,
                )
            # Injected → pure key (no PG). Else DB only for UUID-shaped folder_id;
            # non-UUID memory scope → folder:<id>; connectivity/DataError → None.
            # Unknown key: still run empty / named gates; skip rebind ("" sentinel).
            current_key = await resolve_folder_workspace_key(
                folder_id,
                binding=injected_binding,
                binding_injected=ctx.folder_binding_injected,
            )
            key_for_gates = current_key if current_key is not None else ""
            reason = await folder_profile_explore_reason(
                mem_store,
                prepared.base_tool_context.user_id,
                folder_id,
                current_workspace_key=key_for_gates,
            )
            reason, profile_empty_soft = resolve_hard_explore_reason(
                reason,
                user_message,
            )
            # R2 soft hint + R1 background refresh: fingerprint drift never blocks.
            # Soft-empty 画像 is not "go fill it": skip stale hint + silent refresh.
            # Named 先了解 / 工程短语 already took the hard path above.
            if not reason and not profile_empty_soft:
                live_fp = await compute_workspace_explore_fingerprint(backend)
                nav_stale = await evaluate_explore_fingerprint_drift(
                    mem_store,
                    prepared.base_tool_context.user_id,
                    folder_id,
                    live_fingerprint=live_fp,
                    current_workspace_key=current_key,
                )
                if nav_stale and current_key:
                    from agentcore.memory.explore_refresh import (
                        build_workspace_explore_snapshot,
                        schedule_explore_refresh,
                    )

                    snapshot = await build_workspace_explore_snapshot(backend)
                    schedule_explore_refresh(
                        user_id=prepared.base_tool_context.user_id,
                        folder_id=folder_id,
                        workspace_key=current_key,
                        snapshot=snapshot,
                        live_fingerprint=live_fp,
                    )
            # Precompute close-out key so update_folder_profile does not re-hit PG.
            if current_key:
                upd = chat_tools.get_optional("update_folder_profile")
                if upd is not None and getattr(upd, "workspace_key", None) is None:
                    upd.workspace_key = current_key
            return reason, nav_stale, profile_empty_soft

        explore_reason, folder_nav_stale, folder_profile_empty_soft = await _timed_phase(
            "explore", _run_explore_gates()
        )
    # Sink explore-pending into ToolContext so delegate can suppress structured
    # files_written inference / require ≥2 explore workers（prompt 块 delegate 读不到）。
    # Worker write_scope=explore_memory：写工具层拦出 AgentCore/ 之外的写盘。
    # Cleared in-place by update_folder_profile on successful write.
    if explore_reason:
        prepared.base_tool_context.cold_start_explore_pending = True
        prepared.base_tool_context.write_scope = "explore_memory"
    # CEO file index: untagged body spliced into ``<工作区>`` (not a second XML tag).
    # Workers never receive this listing. Generated fresh each turn; "" omits the 文件节.
    workspace_overview = await _timed_phase(
        "workspace_overview",
        build_workspace_overview(backend, shared_workspace=folder_id is not None),
    )
    chat_system_prompt = compose_ceo_chat_prompt(
        prepared.system_prompt,
        skill_registry=prepared.skill_registry,
        ceo_tool_names=ceo_tool_names,
        ceo_offered_names=ceo_offered_names,
        on_demand_entries=on_demand_entries,
        workspace_context=prepared.workspace_facts,
        workspace_file_index=workspace_overview,
        cold_start_explore=explore_reason or False,
        folder_nav_stale=folder_nav_stale,
        folder_profile_empty_soft=folder_profile_empty_soft,
        attachment_material=attachment_material_scene(prepared.attachment_context),
    )
    # 跨回合同图追加的回显通道 (CEO-only): history replays no tool I/O, so the newest
    # appendable graph's execution_id must ride the prompt to stay visible next turn.
    # "" when the conversation has no team graph yet (section drops out).
    from agentcore.runtime.delegate.graph_append import build_recent_graph_context

    recent_graph_context = await _timed_phase(
        "recent_graph",
        build_recent_graph_context(
            conversation_id=conversation_id,
            exclude_message_id=message_id,
        ),
    )
    # 跨回合交付账本 one-shot：上轮 journal delivery_status partial/blocked + blocking
    # gaps → 易变尾 `<上轮交付缺口>`（不 emit / 不 stamp verdict；不扫用户原文）。
    from agentcore.runtime.delegate.prior_delivery_gaps import (
        apply_gaps_vs_redispatch_mutex,
        build_prior_delivery_gaps_hint,
    )
    from agentcore.runtime.delegate.redispatch_hint import (
        build_prior_failure_redispatch_hint,
    )

    prior_delivery_gaps = await build_prior_delivery_gaps_hint(
        conversation_id=conversation_id,
        exclude_message_id=message_id,
    )
    # 跨轮空委派/无产出：上轮 journal 结构化指纹 → 一次性再派软提示（不扫用户「继续」原文）。
    # 同回合若 gaps 软块非空 → 抑制 redispatch（缺口优先；跳过再查 journal）。
    prior_delegate_retry_raw = (
        ""
        if prior_delivery_gaps
        else await build_prior_failure_redispatch_hint(
            conversation_id=conversation_id,
            exclude_message_id=message_id,
        )
    )
    prior_delivery_gaps, prior_delegate_retry = apply_gaps_vs_redispatch_mutex(
        prior_delivery_gaps,
        prior_delegate_retry_raw,
    )
    # 跨回合文件工作集：历史不回放工具 I/O，journal 抽出仍在场的 path（指针、不回灌正文）。
    from agentcore.runtime.context.working_set import build_working_set_block

    working_set = await build_working_set_block(
        conversation_id=conversation_id,
        exclude_turn_id=message_id,
    )
    # 可用性诚实性 · 甲：偏窄短问 → 复用最近 delivery_status 发卡到本回合答复面。
    from agentcore.runtime.delegate.delivery_status import (
        maybe_reinject_recent_delivery_for_availability_ask,
    )

    await maybe_reinject_recent_delivery_for_availability_ask(
        sink,
        conversation_id=conversation_id,
        user_message=user_message,
        exclude_turn_id=message_id,
        promotion_ledger=prepared.base_tool_context.promotion_ledger,
    )
    # 出处诚实：hydrate 后注入「已登记来源」结构化摘要（对照台账字段，禁占位叙事）。
    # Tools register into the ledger only once the loop runs, so its content is settled
    # for this turn's prompt here.
    chat_system_prompt = build_chat_system_prompt(
        ceo_prompt=chat_system_prompt,
        working_set=working_set,
        recent_team_graph=recent_graph_context,
        prior_delivery_gaps=prior_delivery_gaps,
        prior_delegate_retry=prior_delegate_retry,
        attachment_context=prepared.attachment_context,
        registered_sources=format_registered_sources_prompt(evidence_ledger),
        soft_cap=settings.prompt_budget_char_soft_cap,
    )

    # COST-004 tools 面: 补工具 schema JSON chars / 约算 token（原先只观测系统提示，编排工具
    # ~10k 字符盲区）。纯 structlog，不改 SSE / API 契约。
    from agentcore.runtime.resolve.ceo_surface import observe_tools_offered

    observe_tools_offered(chat_tools, scope="ceo_turn")

    return AssembledTurn(
        approval_gate=approval_gate,
        permission_axes=permission_axes,
        delegate_tool=delegate_tool,
        debate_tool=debate_tool,
        chat_tools=chat_tools,
        chat_system_prompt=chat_system_prompt,
        had_prior_delivery_gaps=bool((prior_delivery_gaps or "").strip()),
        had_recent_team_graph=bool((recent_graph_context or "").strip()),
    )
