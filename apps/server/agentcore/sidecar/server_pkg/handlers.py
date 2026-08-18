"""Sidecar JSON-RPC method handlers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from agentcore.account.credentials import AccountCredentials
from agentcore.api.schemas.messages import ResolveInteractionRequest, interaction_result_from_body
from agentcore.conversation.store.outbox import OutboxStore
from agentcore.core.logging import get_logger
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes
from agentcore.folders.credentials import FoldersCredentials
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import PLATFORM_MODEL_FLASH
from agentcore.runtime.interaction import default_interaction_registry
from agentcore.sidecar import protocol
from agentcore.sidecar.identity import resolve_sidecar_user_id
from agentcore.sidecar.paused_store import LocalPausedTurnStore
from agentcore.sidecar.run_session_store import LocalRunSessionStore
from agentcore.sidecar.server_pkg.result import parse_decision

logger = get_logger(__name__)

_RESOLVE_ADAPTER: TypeAdapter[ResolveInteractionRequest] = TypeAdapter(
    Annotated[ResolveInteractionRequest, Field(discriminator="kind")]
)


def _intervene_ack_fields(ack: Any) -> dict[str, Any]:
    """按人干预回执的线材形（与 REST ``SubmitRunStop/RedirectResponse`` 同字段）。"""
    return {
        "queued": ack.queued,
        "accepted": ack.accepted,
        "reason": ack.reason,
        "detail": ack.detail,
    }


class HandlerMixin:
    async def _on_initialize(self, request_id: Any, params: dict[str, Any]) -> None:
        root_raw = str(params.get("workspaceRoot") or "").strip()
        root = Path(root_raw)
        if not root_raw or not root.is_dir():
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    f"workspaceRoot is not an existing directory: {root_raw!r}",
                )
            )
            return

        raw_user = params.get("userId")
        self._user_id = resolve_sidecar_user_id(None if raw_user is None else str(raw_user))
        self._root = root.resolve()
        self._creds = self._parse_inference(params.get("inference"))
        self._folders_creds = self._parse_folders_auth(params)
        self._account_creds = self._parse_account_auth(params)
        self._apply_browser_bridge(params)
        self._approvals_enabled = bool(params.get("approvalsEnabled", True))
        self._permission_axes = self._parse_permission_axes(params) or DEFAULT_PERMISSION_AXES
        data_dir = str(params.get("dataDir") or "").strip()
        self._paused_store = self._build_paused_store(data_dir)
        self._outbox_store = self._build_outbox_store(data_dir)
        self._run_session_store = self._build_run_session_store(data_dir)
        if self._outbox_store is not None:
            from agentcore.conversation.store import set_conversation_store

            # Swap the process-wide ConversationStore so EventSink checkpoints +
            # TurnJournalWriter appends land in the local outbox (not CloudStore).
            set_conversation_store(self._outbox_store)
        # Same DEMO_TAPE_RECORD_ENABLED gate as cloud lifespan; land under
        # ``<dataDir>/recordings`` (sibling of paused/outbox) — never repo demos/.
        self._install_recorder_if_enabled(data_dir)
        # Own a CLIENT_TOOL 履约方 before any turn: the local engine's channels
        # deliver through this process's fulfill hub, not the turn EventSink.
        self._bind_fulfiller()
        self._initialized = True
        logger.info(
            "sidecar.initialized",
            user_id=self._user_id,
            root_label=self._root.name,
            inference="cloud-proxy" if self._creds else "missing",
            approvals=self._approvals_enabled,
            permission_axes=self._permission_axes.to_dict(),
            durable_pause=self._paused_store is not None,
            outbox=self._outbox_store is not None,
            durable_roster=self._run_session_store is not None,
        )
        # Silent background index warm (Cursor-style): schedule once after root is
        # bound; coalesce with later warmCodeIndex / write kicks. Never awaits ensure.
        self._schedule_code_index_warm()
        await self._reply(
            request_id,
            {
                "ok": True,
                "protocolVersion": protocol.PROTOCOL_VERSION,
                "capabilities": {
                    "turns": True,
                    "interactions": True,
                    "cancel": True,
                    # durable plan_review / ask_user resume across a process restart,
                    # gated on a usable local data dir.
                    "durablePause": self._paused_store is not None,
                    "outbox": self._outbox_store is not None,
                    "durableRoster": self._run_session_store is not None,
                    "warmCodeIndex": True,
                    # List+seed must run in Electron main (mcp-service); this flag
                    # only advertises the RPC. Desktop warmMcpDiscover does the work.
                    "warmMcpDiscover": True,
                    # Non-turn warm: parallel account HTTP → seed prepare rules/memory cache.
                    "warmAccountRulesMemory": True,
                },
            },
        )

    def _schedule_code_index_warm(self) -> None:
        """Fire-and-forget code-index ensure for the initialized workspace root."""
        if self._root is None:
            return
        backend = self._make_backend()
        backend.start_code_index_maintenance()
        logger.info("sidecar.warm_code_index", root_label=self._root.name)

    async def _on_warm_code_index(self, request_id: Any, params: dict[str, Any]) -> None:
        """Non-turn RPC: schedule background index ensure; return immediately."""
        del params  # no params today; reserved for future force flags
        if not self._initialized or self._root is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        self._schedule_code_index_warm()
        await self._reply(request_id, {"ok": True})

    async def _on_warm_mcp_discover(self, request_id: Any, params: dict[str, Any]) -> None:
        """Non-turn RPC: seed MCP discover cache from desktop ``list_tools`` payload."""
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        from agentcore.tools.mcp.wire import (
            mcp_discover_ttl_remaining,
            parse_mcp_list_payload,
            seed_mcp_discover_cache,
        )

        result = parse_mcp_list_payload(params)
        # Align cache_scope with prepare: adopt per-call userId before seed
        # (open-project warm may refresh after initialize'd as local / probe).
        self._refresh_user_id(params)
        # Open-project warm has no conversation yet — user-scoped key only.
        seed_mcp_discover_cache("", result, cache_scope=self._user_id)
        ttl_seconds = mcp_discover_ttl_remaining(cache_scope=self._user_id)
        logger.info(
            "sidecar.warm_mcp_discover",
            user_id=self._user_id,
            tool_count=result.tool_count,
            ready_servers=result.ready_servers,
            failed_servers=result.failed_servers,
            degraded=result.degraded,
            ttl_seconds=ttl_seconds,
        )
        await self._reply(
            request_id,
            {
                "ok": True,
                # 续期握手：本条 MCP 列表的剩余寿命。缓存过期即空装配（不 await
                # ClientTool），调用方必须在此窗口内重暖（含长任务在途周期续暖）。
                "ttlSeconds": ttl_seconds,
            },
        )

    async def _on_warm_account_rules_memory(
        self, request_id: Any, params: dict[str, Any]
    ) -> None:
        """Non-turn RPC: warm account rules/memory snapshot into prepare cache.

        The reply's ``ttlSeconds`` is the seeded entry's remaining life — the
        caller must re-warm within it. Prepare reads this cache only, so a lapsed
        entry means empty rules / memory injection, not a cloud re-fetch.
        """
        if not self._initialized:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        # Desktop may refresh the account ticket on warm (same shape as startTurn).
        self._refresh_creds(params)
        self._refresh_user_id(params)
        if self._account_creds is None:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_REQUEST,
                    "accountAuth required for warmAccountRulesMemory",
                )
            )
            return
        from agentcore.memory.account_prepare_cache import (
            account_rules_memory_ttl_remaining,
            warm_account_rules_memory,
        )
        from agentcore.sidecar.server_pkg.turns import normalize_folder_id_param

        folder_id = (
            normalize_folder_id_param(params.get("folderId"))
            if "folderId" in params
            else None
        )
        try:
            snapshot = await warm_account_rules_memory(
                self._account_creds,
                user_id=self._user_id,
                folder_id=folder_id,
            )
        except Exception as e:  # noqa: BLE001 - warm must not kill the sidecar
            logger.warning(
                "sidecar.warm_account_rules_memory_failed",
                user_id=self._user_id,
                folder_id=folder_id,
                error=str(e),
            )
            await self._send(
                protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e))
            )
            return
        ttl_seconds = account_rules_memory_ttl_remaining(self._user_id, folder_id)
        logger.info(
            "sidecar.warm_account_rules_memory",
            user_id=self._user_id,
            folder_id=folder_id,
            degraded=snapshot.degraded,
            topic_count=len(snapshot.memory_topics),
            memory_file_count=len(snapshot.memory_bodies),
            ttl_seconds=ttl_seconds,
        )
        await self._reply(
            request_id,
            {
                "ok": True,
                "degraded": snapshot.degraded,
                "topicCount": len(snapshot.memory_topics),
                "memoryFileCount": len(snapshot.memory_bodies),
                # 续期握手：本条快照的剩余寿命。缓存过期即空注入（不回落云端），
                # 故调用方必须在此窗口内重暖，不能把「暖过一次」当永久有效。
                "ttlSeconds": ttl_seconds,
            },
        )

    @staticmethod
    def _install_recorder_if_enabled(data_dir: str) -> None:
        """Arm the process-wide EventSink emit tap when recording is enabled.

        Switch reaches the sidecar via the same env / ``apps/server/.env`` channel
        as the cloud (``DEMO_TAPE_RECORD_ENABLED`` → ``settings.demo_tape_record_enabled``);
        no initialize-contract change. Requires ``dataDir`` so recordings land next
        to paused/outbox under ``<userData>/sidecar/recordings/``.
        """
        from agentcore.config import settings

        if not settings.demo_tape_record_enabled:
            return
        if not data_dir:
            logger.warning(
                "demo_tape.sidecar_record_skipped",
                reason="no_data_dir",
            )
            return
        from agentcore.demo_tape.recorder import install_recorder

        install_recorder(path=Path(data_dir) / "recordings")

    @staticmethod
    def _build_paused_store(data_dir: str) -> LocalPausedTurnStore | None:
        """Build the local durable-pause store from ``initialize``'s ``dataDir``.

        ``dataDir`` is the desktop's per-app data dir (e.g. ``<userData>/sidecar``);
        frames land under ``<dataDir>/paused``. Absent / blank ⇒ ``None`` ⇒ pauses
        stay in-memory (process-lifetime), the pre-durable behaviour.
        ``outbox_base`` is wired so D3 stale-claim recovery can adjudicate by journal.
        """
        if not data_dir:
            return None
        root = Path(data_dir)
        return LocalPausedTurnStore(root / "paused", outbox_base=root / "outbox")

    @staticmethod
    def _build_outbox_store(data_dir: str) -> OutboxStore | None:
        """Build the progressive outbox store (sibling of paused under dataDir).

        Pause/outbox split (as-built: 双模式工作区 §10.4): pause and outbox share
        the dataDir root but are separate stores / processors — never one state machine.
        """
        if not data_dir:
            return None
        return OutboxStore(Path(data_dir) / "outbox")

    @staticmethod
    def _build_run_session_store(data_dir: str) -> LocalRunSessionStore | None:
        """Build the local durable 留人 roster (sibling of paused under dataDir).

        Aligns sidecar with cloud ``turn_runner.session_callbacks``: without this,
        memory LRU eviction is a hard miss and rejection copy falsely claimed
        「落盘未命中」. Absent dataDir / persist disabled ⇒ ``None`` ⇒ memory-only.
        """
        if not data_dir:
            return None
        from agentcore.config import settings

        if not settings.session_roster_persist_enabled:
            return None
        return LocalRunSessionStore(Path(data_dir) / "run_sessions")

    @staticmethod
    def _parse_inference(raw: Any) -> LLMCredentials | None:
        """Build the per-turn cloud-proxy credentials from ``initialize`` params.

        ``inference = {baseUrl, apiKey, model?}`` points the engine's ``build_provider`` at
        the cloud inference proxy (so the platform key never lands on the user's
        machine). ``model`` is server-resolved at token mint and echoed here so the
        local engine logs / profiles match the proxy's upstream model.
        ``None`` / missing / incomplete → no credentials; startTurn / resume refuse
        with structured ``INFERENCE_TOKEN_EXPIRED`` (dev also BYOK — no silent
        platform-key fallback).
        """
        if not isinstance(raw, dict):
            return None
        base_url = str(raw.get("baseUrl") or "").strip()
        api_key = str(raw.get("apiKey") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not base_url or not api_key:
            return None
        return LLMCredentials(
            api_key=api_key,
            base_url=base_url,
            default_model=model or PLATFORM_MODEL_FLASH,
        )

    @staticmethod
    def _parse_folders_creds(raw: Any) -> FoldersCredentials | None:
        """Build folders narrow-ticket creds from ``folders`` / ``foldersAuth``.

        Shape matches inference: ``{baseUrl, apiKey}`` where ``baseUrl`` is the
        folders collection URL (``…/v1/folders``) and ``apiKey`` is the
        ``type=folders`` JWT. Never accepts an access token — desktop mints the
        narrow ticket separately.
        """
        if not isinstance(raw, dict):
            return None
        base_url = str(raw.get("baseUrl") or "").strip()
        api_key = str(raw.get("apiKey") or "").strip()
        if not base_url or not api_key:
            return None
        return FoldersCredentials(api_key=api_key, base_url=base_url)

    @classmethod
    def _parse_folders_auth(cls, params: dict[str, Any]) -> FoldersCredentials | None:
        """Prefer ``folders``; accept ``foldersAuth`` as an alias (desktop contract)."""
        if "folders" in params:
            return cls._parse_folders_creds(params.get("folders"))
        if "foldersAuth" in params:
            return cls._parse_folders_creds(params.get("foldersAuth"))
        return None

    @staticmethod
    def _parse_account_creds(raw: Any) -> AccountCredentials | None:
        """Build account narrow-ticket creds from ``account`` / ``accountAuth``.

        Shape matches folders: ``{baseUrl, apiKey}`` where ``baseUrl`` is the
        account API root (``…/v1/account``) and ``apiKey`` is the ``type=account``
        JWT. Never accepts an access / inference / folders token — desktop mints
        the account ticket via ``POST /v1/account/token``.
        """
        if not isinstance(raw, dict):
            return None
        base_url = str(raw.get("baseUrl") or "").strip()
        api_key = str(raw.get("apiKey") or "").strip()
        if not base_url or not api_key:
            return None
        return AccountCredentials(api_key=api_key, base_url=base_url)

    @classmethod
    def _parse_account_auth(cls, params: dict[str, Any]) -> AccountCredentials | None:
        """Prefer ``account``; accept ``accountAuth`` as an alias (desktop contract)."""
        if "account" in params:
            return cls._parse_account_creds(params.get("account"))
        if "accountAuth" in params:
            return cls._parse_account_creds(params.get("accountAuth"))
        return None

    def _refresh_creds(self, params: dict[str, Any]) -> None:
        """Refresh session creds from a per-turn ``inference`` block when present.

        A sidecar is long-lived (one per root, until app quit) but the cloud-proxy
        token rotates (2h TTL), so the desktop re-sends the current ``inference`` on
        every startTurn / resume — this keeps a day-long session from 401-ing once the
        initialize-time token expires. Absent key ⇒ keep the initialize-time creds.
        Explicit null / incomplete clears them; the next turn then early-rejects with
        ``INFERENCE_TOKEN_EXPIRED`` (no platform-key fallback).
        """
        if "inference" in params:
            self._creds = self._parse_inference(params.get("inference"))
        if "folders" in params or "foldersAuth" in params:
            self._folders_creds = self._parse_folders_auth(params)
        if "account" in params or "accountAuth" in params:
            self._account_creds = self._parse_account_auth(params)
        # Bridge creds: always apply when key present (including explicit null → clear).
        if "browserBridge" in params:
            self._apply_browser_bridge(params)

    async def _reject_if_missing_inference(self, request_id: Any, *, op: str) -> bool:
        """Refuse startTurn/resume when no inference JWT is bound. True ⇒ caller returns.

        Product code rides JSON-RPC ``error.data`` so callers / writeback adapters can
        map to ``INFERENCE_TOKEN_EXPIRED`` without scraping English internals.
        """
        if self._creds is not None:
            return False
        from agentcore.sidecar.server_pkg.turns import structured_missing_inference_error

        structured = structured_missing_inference_error()
        logger.warning(
            "sidecar.inference_credentials_missing",
            op=op,
            request_id=request_id,
        )
        await self._send(
            protocol.make_error(
                request_id,
                protocol.INVALID_REQUEST,
                structured["message"],
                data=structured,
            )
        )
        return True

    @staticmethod
    def _apply_browser_bridge(params: dict[str, Any]) -> None:
        """Adopt DesktopBrowserBridge credentials for this turn (B-Arch · C1/C4).

        Mirrors inference refresh: desktop sends ``browserBridge: {baseUrl, token}``
        on initialize / startTurn / resume. Missing key on initialize → leave env
        fallback (dev probes). Explicit null / empty → withhold browser this turn.
        """
        from agentcore.runtime.browser.desktop_bridge import apply_desktop_bridge_from_turn

        if "browserBridge" not in params:
            return
        apply_desktop_bridge_from_turn(params.get("browserBridge"))

    @staticmethod
    def _parse_permission_axes(params: dict[str, Any]) -> PermissionAxes | None:
        """Coerce desktop ``permissionAxes`` object.

        Unknown / missing / non-object ⇒ ``None`` (caller keeps current / default).
        """
        raw_axes = params.get("permissionAxes")
        if isinstance(raw_axes, dict):
            try:
                return PermissionAxes.from_mapping(raw_axes)
            except ValueError:
                return None
        return None

    def _refresh_permission_axes(
        self, params: dict[str, Any], conversation_id: str = ""
    ) -> None:
        """Adopt the conversation's CURRENT permission axes from per-turn params.

        Permission axes stay client-pushed — the desktop re-sends them on every
        startTurn / resume so a mid-session switch applies to the next turn.
        (``folderId`` is resolved in ``_run_turn`` from params when present; DB
        fallback only when the key is absent.)
        Absent / invalid ⇒ keep the current bag / initialize default.
        Per-conversation bag is stamped so harvest never reads another conv's last write.
        """
        parsed = self._parse_permission_axes(params)
        if parsed is not None:
            self._permission_axes = parsed
        cid = (conversation_id or "").strip()
        if cid:
            self._permission_axes_by_conv[cid] = (
                parsed if parsed is not None else self._permission_axes
            )

    def _refresh_user_id(self, params: dict[str, Any]) -> None:
        """Adopt per-turn ``userId`` when present (mirrors permissionAxes / inference).

        Long-lived sidecars may have ``initialize``'d as ``\"local\"`` (probe / pre-login);
        the desktop re-sends the account id on every startTurn / resume so
        ``ToolContext.user_id`` / baseline / log_context follow the logged-in principal.
        Absent key ⇒ keep the initialize-time value.

        The fulfiller session is keyed by the same id, so it re-registers here too
        (no-op when unchanged) — else the channels would look for a 履约方 under the
        new principal while the hub still holds the old one.
        """
        if "userId" not in params:
            return
        raw = params.get("userId")
        self._user_id = resolve_sidecar_user_id(None if raw is None else str(raw))
        self._bind_fulfiller()

    async def _on_start_turn(self, request_id: Any, params: dict[str, Any]) -> None:
        if not self._initialized or self._root is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        turn_id = str(params.get("turnId") or "").strip()
        if not turn_id:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_PARAMS, "turnId is required")
            )
            return
        conversation_id = str(params.get("conversationId") or turn_id)
        if turn_id in self._turns:
            await self._reject_turn_already_running(
                request_id,
                op="startTurn",
                turn_id=turn_id,
                conversation_id=conversation_id,
            )
            return
        occupying = self.live_turn_task(conversation_id)
        if occupying is not None:
            occupying_id = next(
                (
                    tid
                    for tid, task in self._turns.items()
                    if task is occupying
                ),
                turn_id,
            )
            await self._reject_turn_already_running(
                request_id,
                op="startTurn",
                turn_id=occupying_id,
                conversation_id=conversation_id,
            )
            return

        # Tape bindings live on the cloud process. A local/sidecar turn never sees
        # them — historically this silently became a normal AI reply. When replay
        # is armed, refuse so the operator gets an explicit error instead.
        if await self._reject_if_tape_bound_local(request_id, conversation_id):
            return

        # Adopt this turn's cloud-proxy token before it runs (refreshes a rotated TTL).
        self._refresh_creds(params)
        self._refresh_permission_axes(params, conversation_id)
        self._refresh_user_id(params)
        self._declare_fulfill_root(params)

        # The response to startTurn is DEFERRED until the turn completes (it carries)
        # the final result); the live events flow as ``turn/event`` notifications in
        # the meantime. Spawning a task lets ``respond`` / ``cancel`` be serviced by
        # the read loop while the turn runs. Missing inference is refused inside
        # ``_run_turn`` (structured result + outbox) before prepare/build_turn_router.
        task = asyncio.create_task(self._run_turn(request_id, turn_id, params))
        self._register_turn(turn_id, task, conversation_id=conversation_id)

    async def _reject_turn_already_running(
        self,
        request_id: Any,
        *,
        op: str,
        turn_id: str,
        conversation_id: str,
    ) -> None:
        """Refuse a duplicate startTurn/resume; log enough to triage double-submit vs zombie."""
        task = self._turns.get(turn_id)
        logger.warning(
            "sidecar.turn_already_running",
            op=op,
            turn_id=turn_id,
            conversation_id=conversation_id
            or self._turn_conversations.get(turn_id)
            or None,
            inflight_done=None if task is None else task.done(),
            inflight_cancelled=None if task is None else task.cancelled(),
        )
        await self._send(
            protocol.make_error(
                request_id, protocol.INVALID_PARAMS, f"turn already running: {turn_id}"
            )
        )

    async def _reject_if_tape_bound_local(self, request_id: Any, conversation_id: str) -> bool:
        """Return True when the startTurn RPC was rejected (caller must return)."""
        from agentcore.demo_tape.binding import LOCAL_SESSION_BOUND_MSG, resolve_binding

        binding = resolve_binding(conversation_id)
        if binding is None:
            return False
        logger.error(
            "demo_tape.sidecar_local_session_bound",
            conversation_id=conversation_id,
            tape=str(binding.tape_path),
            speed=binding.speed,
        )
        await self._send(
            protocol.make_error(
                request_id,
                protocol.INVALID_PARAMS,
                f"{LOCAL_SESSION_BOUND_MSG} tape={binding.tape_path.name}",
            )
        )
        return True

    async def _on_respond(self, request_id: Any, params: dict[str, Any]) -> None:
        interaction_id = str(params.get("requestId") or "")
        conversation_id = str(params.get("conversationId") or "")
        try:
            body = _RESOLVE_ADAPTER.validate_python(params.get("result"))
        except ValidationError as e:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, f"invalid respond result: {e}"
                )
            )
            return

        # Mirror the cloud resolve route's guards (routes/conversations.py): refuse a
        # stale / cross-conversation / kind-mismatched settle, and build the kind's
        # typed result via the SHARED projection — so an approval resolves with an
        # ApprovalDecision enum (the gate compares it by identity), a client_tool with
        # its op envelope, etc., exactly as in cloud mode. ask_user / plan_review are no
        # longer resolvable here (挂起即收口 ②, Phase 3 — they finalize and resume cold).
        registry = default_interaction_registry()
        pending = registry.get(interaction_id)
        if (
            pending is None
            or pending.conversation_id != conversation_id
            or pending.kind != body.kind
        ):
            await self._reply(request_id, {"resolved": False})
            return
        resolved = registry.resolve(
            interaction_id,
            interaction_result_from_body(body),
            conversation_id=conversation_id,
        )
        await self._reply(request_id, {"resolved": bool(resolved)})

    async def _on_resume(self, request_id: Any, params: dict[str, Any]) -> None:
        """Continue a durably-paused turn on a fresh process (结构化挂起 2b resume).

        Mirrors the cloud ``POST .../resume`` route: claim the local frame (atomic,
        so a turn never resumes twice) then drive the rest of the turn on a fresh
        sink, replying with the same final-result shape as ``startTurn``. The
        message_id doubles as the event-routing turn id (one durable pause per turn).

        Cold resume × live (D9)：宿主仍占 ``_turns`` 时不拒 ``turn already running``——
        peek → settlement 预写 → ``resume_deferred`` → 槽空后再 claim 续跑。
        """
        if not self._initialized or self._root is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.NOT_INITIALIZED, "initialize must be called first"
                )
            )
            return
        message_id = str(params.get("messageId") or "").strip()
        conversation_id = str(params.get("conversationId") or "").strip()
        if not message_id or not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "messageId and conversationId are required",
                )
            )
            return
        if self._paused_store is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, "durable pause is not enabled"
                )
            )
            return

        busy_reason = self.busy_reason_for_resume(conversation_id, message_id)
        if busy_reason is not None:
            await self._on_resume_when_busy(
                request_id,
                params,
                message_id=message_id,
                conversation_id=conversation_id,
                busy_reason=busy_reason,
            )
            return

        # Refresh + gate BEFORE claim so a missing JWT never consumes the pause frame
        # (desktop remints and retries; RESUME_RETRYABLE-class keep-card semantics).
        self._refresh_creds(params)
        self._refresh_permission_axes(params, conversation_id)
        self._refresh_user_id(params)
        self._declare_fulfill_root(params)
        if await self._reject_if_missing_inference(request_id, op="resume"):
            return

        suspension = await self._paused_store.claim(message_id, conversation_id=conversation_id)
        if suspension is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.PAUSED_TURN_NOT_FOUND, "挂起的回合不存在或已处理"
                )
            )
            return

        parsed = self._parse_resume_params(params)
        decision = parsed["decision"]
        note = parsed["note"]
        selected = parsed["selected"]
        excluded_run_ids = parsed["excluded_run_ids"]
        write_capability_overrides = parsed["write_capability_overrides"]
        model_overrides = parsed["model_overrides"]
        trace_id = parsed["trace_id"]
        user_message_id = parsed["user_message_id"]
        # Per-turn account id wins over the freeze-in-frame value (probe may have
        # initialized as local; login mid-session must not leave ToolContext on the
        # alias UUID).
        suspension.user_id = self._user_id
        # Prefer resume RPC folderId / localRootId/localSubpath when the desktop
        # re-sends them; else keep frame-stamped scope/bind from the pause card.
        from agentcore.sidecar.server_pkg.turns import apply_rpc_folder_binding_to_suspension

        apply_rpc_folder_binding_to_suspension(suspension, params)
        folder_cid = str(getattr(suspension, "conversation_id", "") or "")
        prior_folder = self.stamp_folder_scope(
            folder_cid,
            folder_id=getattr(suspension, "folder_id", None),
            binding_injected=bool(getattr(suspension, "folder_binding_injected", False)),
            local_root_id=getattr(suspension, "folder_local_root_id", None),
            local_subpath=str(getattr(suspension, "folder_local_subpath", "") or ""),
        )

        veto_err = self._validate_resume_team_veto(
            suspension,
            decision,
            excluded_run_ids=excluded_run_ids,
            write_capability_overrides=write_capability_overrides,
            model_overrides=model_overrides,
        )
        if veto_err is not None:
            self.restore_folder_scope(folder_cid, prior_folder)
            await self._paused_store.rollback_claim(message_id)
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_PARAMS, veto_err)
            )
            return

        task = asyncio.create_task(
            self._run_resume(
                request_id,
                suspension,
                decision,
                note,
                selected,
                trace_id,
                user_message_id,
                params.get("externalMounts"),
                excluded_run_ids=excluded_run_ids,
                write_capability_overrides=write_capability_overrides,
                model_overrides=model_overrides,
            )
        )
        self._register_turn(message_id, task, conversation_id=conversation_id)

    def _parse_resume_params(self, params: dict[str, Any]) -> dict[str, Any]:
        decision = parse_decision(params.get("decision"))
        note = str(params.get("note") or "")
        selected = [str(s) for s in (params.get("selected") or [])]
        # 开工组队有限否决（对齐云 POST resume）：仅 delegate team_preview continue 生效。
        excluded_run_ids = [
            str(x).strip()
            for x in (params.get("excluded_run_ids") or [])
            if str(x).strip()
        ]
        write_capability_overrides: list[dict[str, str]] = []
        for raw in params.get("write_capability_overrides") or []:
            if not isinstance(raw, dict):
                continue
            rid = str(raw.get("run_id") or "").strip()
            cap = str(raw.get("capability") or "").strip()
            if rid:
                write_capability_overrides.append({"run_id": rid, "capability": cap})
        model_overrides: dict[str, dict[str, str]] = {}
        raw_models = params.get("model_overrides") or {}
        if isinstance(raw_models, dict):
            for rid, row in raw_models.items():
                key = str(rid or "").strip()
                if not key or not isinstance(row, dict):
                    continue
                model = str(row.get("model") or "").strip()
                if not model:
                    continue
                entry: dict[str, str] = {"model": model}
                origin = str(row.get("origin") or "").strip().lower()
                if origin in ("platform", "byok"):
                    entry["origin"] = origin
                provider_id = str(row.get("provider_id") or "").strip()
                if provider_id:
                    entry["provider_id"] = provider_id
                model_overrides[key] = entry
        return {
            "decision": decision,
            "note": note,
            "selected": selected,
            "excluded_run_ids": excluded_run_ids,
            "write_capability_overrides": write_capability_overrides,
            "model_overrides": model_overrides,
            "trace_id": str(params.get("traceId") or ""),
            "user_message_id": str(params.get("userMessageId") or "").strip(),
        }

    @staticmethod
    def _validate_resume_team_veto(
        suspension: Any,
        decision: Any,
        *,
        excluded_run_ids: list[str],
        write_capability_overrides: list[dict[str, str]],
        model_overrides: dict[str, dict[str, str]],
    ) -> str | None:
        """Return an error message when team veto / debate overrides are illegal."""
        from agentcore.core.errors import ValidationError as CoreValidationError
        from agentcore.runtime.kickoff.team_veto import (
            should_apply_debate_model_overrides,
            should_apply_team_veto,
            validate_debate_model_overrides,
            validate_team_preview_veto_workers,
        )
        from agentcore.runtime.suspension import TeamPreviewSuspension

        if should_apply_team_veto(suspension, decision) and isinstance(
            suspension, TeamPreviewSuspension
        ):
            try:
                validate_team_preview_veto_workers(
                    suspension.workers,
                    excluded_run_ids=excluded_run_ids,
                    write_capability_overrides=write_capability_overrides,
                    model_overrides=model_overrides,
                )
            except CoreValidationError as e:
                return str(e)
        elif isinstance(suspension, TeamPreviewSuspension) and should_apply_debate_model_overrides(
            suspension, decision
        ):
            try:
                validate_debate_model_overrides(
                    suspension.sides,
                    debate_arguments=suspension.debate_arguments,
                    model_overrides=model_overrides,
                )
            except CoreValidationError as e:
                return str(e)
        return None

    async def _on_resume_when_busy(
        self,
        request_id: Any,
        params: dict[str, Any],
        *,
        message_id: str,
        conversation_id: str,
        busy_reason: str,
    ) -> None:
        """Busy cold resume: prewrite → ``resume_deferred`` → wait → claim → run.

        Settlement is durable before waiting so a long D1 hold cannot drop the click.
        Same ``message_id`` re-submit joins the parked waiter (no second prewrite);
        a different ``message_id`` supersedes (prior deferred Future cancelled).
        RPC reply still deferred until the resume pipeline finishes (fan-out to joiners).
        """
        assert self._paused_store is not None
        from agentcore.runtime.events import resume_deferred
        from agentcore.sidecar.server_pkg.core import SidecarResumeDeferredWaiter
        from agentcore.sidecar.server_pkg.turns import (
            apply_rpc_folder_binding_to_suspension,
            resolve_resume_user_message_id,
        )

        existing = self._resume_deferred.get(conversation_id)
        if existing is not None and existing.message_id == message_id:
            # Idempotent join: settlement already locked; do not cancel / re-prewrite.
            if existing.slot_free.cancelled():
                await self._send(
                    protocol.make_error(
                        request_id,
                        protocol.INVALID_PARAMS,
                        "resume superseded",
                    )
                )
                return
            if request_id not in existing.reply_ids:
                existing.reply_ids.append(request_id)
            logger.info(
                "resume.deferred_joined",
                conversation_id=conversation_id,
                message_id=message_id,
                busy_reason=existing.busy_reason,
                reply_count=len(existing.reply_ids),
            )
            return

        peeked = await self._paused_store.load(message_id, conversation_id=conversation_id)
        if peeked is None:
            await self._send(
                protocol.make_error(
                    request_id, protocol.PAUSED_TURN_NOT_FOUND, "挂起的回合不存在或已处理"
                )
            )
            return

        parsed = self._parse_resume_params(params)
        decision = parsed["decision"]
        note = parsed["note"]
        selected = parsed["selected"]
        excluded_run_ids = parsed["excluded_run_ids"]
        write_capability_overrides = parsed["write_capability_overrides"]
        model_overrides = parsed["model_overrides"]
        trace_id = parsed["trace_id"]
        user_message_id = parsed["user_message_id"]

        self._refresh_creds(params)
        self._refresh_permission_axes(params, conversation_id)
        self._refresh_user_id(params)
        self._declare_fulfill_root(params)
        if await self._reject_if_missing_inference(request_id, op="resume"):
            return
        peeked.user_id = self._user_id
        apply_rpc_folder_binding_to_suspension(peeked, params)
        prior_folder = self.stamp_folder_scope(
            conversation_id,
            folder_id=getattr(peeked, "folder_id", None),
            binding_injected=bool(getattr(peeked, "folder_binding_injected", False)),
            local_root_id=getattr(peeked, "folder_local_root_id", None),
            local_subpath=str(getattr(peeked, "folder_local_subpath", "") or ""),
        )

        veto_err = self._validate_resume_team_veto(
            peeked,
            decision,
            excluded_run_ids=excluded_run_ids,
            write_capability_overrides=write_capability_overrides,
            model_overrides=model_overrides,
        )
        if veto_err is not None:
            self.restore_folder_scope(conversation_id, prior_folder)
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_PARAMS, veto_err)
            )
            return

        umid = resolve_resume_user_message_id(
            user_message_id,
            getattr(peeked, "user_message_id", None),
        )
        decision_value = decision.value if hasattr(decision, "value") else str(decision)
        outbox = self._outbox_store
        if outbox is not None:
            try:
                from agentcore.sidecar.settlement_prewrite import (
                    prewrite_sidecar_resume_settlement,
                )

                await outbox.reopen_for_resume(
                    turn_id=message_id,
                    user_message_id=umid,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
                await prewrite_sidecar_resume_settlement(
                    outbox,
                    peeked,
                    decision=decision_value,
                    note=note,
                    selected=selected,
                    user_message_id=umid,
                    trace_id=trace_id,
                    excluded_run_ids=excluded_run_ids,
                    write_capability_overrides=write_capability_overrides,
                    model_overrides=model_overrides,
                )
            except Exception as e:
                logger.warning(
                    "sidecar.resume_settlement_prewrite_failed",
                    turn_id=message_id,
                    error=str(e),
                )
                await self._send(
                    protocol.make_error(
                        request_id,
                        protocol.RESUME_RETRYABLE,
                        f"settlement prewrite failed: {e}",
                    )
                )
                return
            await self._outbox_resume_writeback(
                outbox,
                conversation_id=conversation_id,
                message_id=message_id,
                trace_id=trace_id,
            )

        ev = resume_deferred(
            message_id=message_id,
            conversation_id=conversation_id,
            busy_reason=busy_reason,  # type: ignore[arg-type]
        )
        await self._send(
            protocol.make_notification(
                "turn/event",
                {
                    "turnId": message_id,
                    "conversationId": conversation_id,
                    "event": {
                        "type": ev.type.value,
                        "timestamp": ev.timestamp,
                        "payload": ev.payload,
                    },
                },
            )
        )

        slot_free: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        waiter = SidecarResumeDeferredWaiter(
            conversation_id=conversation_id,
            message_id=message_id,
            busy_reason=busy_reason,  # type: ignore[arg-type]
            slot_free=slot_free,
            reply_ids=[request_id],
        )
        self.register_resume_deferred(waiter)

        async def _wait_claim_and_run() -> None:
            try:
                while True:
                    try:
                        await waiter.slot_free
                    except asyncio.CancelledError:
                        await self._send_to_request_ids(
                            waiter.reply_ids,
                            request_id,
                            lambda rid: protocol.make_error(
                                rid,
                                protocol.INVALID_PARAMS,
                                "resume superseded",
                            ),
                        )
                        return
                    if self.busy_reason_for_resume(conversation_id, message_id) is None:
                        break
                    # Slot re-taken between wake and claim — re-park (same SSE posture).
                    waiter.slot_free = asyncio.get_running_loop().create_future()
                    self.register_resume_deferred(waiter)

                claimed = await self._paused_store.claim(
                    message_id, conversation_id=conversation_id
                )
                if claimed is None:
                    await self._send_to_request_ids(
                        waiter.reply_ids,
                        request_id,
                        lambda rid: protocol.make_error(
                            rid,
                            protocol.PAUSED_TURN_NOT_FOUND,
                            "挂起的回合不存在或已处理",
                        ),
                    )
                    return
                # Peeked frame already carries settlement in journal_entries (prewrite);
                # keep that stream for pipeline dedupe. Claim only consumed the file.
                peeked.user_id = self._user_id
                apply_rpc_folder_binding_to_suspension(peeked, params)
                self.stamp_folder_scope(
                    conversation_id,
                    folder_id=getattr(peeked, "folder_id", None),
                    binding_injected=bool(
                        getattr(peeked, "folder_binding_injected", False)
                    ),
                    local_root_id=getattr(peeked, "folder_local_root_id", None),
                    local_subpath=str(
                        getattr(peeked, "folder_local_subpath", "") or ""
                    ),
                )
                task = asyncio.create_task(
                    self._run_resume(
                        request_id,
                        peeked,
                        decision,
                        note,
                        selected,
                        trace_id,
                        umid,
                        params.get("externalMounts"),
                        excluded_run_ids=excluded_run_ids,
                        write_capability_overrides=write_capability_overrides,
                        model_overrides=model_overrides,
                        settlement_prewritten=outbox is not None,
                        reply_ids=waiter.reply_ids,
                    )
                )
                self._register_turn(message_id, task, conversation_id=conversation_id)
            except Exception as e:  # noqa: BLE001 — keep read-loop safe if wait task escapes
                err_msg = str(e)
                logger.error(
                    "sidecar.resume_failed",
                    message_id=message_id,
                    error=err_msg,
                    exc_info=True,
                )
                await self._send_to_request_ids(
                    waiter.reply_ids,
                    request_id,
                    lambda rid: protocol.make_error(rid, protocol.INTERNAL_ERROR, err_msg),
                )

        wait_task = asyncio.create_task(_wait_claim_and_run())
        # Keep the waiter alive until claim/run (or supersede) — not in ``_turns`` yet.
        self._pending_sends.add(wait_task)
        wait_task.add_done_callback(self._pending_sends.discard)

    async def _on_list_paused(self, request_id: Any, params: dict[str, Any]) -> None:
        """A conversation's pending durable pauses, as resume-card summaries.

        Read-only (does not claim); ``resume`` claims. Mirrors the cloud recovery
        snapshot's ``paused`` summaries (``GET .../recovery``) so the desktop renders
        the same resume cards.
        """
        conversation_id = str(params.get("conversationId") or "").strip()
        if self._paused_store is None or not conversation_id:
            await self._reply(request_id, {"data": []})
            return
        summaries = await self._paused_store.list_summaries(conversation_id)
        await self._reply(request_id, {"data": summaries})

    async def _on_cancel(self, request_id: Any, params: dict[str, Any]) -> None:
        """Explicit user stop — mirrors cloud ``POST …/stop`` (hard cancel).

        Cascade-cancels live coordination then cancels the turn task. ``mode`` /
        ``reason`` only fingerprint the salvage log (``user_stop`` / abort tags).
        """
        from agentcore.sidecar.server_pkg.cancel_mark import (
            CANCEL_REASON_ATTR,
            normalize_cancel_reason,
        )

        turn_id = str(params.get("turnId") or "")
        reason = normalize_cancel_reason(params.get("reason"))
        # Hard cancel only; legacy pause / unspecified / unknown tags → user_stop.
        # Preserve abort_signal / attach_abort fingerprints for salvage logs.
        if reason not in ("abort_signal", "attach_abort"):
            reason = "user_stop"

        cid_from_params = str(params.get("conversationId") or "").strip()
        conversation_id = cid_from_params or self._turn_conversations.get(turn_id, "")
        cascaded = False
        client_tools_cancelled = 0
        if conversation_id:
            from agentcore.runtime.coordination.session import (
                cancel_coordination_on_user_stop,
            )
            from agentcore.runtime.events.client_tool_reattach import (
                cancel_pending_client_tools,
            )

            cascaded = cancel_coordination_on_user_stop(conversation_id)
            # Before ``task.cancel()``: unwinding discards the registry entries, so
            # an op already dispatched to the desktop (host_shell…) would keep
            # running on the user's machine with nobody awaiting it.
            client_tools_cancelled = cancel_pending_client_tools(conversation_id)
        task = self._turns.get(turn_id)
        task_found = task is not None
        task_done = bool(task is not None and task.done())
        task_cancelled = False
        if task is not None and not task.done():
            setattr(task, CANCEL_REASON_ATTR, reason)
            task.cancel()
            task_cancelled = True
            await self._reply(request_id, {"cancelled": True, "mode": "cancel"})
        else:
            await self._reply(
                request_id,
                {"cancelled": cascaded, "mode": "cancel"},
            )
        logger.info(
            "sidecar.turn_cancel_requested",
            turn_id=turn_id or None,
            conversation_id=conversation_id or None,
            reason=reason,
            mode="cancel",
            cascaded=cascaded,
            task_found=task_found,
            task_done=task_done,
            task_cancelled=task_cancelled,
            client_tools_cancelled=client_tools_cancelled,
        )

    async def _on_run_redirect(self, request_id: Any, params: dict[str, Any]) -> None:
        from agentcore.runtime.runs.intervene import accept_run_redirect

        execution_id = str(params.get("executionId") or "").strip()
        run_id = str(params.get("runId") or "").strip()
        feedback = str(params.get("feedback") or "").strip()
        conversation_id = str(params.get("conversationId") or "").strip()
        if not execution_id or not run_id or not feedback or not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "runRedirect requires executionId, runId, feedback, conversationId",
                )
            )
            return
        ack = accept_run_redirect(
            execution_id=execution_id,
            run_id=run_id,
            feedback=feedback,
            conversation_id=conversation_id,
        )
        await self._reply(request_id, {"ok": True, **_intervene_ack_fields(ack)})

    async def _on_run_stop(self, request_id: Any, params: dict[str, Any]) -> None:
        from agentcore.runtime.runs.intervene import accept_run_stop

        execution_id = str(params.get("executionId") or "").strip()
        conversation_id = str(params.get("conversationId") or "").strip()
        raw_run = params.get("runId")
        run_id = str(raw_run).strip() if raw_run is not None else None
        if run_id == "":
            run_id = None
        if not execution_id or not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "runStop requires executionId, conversationId",
                )
            )
            return
        ack = accept_run_stop(
            execution_id=execution_id,
            run_id=run_id,
            conversation_id=conversation_id,
        )
        await self._reply(request_id, {"ok": True, **_intervene_ack_fields(ack)})

    async def _on_debate_steer(self, request_id: Any, params: dict[str, Any]) -> None:
        from agentcore.runtime.debate.steer_queue import enqueue_steer, peek_steer_count

        execution_id = str(params.get("executionId") or "").strip()
        conversation_id = str(params.get("conversationId") or "").strip()
        decision = str(params.get("decision") or "continue").strip()
        focus = str(params.get("focus") or "").strip()
        ask = str(params.get("ask") or "").strip()
        ask_target = str(params.get("askTarget") or "").strip()
        if not execution_id or not conversation_id or decision not in ("continue", "conclude"):
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "debateSteer requires executionId, conversationId, decision∈continue|conclude",
                )
            )
            return
        # ok=False = 掌舵窗口已关（辩论没在跑 / 已过末轮边界）：没有边界来捞它，如实拒收。
        accepted = (
            enqueue_steer(
                execution_id=execution_id,
                conversation_id=conversation_id,
                decision=decision,  # type: ignore[arg-type]
                focus=focus,
                ask=ask,
                ask_target=ask_target,
            )
            is not None
        )
        await self._reply(
            request_id, {"ok": accepted, "queued": peek_steer_count(execution_id)}
        )

    async def _on_list_browser_sessions(self, request_id: Any, params: dict[str, Any]) -> None:
        """Local hydrate: list live BrowserSessions from this process's Registry.

        Wire shape mirrors cloud ``GET …/browser/sessions`` (snake_case) so the
        desktop mapper can reuse the same fromWire path.
        """
        conversation_id = str(params.get("conversationId") or "").strip()
        if not conversation_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "listBrowserSessions requires conversationId",
                )
            )
            return
        from agentcore.runtime.browser.registry import default_browser_session_registry

        reg = default_browser_session_registry()
        infos = reg.list_by_conversation(conversation_id)
        active = reg.resolve_session_id(conversation_id)
        await self._reply(
            request_id,
            {
                "data": [
                    {
                        "session_id": i.session_id,
                        "conversation_id": i.conversation_id,
                        "host_kind": i.host_kind,
                        "control": i.control,
                        "run_id": i.run_id,
                        "created_at": i.created_at,
                        "last_used": i.last_used,
                        "url": i.url,
                        "title": i.title,
                    }
                    for i in infos
                ],
                "active_session_id": active,
            },
        )

    async def _on_turn_files_diff(self, request_id: Any, params: dict[str, Any]) -> None:
        """A1+ local: baseline zip vs live workspace (read-only; no cloud path)."""
        if self._root is None:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_REQUEST, "sidecar not initialized")
            )
            return
        message_id = str(params.get("messageId") or "").strip()
        if not message_id:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, "turnFilesDiff requires messageId"
                )
            )
            return
        baseline_raw = params.get("baselineSnapshotId")
        baseline_id = (
            str(baseline_raw).strip()
            if baseline_raw is not None and str(baseline_raw).strip()
            else None
        )
        from agentcore.workspace.turn_diff import compute_local_turn_files_diff

        try:
            result = await compute_local_turn_files_diff(
                workspace_root=self._root,
                message_id=message_id,
                baseline_snapshot_id=baseline_id,
            )
        except Exception as e:
            logger.warning("sidecar.turn_files_diff_failed", error=str(e), exc_info=True)
            await self._reply(
                request_id,
                {
                    "message_id": message_id,
                    "baseline_snapshot_id": baseline_id,
                    "available": False,
                    "data": [],
                    "total": 0,
                    "added": 0,
                    "modified": 0,
                    "deleted": 0,
                },
            )
            return

        rows = [
            {
                "path": c.path,
                "change_type": c.change_type,
                "base_sha": c.base_sha,
                "result_sha": c.result_sha,
                "is_binary": c.is_binary,
                "content": c.content,
                "size_bytes": c.size_bytes,
                "base_content": c.base_content,
            }
            for c in result.changes
        ]
        added = sum(1 for r in rows if r["change_type"] == "added")
        modified = sum(1 for r in rows if r["change_type"] == "modified")
        deleted = sum(1 for r in rows if r["change_type"] == "deleted")
        await self._reply(
            request_id,
            {
                "message_id": result.message_id,
                "baseline_snapshot_id": result.baseline_snapshot_id,
                "available": result.available,
                "data": rows,
                "total": len(rows),
                "added": added,
                "modified": modified,
                "deleted": deleted,
            },
        )

    async def _on_restore_turn_baseline(self, request_id: Any, params: dict[str, Any]) -> None:
        """A2′ local: unzip baseline over workspace (never cloud restoreSnapshot)."""
        if self._root is None:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_REQUEST, "sidecar not initialized")
            )
            return
        snapshot_id = str(
            params.get("snapshotId") or params.get("baselineSnapshotId") or ""
        ).strip()
        if not snapshot_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "restoreTurnBaseline requires snapshotId",
                )
            )
            return
        from agentcore.workspace.turn_diff import restore_local_turn_baseline

        try:
            await restore_local_turn_baseline(workspace_root=self._root, snapshot_id=snapshot_id)
        except FileNotFoundError:
            await self._send(
                protocol.make_error(
                    request_id, protocol.INVALID_PARAMS, f"baseline not found: {snapshot_id}"
                )
            )
            return
        except Exception as e:
            logger.warning("sidecar.restore_turn_baseline_failed", error=str(e), exc_info=True)
            await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))
            return
        await self._reply(request_id, {"ok": True, "snapshot_id": snapshot_id})

    async def _on_create_workspace_version(
        self, request_id: Any, params: dict[str, Any]
    ) -> None:
        """命名版本 · 创建：zip 本机工作区到 ``AgentCore/versions/<id>/``。

        用户显式动作，与 best-effort 的回合基线分轨：任何失败都回 error，绝不
        静默成功让用户以为版本已留下。
        """
        if self._root is None:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_REQUEST, "sidecar not initialized")
            )
            return
        from agentcore.storage._archive import ArchiveLimitError
        from agentcore.workspace.versions import (
            InvalidVersionNameError,
            create_workspace_version,
        )

        try:
            version = await create_workspace_version(
                workspace_root=self._root, name=str(params.get("name") or "")
            )
        except InvalidVersionNameError as e:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_PARAMS, str(e))
            )
            return
        except ArchiveLimitError as e:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    f"工作区过大，无法留版本（{e.reason}）："
                    f"{e.file_count} 个文件 / {e.total_bytes} 字节",
                )
            )
            return
        except Exception as e:
            logger.warning(
                "sidecar.create_workspace_version_failed", error=str(e), exc_info=True
            )
            await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))
            return
        await self._reply(request_id, version.to_wire())

    async def _on_restore_workspace_version(
        self, request_id: Any, params: dict[str, Any]
    ) -> None:
        """命名版本 · 恢复：overlay 解压回工作区（不清空，不经云 restoreSnapshot）。"""
        if self._root is None:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_REQUEST, "sidecar not initialized")
            )
            return
        version_id = str(params.get("versionId") or "").strip()
        if not version_id:
            await self._send(
                protocol.make_error(
                    request_id,
                    protocol.INVALID_PARAMS,
                    "restoreWorkspaceVersion requires versionId",
                )
            )
            return
        from agentcore.workspace.versions import (
            InvalidVersionIdError,
            VersionNotFoundError,
            restore_workspace_version,
        )

        try:
            version = await restore_workspace_version(
                workspace_root=self._root, version_id=version_id
            )
        except (InvalidVersionIdError, VersionNotFoundError) as e:
            await self._send(
                protocol.make_error(request_id, protocol.INVALID_PARAMS, str(e))
            )
            return
        except Exception as e:
            logger.warning(
                "sidecar.restore_workspace_version_failed", error=str(e), exc_info=True
            )
            await self._send(protocol.make_error(request_id, protocol.INTERNAL_ERROR, str(e)))
            return
        await self._reply(request_id, {"ok": True, **version.to_wire()})
