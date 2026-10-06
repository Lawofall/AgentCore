"""Fresh-turn Phase 1: memory, prompts, channels, LLM, base tool context."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass

import agentcore.runtime.pipeline as pipeline_pkg
from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.core.types import new_id
from agentcore.db.base import async_session_factory
from agentcore.db.repositories import FolderRepository
from agentcore.desktop.channel import DesktopClientChannel
from agentcore.folders.desk import caller_is_desk_member, resolve_folder_owner_user_id
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import TurnProfiles
from agentcore.memory import load_turn_rule_view
from agentcore.runtime.context import (
    build_workspace_context,
    detect_workspace_git,
    resolve_channel_profile,
)
from agentcore.runtime.costing import RunCost
from agentcore.runtime.events import EventSink
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.runtime.resolve.prepare import (
    _build_attachment_prompt,
    _wire_conversation_log_tools,
    merge_attachment_and_mention_context,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_worker_base_prompt,
    render_worker_turn_envelope,
)
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_worker_registry
from agentcore.tools.ceo_toolset import wire_worker_consult
from agentcore.tools.mcp.wire import McpDiscoverResult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.workspace.cloud_tree import normalize_rel_path
from agentcore.workspace.locate import (
    workspace_channel_for_tools,
)
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


async def list_consult_entries(registry: ToolRegistry, user_id: str) -> list:
    """``<按需目录>`` rows for this registry's consult source. Empty if unwired."""
    consult = registry.get_optional("consult")
    source = getattr(consult, "source", None) if consult is not None else None
    if source is None:
        return []
    return list(await source.list_directory(user_id))


async def _probe_local_workspace(
    backend: WorkspaceBackend, attachments: list[dict] | None
) -> tuple[object, object, bool, object]:
    """Git / empty-desk / exec-language probes share one local-IO budget; run together."""
    from agentcore.runtime.pipeline.errors import (
        await_prepare_local_io,
        prepare_local_io_span,
    )
    from agentcore.tools.sandbox.exec_languages import resolve_exec_languages
    from agentcore.workspace.desk_empty import desk_is_visibly_empty
    from agentcore.workspace.sparse_listing import collect_turn_material_paths

    material_paths = collect_turn_material_paths(attachments)
    backend.ai_list_materials = material_paths
    with prepare_local_io_span(backend):
        exec_languages, git_fact, desk_visibly_empty = await _gather_cancel_on_fail(
            _timed_phase(
                "exec_languages",
                await_prepare_local_io(resolve_exec_languages(backend)),
            ),
            _timed_phase("git", await_prepare_local_io(detect_workspace_git(backend))),
            _timed_phase(
                "desk_empty",
                await_prepare_local_io(desk_is_visibly_empty(backend)),
            ),
        )
    return exec_languages, git_fact, desk_visibly_empty, material_paths


async def _resolve_table_context(
    *,
    table_id: str | None,
    table_selection: list[str] | None,
    user_id: str,
    conversation_id: str,
    folder_id: str | None,
    attachments: list[dict] | None,
) -> tuple[str | None, str]:
    if table_id is None:
        from agentcore.table.bind import lookup_table_id_for_turn

        try:
            table_id = await lookup_table_id_for_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                folder_id=folder_id,
                attachments=attachments,
            )
        except Exception:
            table_id = None
    table_context = ""
    if table_id:
        from agentcore.table.context import render_table_context, sanitize_table_selection

        try:
            table_context = await render_table_context(
                table_id=table_id,
                user_id=user_id,
                selected_ids=sanitize_table_selection(table_selection),
            )
        except Exception:
            table_context = ""
    return table_id, table_context


async def _timed_phase[T](phase: str, awaitable: Awaitable[T]) -> T:
    """Await ``awaitable`` and emit one ``chat.prepare_phase`` line (phase + ms)."""
    started = time.monotonic()
    try:
        return await awaitable
    finally:
        logger.info(
            "chat.prepare_phase",
            phase=phase,
            ms=int((time.monotonic() - started) * 1000),
        )


async def _gather_cancel_on_fail(*aws: Awaitable) -> tuple:
    """Like ``asyncio.gather`` but cancel siblings when one fails (no leaked probes)."""
    tasks = [
        aw if isinstance(aw, asyncio.Task) else asyncio.create_task(aw)
        for aw in aws
    ]
    try:
        return tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def resolve_desk_folder_label(
    user_id: str, folder_id: str | None
) -> str | None:
    """Sitting-desk path/name for ``<工作区>``; ``None`` on miss or failure.

    ``rel_path`` wins when present, otherwise ``name``. Never raises into prepare.
    """
    fid = (folder_id or "").strip()
    if not fid:
        return None
    try:
        from agentcore.folders.credentials import (
            FoldersCloudError,
            cloud_get_folder,
            get_folders_credentials,
        )

        creds = get_folders_credentials()
        if creds is not None:
            try:
                summary = await cloud_get_folder(creds, folder_id=fid)
            except FoldersCloudError as e:
                logger.warning(
                    "desk_folder_label.cloud_failed",
                    user_id=user_id,
                    folder_id=fid,
                    error=str(e),
                    code=e.code,
                )
                return None
            if summary is None:
                return None
            rel = summary.get("rel_path")
            name = summary.get("name")
            label = (
                normalize_rel_path(rel)
                if isinstance(rel, str) and rel.strip()
                else None
            )
            if not label and isinstance(name, str):
                label = name.strip() or None
            return label
        from agentcore.db.sidecar_tickets import sidecar_narrow_tickets_bound

        if sidecar_narrow_tickets_bound():
            return None
        async with async_session_factory() as session:
            folder = await FolderRepository(session).get_by_id(fid, user_id=user_id)
    except Exception as e:  # noqa: BLE001 - label miss must never break a turn
        logger.warning(
            "desk_folder_label.load_failed",
            user_id=user_id,
            folder_id=fid,
            error=str(e),
        )
        return None
    if folder is None:
        return None
    label = normalize_rel_path(folder.rel_path) or (folder.name or "").strip()
    return label or None


@dataclass
class PreparedTurn:
    """Phase-1 outputs shared by assemble + execute."""

    llm: object
    system_prompt: str
    workspace_facts: str
    worker_base_prompt: str
    worker_envelope: str
    worker_tools: ToolRegistry
    skill_registry: object
    table_context: str
    base_tool_context: ToolContext
    vision_cost_sink: list[RunCost]
    attachment_context: str
    user_message: str
    native_image_parts: list[dict]
    bound_execution_id: str
    execution_id_token: object
    mcp_discover: McpDiscoverResult
    member_turn: bool


async def prepare_fresh_turn(
    *,
    conversation_id: str,
    user_id: str,
    backend: WorkspaceBackend,
    sink: EventSink,
    folder_id: str | None,
    table_id: str | None = None,
    table_selection: list[str] | None = None,
    attachments: list[dict] | None,
    permission_axes,
    llm_credentials: LLMCredentials | None,
    x_client_platform: str | None,
    profiles: TurnProfiles | None = None,
    agent_mentions: list[dict] | None = None,
    folder_binding_injected: bool = False,
    folder_local_root_id: str | None = None,
    folder_local_subpath: str | None = None,
    user_message: str = "",
) -> PreparedTurn:
    """Build the stable base prompt, worker tools, channels, and tool context."""
    # User always-rules inject into ``<设定>``. AI-maintained notes are not injected.
    # Look up via ``pipeline.run`` so governance tests can monkeypatch the seam
    # (``test_pipeline_governance._patch_pipeline``).
    from agentcore.runtime.pipeline import run as run_mod

    memory_store = run_mod.default_memory_store()
    desk_owner_id, member_turn = await _gather_cancel_on_fail(
        resolve_folder_owner_user_id(folder_id),
        caller_is_desk_member(user_id=user_id, folder_id=folder_id),
    )
    folder_rules_user_id = desk_owner_id or user_id
    # Member turns still inject the owner's folder-layer 规则; account-level stays private.
    # Clean shared base (constitution + always-on ``<设定>``): NO date, NO
    # workspace facts, NO attachments, NO CEO hints. Cacheable prefix shared by
    # the CEO and workers. Date / workspace / attachments ride ``[系统提示]``
    # envelopes (opening user), not ``role: system``.
    # Host / MCP backfill needs a desktop client — orthogonal to workspace location.
    # Member turns: same client header, but no desktop fulfill (协作桌 · 否决本地共享).
    channel = resolve_channel_profile(x_client_platform).for_turn(
        member_turn=member_turn
    )
    desktop_online = channel.desktop_online
    # Presence gate + prepare local IO budget. The span adopts turn_runner's
    # turn-wide deadline (baseline already spent part of it) and starts its own
    # when prepare is invoked alone, e.g. tests / stage-card / workflow entries.
    from agentcore.runtime.pipeline.errors import (
        raise_if_local_workspace_fulfiller_absent,
    )

    raise_if_local_workspace_fulfiller_absent(user_id=user_id, backend=backend)
    auto_desk_folder_id: str | None = None
    if folder_id is None:
        from agentcore.runtime.delegate.target_desktop import adopt_persisted_auto_desk

        adopted = await adopt_persisted_auto_desk(
            birth_folder_id=folder_id,
            user_id=user_id,
            conversation_id=conversation_id,
            birth_backend=backend,
            sink=sink,
        )
        if adopted is not None:
            backend = adopted.backend
            auto_desk_folder_id = adopted.folder_id
    sitting_folder_id = folder_id or auto_desk_folder_id
    # Desktop channel early: cache-only MCP discovery uses the same ClientTool sink.
    desktop_channel = (
        DesktopClientChannel(
            user_id=user_id,
            conversation_id=conversation_id,
            registry=default_interaction_registry(),
            timeout_seconds=settings.board_op_timeout_seconds,
        )
        if desktop_online
        else None
    )
    from agentcore.llm.credentials import bind_credential_pricing_context
    from agentcore.tools.mcp import discover_mcp_tools, register_mcp_tools
    from agentcore.tools.sandbox.desk_provision import provision_server_desk

    # Call-level pricing + optional user unit card (同路贯穿 calculate_cost).
    # Bind before the gather so the LLM-router task inherits the context.
    bind_credential_pricing_context(llm_credentials)
    # Independent IO: rules / desk name / LLM router / cache-only MCP / local
    # probes / cloud guest boot. Presence + auto-desk already settled the sitting root.
    # Outside the local-IO span: cloud guest boot must not spend the 20s local
    # presence budget. Chat-path ``run`` never waits this. Air bubble uses
    # ``desk_provision_wait`` (preparing-cloud), not empty Thinking…
    (
        rule_view,
        desk_folder_label,
        llm,
        mcp_discover,
        local_probe,
        _,
    ) = await _gather_cancel_on_fail(
        _timed_phase(
            "rules",
            load_turn_rule_view(
                memory_store,
                user_id,
                folder_id=folder_id,
                folder_user_id=folder_rules_user_id,
            ),
        ),
        _timed_phase(
            "desk_folder_label",
            resolve_desk_folder_label(folder_rules_user_id, sitting_folder_id),
        ),
        _timed_phase(
            "llm",
            pipeline_pkg.build_turn_router(
                llm_credentials, user_id=user_id, profiles=profiles
            ),
        ),
        _timed_phase(
            "mcp",
            discover_mcp_tools(desktop_channel, cache_scope=user_id, cache_only=True),
        ),
        _probe_local_workspace(backend, attachments),
        _timed_phase(
            "cloud_desk",
            provision_server_desk(
                backend, conversation_id=conversation_id, sink=sink
            ),
        ),
    )
    exec_languages, git_fact, desk_visibly_empty, material_paths = local_probe
    workspace_facts = build_workspace_context(
        backend,
        desktop_online=desktop_online,
        exec_languages=exec_languages,
        permission_axes=permission_axes,
        git_fact=git_fact,
        desk_folder_id=sitting_folder_id,
        desk_folder_label=desk_folder_label,
        desk_is_birth=folder_id is not None,
        desk_visibly_empty=desk_visibly_empty,
    )
    system_prompt = assemble_system_prompt(
        rules_markdown=rule_view.settings,
        path_index=rule_view.path_index,
    )
    # Resolve whether this turn's main model can take image parts before
    # attachment context so resident images go native multimodal (or honest note).
    from agentcore.llm.image_accept import model_accepts_images

    main_model = profiles.model_for("chat") if profiles is not None else ""
    main_native_vision = model_accepts_images(main_model)
    native_image_parts: list[dict] = []
    vision_cost_sink: list[RunCost] = []
    # Built before the attachment block: its ``code_execute`` steer must follow this
    # turn's real worker assembly, never a second predicate. MCP / consult wiring
    # below only adds tools and cannot flip the execution class.
    worker_tools = build_worker_registry(
        backend=backend,
        permission_axes=permission_axes,
        languages=exec_languages if backend.location == "local" else None,
        desktop_online=desktop_online,
    )
    # Snapshot names before consult/MCP mutate the registry (attachments read-only).
    opening_tool_names = worker_tools.names
    skill_registry = build_system_skill_registry()
    register_mcp_tools(worker_tools, mcp_discover)
    _wire_conversation_log_tools(
        worker_tools,
        folder_id=folder_id,
    )

    async def _worker_on_demand() -> list:
        await wire_worker_consult(
            worker_tools,
            skill_registry=skill_registry,
            folder_id=folder_id,
            user_id=user_id,
        )
        return await _timed_phase(
            "on_demand_dir",
            list_consult_entries(worker_tools, user_id),
        )

    from agentcore.runtime.deep_research_auto import load_deep_research_auto_state

    attachment_prompt, on_demand_entries, table_resolved, deep_research_state = (
        await _gather_cancel_on_fail(
            _timed_phase(
                "attachments",
                _build_attachment_prompt(
                    attachments,
                    user_id=user_id,
                    host_conversation_id=conversation_id,
                    backend=backend,
                    main_native_vision=main_native_vision,
                    native_image_parts=native_image_parts if main_native_vision else None,
                    available_tools=opening_tool_names,
                ),
            ),
            _worker_on_demand(),
            _resolve_table_context(
                table_id=table_id,
                table_selection=table_selection,
                user_id=user_id,
                conversation_id=conversation_id,
                folder_id=folder_id,
                attachments=attachments,
            ),
            load_deep_research_auto_state(conversation_id),
        )
    )
    table_id, table_context = table_resolved
    deep_research_auto, deep_research_auto_debate_count = deep_research_state
    attachment_context = merge_attachment_and_mention_context(
        attachment_prompt.envelope, agent_mentions
    )
    attachment_slim = merge_attachment_and_mention_context(
        attachment_prompt.slim_envelope, agent_mentions
    )
    from agentcore.core.inline_body import apply_inline_body

    user_message, attachment_context = apply_inline_body(
        user_message,
        attachment_prompt.file_blocks,
        agent_mentions,
        attachment_context,
        attachment_slim,
    )
    attachment_context = attachment_context or ""
    from agentcore.documents.path_rules import (
        attachment_rel_paths,
        path_rules_note_for_paths,
    )

    path_note = path_rules_note_for_paths(
        rule_view.path_rules, attachment_rel_paths(attachments)
    )
    if path_note:
        attachment_context = (
            f"{attachment_context}\n{path_note}" if attachment_context else path_note
        )
    # Workers hold no CEO hints; frozen system is shared base + ``<按需目录>``.
    # Date / workspace / attachments ride ``worker_envelope`` (opening user).
    worker_base_prompt = compose_worker_base_prompt(
        system_prompt,
        on_demand_entries=on_demand_entries,
    )
    worker_envelope = render_worker_turn_envelope(
        workspace_context=workspace_facts,
        attachment_context=attachment_context,
    )
    # desktop_channel created earlier (MCP discovery); reuse the same instance.
    workspace_channel = workspace_channel_for_tools(
        backend,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    # The workspace backend is resolved per conversation by the caller
    # (folder space vs. its own conversation space) and injected here. The
    # engine and tools never see a Path — they only touch ``context.backend``.
    from agentcore.runtime.coordination.session import (
        invalidate_verify_cache_for_execution,
    )

    base_tool_context = ToolContext.create(
        execution_id=new_id(),
        run_id=new_id(),
        agent_id="default",
        backend=backend,
        user_id=user_id,
        conversation_id=conversation_id,
        permission_axes=(
            json.dumps(permission_axes.to_dict()) if permission_axes is not None else None
        ),
        deep_research_auto=deep_research_auto,
        deep_research_auto_debate_count=deep_research_auto_debate_count,
        table_id=table_id,
        desktop_channel=desktop_channel,
        workspace_channel=workspace_channel,
        accepts_images=main_native_vision,
        shared_workspace=folder_id is not None or auto_desk_folder_id is not None,
        auto_desk_folder_id=auto_desk_folder_id,
        ownership_desk_id=(
            str(folder_id).strip()
            if isinstance(folder_id, str) and folder_id.strip()
            else None
        ),
        material_paths=material_paths,
        attachment_context=attachment_context,
        folder_binding_injected=folder_binding_injected,
        folder_local_root_id=folder_local_root_id,
        folder_local_subpath=folder_local_subpath or None,
        on_file_landed=invalidate_verify_cache_for_execution,
        path_rules=rule_view.path_rules,
    )
    if auto_desk_folder_id:
        base_tool_context.turn_target_desk.note_folder(auto_desk_folder_id)
    from agentcore.tools.builtin.file_ops.named_desk_read import stamp_named_file_pins

    stamp_named_file_pins(
        base_tool_context,
        attachments,
        sitting_folder_id=sitting_folder_id,
    )
    from agentcore.runtime.closing_posture import reset_turn_scoped_closing_state
    from agentcore.runtime.coordination.session import current_execution_id

    bound_execution_id = base_tool_context.execution_id
    execution_id_token = current_execution_id.set(bound_execution_id)
    # Fresh turn: nothing a prior batch latched may reach this turn's finish_guard.
    reset_turn_scoped_closing_state(
        promotion_ledger=base_tool_context.promotion_ledger,
    )

    # Pillar B: if a background execution is already live for this conversation,
    # adopt it so the CEO wait path / interjection routing share one registry key.
    # Do NOT overwrite this turn's minted ``base_tool_context.execution_id`` —
    # dispatch lands on that mint (一回合一张协作图). Observation stays on the
    # adopted live graph via ``current_execution_id`` (set inside adopt).
    from agentcore.runtime.coordination.session import adopt_active_execution

    adopted = adopt_active_execution(conversation_id, event_sink=sink)
    if adopted is not None:
        # Harvest / reattach: re-stamp write-ownership honesty from the live ledger.
        from agentcore.runtime.closing_posture import (
            apply_write_ownership_honesty_for_session,
        )

        apply_write_ownership_honesty_for_session(adopted)

    return PreparedTurn(
        llm=llm,
        system_prompt=system_prompt,
        workspace_facts=workspace_facts,
        worker_base_prompt=worker_base_prompt,
        worker_envelope=worker_envelope,
        worker_tools=worker_tools,
        skill_registry=skill_registry,
        table_context=table_context,
        base_tool_context=base_tool_context,
        vision_cost_sink=vision_cost_sink,
        attachment_context=attachment_context,
        user_message=user_message,
        native_image_parts=native_image_parts,
        bound_execution_id=bound_execution_id,
        execution_id_token=execution_id_token,
        mcp_discover=mcp_discover,
        member_turn=member_turn,
    )
