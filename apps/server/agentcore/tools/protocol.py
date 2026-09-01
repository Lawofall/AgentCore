"""Tool Protocol, ToolSchema, and approval three-state.

Defines the unified contract for all tools (built-in and external).
Tools declare their schema (for LLM function calling) and implement execute().
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

WriteScope = Literal["none", "explore_memory", "project"]
# Tool-output audience: stamped at production. User-bubble writers refuse ``ceo``.
ToolAudience = Literal["user", "ceo"]
TOOL_AUDIENCE_USER: ToolAudience = "user"
TOOL_AUDIENCE_CEO: ToolAudience = "ceo"

from agentcore.core.text import truncate_head_tail
from agentcore.core.types import ToolApproval, ToolCategory, ToolEffect
from agentcore.tools.file_products import FileProduct

if TYPE_CHECKING:
    from agentcore.board.channel import BoardChannel
    from agentcore.desktop.channel import DesktopClientChannel
    from agentcore.runtime.costing import RunCost
    from agentcore.vision.protocol import VisionReader
    from agentcore.workspace.channel import WorkspaceChannel
    from agentcore.workspace.protocol import WorkspaceBackend
    from agentcore.workspace.write_claims import WriteCoordinator


@dataclass
class WorkspaceSlot:
    """Shared mutable workspace root for one conversation turn.

    ``dataclasses.replace`` on :class:`ToolContext` copies this object by
    reference, so a mid-turn rebind (``ctx.backend = new``) is visible to every
    snapshot that still shares the slot. Callers that deliberately sit on another
    desk (``apply_target_desktop`` / ``folder_fs``) must pass a fresh slot.
    """

    backend: WorkspaceBackend
    material_paths: frozenset[str] = field(default_factory=frozenset)
    # Per-desk cache for empty-desk project-shell (not turn-global: a fork sits
    # on another root). ``None`` = not listed yet. Invalidated on backend rebind.
    desk_visibly_empty: bool | None = None
    # Test override for nested-Folder names. ``None`` = load from DB on register
    # (``ownership_desk_id``). A frozenset skips the query (including empty).
    child_folder_names: frozenset[str] | None = None


def fork_workspace_slot(
    backend: WorkspaceBackend,
    *,
    material_paths: frozenset[str] | None = None,
) -> WorkspaceSlot:
    """Build a new slot that does not follow a parent rebind."""
    return WorkspaceSlot(
        backend=backend,
        material_paths=frozenset() if material_paths is None else material_paths,
    )


@dataclass
class TurnExploreGate:
    """CEO-turn explore pending + write_scope, shared across ``replace()`` copies.

    Engine injects per-call ``on_phase`` via ``dataclasses.replace``, which copies
    bool / str fields by value. This object is copied by reference (same as
    :class:`WorkspaceSlot`), so ``update_folder_profile`` close-out is visible to
    ``delegate`` on the pipeline base context.

    Worker forks that need a different write_scope must pass a fresh gate
    (:func:`fork_explore_write_scope`) so siblings do not share write permission.

    ``turn_created_folder_ids`` is a turn-level ledger (shared by reference on
    fork): folders minted this turn via ``create_folder`` / first auto-desk.
    A worker whose ``target_folder_id`` is in this set may write the project
    tree even while explore-pending still locks the birth folder.
    """

    pending: bool = False
    write_scope: WriteScope = "project"
    turn_created_folder_ids: set[str] = field(default_factory=set)


def fork_explore_write_scope(
    context: ToolContext,
    write_scope: str,
) -> TurnExploreGate:
    """Snapshot explore-pending; give this worker its own write_scope."""
    scope: WriteScope = "project"
    if write_scope == "none":
        scope = "none"
    elif write_scope == "explore_memory":
        scope = "explore_memory"
    elif write_scope == "project":
        scope = "project"
    return TurnExploreGate(
        pending=bool(context.cold_start_explore_pending),
        write_scope=scope,
        turn_created_folder_ids=context._explore_gate.turn_created_folder_ids,
    )


@dataclass(frozen=True)
class EscalationOutcome:
    """The result of a worker's blocking escalate (阻塞式求决策 §4.4).

    ``status``:
    - ``"resolved"`` — answered (``answer`` carries it);
    - ``"assumed"`` — explicit 按假设继续 (user or CEO);
    - ``"timed_out"`` — wall-clock miss (no answer within the window);
    - ``"degraded"`` — never suspended (concurrency cap) → proceed on assumption
      like a non-blocking escalate.

    ``assumed`` and ``timed_out`` share the worker fallback (use stated assumption)
    but must stay distinct on the wire — conflating them as ``timeout`` made
    「点了按假设继续」look like「系统超时没收到点击」.
    """

    status: str
    answer: str | None = None


@dataclass
class EscalationChannel:
    """Per-run wiring that lets a worker's ``escalate(blocking=true)`` suspend.

    Built by ``build_agent_executor`` for each delegated worker and ``None`` on the
    CEO / tests / unarmed turns (then ``escalate`` keeps its non-blocking behaviour).
    ``armed`` is the live-user gate (the SAME gate as ``ask_user`` — a live
    interactive client). ``request`` owns the mechanism the tool must stay clear of
    (引擎纯化): it enforces the concurrency cap, suspends on the interaction bridge,
    emits the ``escalation_required`` / ``escalation_resolved`` pair, records the
    resolution into the worker's ``RunState`` for CEO synthesis, and returns the
    :class:`EscalationOutcome`. The tool only decides WHETHER to block and maps the
    outcome to its ``ToolResult``.

    ``awaiting`` on ``request``: ``"user"`` (经典直挂用户) or ``"ceo"`` (协调模式等主管
    仲裁；初始不发用户可答卡，由 ``resolve_escalation`` 兑现).
    """

    armed: bool
    # ``request(question, assumption, questions, kind, awaiting="user",
    # browser_login=False)`` — trailing ``browser_login`` is optional for
    # backward-compatible mocks (defaults False on the production channel).
    request: Callable[..., Awaitable[EscalationOutcome]]


@dataclass(frozen=True)
class ToolSchema:
    """Tool metadata declaration for LLM function calling."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format
    category: ToolCategory
    approval: ToolApproval = ToolApproval.NEVER
    # Engine-level hard ceiling (seconds) for ONE call of this tool — a B1 backstop
    # so a wedged tool can't stall a whole turn. ``None`` ⇒ the engine applies a
    # per-category default (``runtime.engine.resolve_tool_timeout``); ORCHESTRATION
    # / INTERACTION tools are exempt (they legitimately wait minutes on sub-runs or
    # the user). Set explicitly only for a non-default ceiling. This is a coarse
    # safety net layered ABOVE a tool's own finer timeout (e.g. ``code_execute``
    # caps its sandbox itself), never a replacement for it.
    timeout_seconds: float | None = None


@dataclass
class TurnTargetDeskHint:
    """Turn-scoped soft default desk for bare-chat ``delegate`` (not session birth).

    ``create_folder`` / unique ``resolve_folder`` stamp a folder id onto the CEO
    :class:`ToolContext`. A second distinct id in the same turn clears the default
    so multi-folder fan-out still requires explicit ``target_folder_id``. Never
    rewrites conversation ``folder_id``.

    ``auto_cloud_provisioned``: runtime silently created a cloud desk this turn for
    bare-chat write tasks — at most once; never rewrites conversation ``folder_id``.
    Cross-turn reuse persists on ``Conversation.auto_desk_folder_id`` (orthogonal).
    """

    folder_id: str | None = None
    auto_cloud_provisioned: bool = False
    _seen: set[str] = field(default_factory=set, repr=False)

    def note_folder(self, folder_id: str | None) -> None:
        if not isinstance(folder_id, str):
            return
        cleaned = folder_id.strip()
        if not cleaned:
            return
        self._seen.add(cleaned)
        self.folder_id = cleaned if len(self._seen) == 1 else None


@dataclass
class TurnPromotionLedger:
    """回合归位台账 —— 交付对账快照 + 已归位的 ``{from,to}``（历史事件 / journal 重放）。

    **为何是 ToolContext 上的共享可变对象、而不是 ContextVar**：``execute_tools`` 用
    ``asyncio.gather`` 跑每个工具调用，gather 会复制 context —— 后台 drive 写的
    ContextVar 对 CEO 父任务不可见。``dataclasses.replace`` 浅拷贝仍指向同一个对象，
    所以整回合共用一本账（同 ``turn_target_desk`` / ``landed_artifact_kinds`` 的模式；
    ``coordination.session`` 的注释记着同一条坑）。逐回合 ``ToolContext.create``
    天然隔离并发回合。

    ``reconciliation`` = 本回合最近一次 ``delivery_status`` 载荷。``promotions`` =
    已归位行（journal 重放接手历史 ``{from,to}``），顺序即归位序。
    读写口径见 :mod:`agentcore.runtime.delegate.promotion`。

    ``delivery_verdict`` = 同一对象上的收口档位槽（``DeliveryVerdict | None``）。
    后台 drive 的 ContextVar ``set`` 写不回 CEO 父任务，必须挂在这本共享账上；
    禁止再造第三种通道、禁止改去查 turn_journal。
    """

    reconciliation: dict[str, Any] | None = None
    promotions: list[dict[str, str]] = field(default_factory=list)
    delivery_verdict: Any = None


@dataclass
class TurnProjectShell:
    """Empty-desk project-shell strip for one conversation turn.

    Shared by reference across ``replace()`` (CEO ↔ worker) and workspace-slot
    forks (``fork_workspace_slot`` only replaces ``_workspace``). First unknown
    top-level segment on a visibly empty desk is ``stripped_slug``; later write /
    read / mkdir / artifacts / write-claims strip that prefix for the turn.
    """

    stripped_slug: str | None = None


@dataclass
class RetrievalBudgetState:
    """Per-run ``web_search`` / ``read_url`` counter (提案 A1).

    Wired onto :class:`ToolContext` by the worker executor. ``used`` is reserved
    before a live call and refunded on cache hits / uncharged results so parallel
    tool_exec calls cannot overshoot ``limit``.

    ``used_by_tool`` splits the same shared pool by tool name — bookkeeping only,
    reserve / refund semantics are unchanged. Feeds the per-round budget-awareness
    injection and the 分工具用量分布 telemetry (下一阶段据此决定是否拆池). Mutated
    under the same lock as ``used`` so parallel calls keep ``∑ used_by_tool == used``.

    ``consecutive_empty_searches`` tracks live empty SERPs in this run so the
    search tool can require a strategy change after a streak (成篇质量定案).

    ``evidence_gap`` is a sticky run-scoped flag set when an academic_literature
    search marks a structured gap (junk / no preferred paper host) — delivery
    downgrade consumers may read it without scanning every tool event.
    """

    limit: int
    used: int = 0
    used_by_tool: dict[str, int] = field(default_factory=dict)
    consecutive_empty_searches: int = 0
    evidence_gap: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def searches_used(self) -> int:
        """Charged ``web_search`` calls (names mirror ``RETRIEVAL_TOOL_NAMES``,
        which lives in runtime.runs.retrieval_budget and imports this module)."""
        return self.used_by_tool.get("web_search", 0)

    @property
    def reads_used(self) -> int:
        """Charged ``read_url`` calls."""
        return self.used_by_tool.get("read_url", 0)

    def note_search_empty(self) -> int:
        """Record an empty SERP; return the new consecutive-empty streak."""
        self.consecutive_empty_searches += 1
        return self.consecutive_empty_searches

    def note_search_hit(self) -> None:
        """Reset empty streak after a non-empty injection."""
        self.consecutive_empty_searches = 0

    def note_evidence_gap(self) -> None:
        """Sticky-set academic literature evidence-gap (never clears mid-run)."""
        self.evidence_gap = True

    async def try_reserve(self, tool: str) -> bool:
        """Reserve one slot for ``tool``. False ⇒ exhausted (caller must not run it).

        ``tool`` only splits the ledger; the pool stays shared across retrieval tools.
        """
        async with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            self.used_by_tool[tool] = self.used_by_tool.get(tool, 0) + 1
            return True

    async def refund(self, tool: str) -> None:
        """Return a reserved slot (cache hit / uncharged call), split ledger included."""
        async with self._lock:
            if self.used > 0:
                self.used -= 1
            spent = self.used_by_tool.get(tool, 0)
            if spent > 1:
                self.used_by_tool[tool] = spent - 1
            elif spent == 1:
                del self.used_by_tool[tool]

    async def refill(self, extra: int) -> int:
        """Grant ``extra`` additional retrieval slots (contract rework slice).

        Raises the ``limit`` (not a used-reset) so prior charges stay honest while
        the rework pass gets a fresh slice. Returns the new remaining count.
        Prefer :meth:`refill_within_cap` for budget-bounded rework.
        """
        async with self._lock:
            add = max(0, int(extra))
            self.limit += add
            return max(0, self.limit - self.used)

    async def refill_within_cap(self, extra: int, *, cap: int) -> int:
        """Grant up to ``extra`` slots without raising ``limit`` above ``cap``.

        Contract rework must not bypass the original retrieval budget ceiling.
        When ``cap <= 0`` or there is no headroom, this is a no-op (returns current
        remaining). Returns the new remaining count.
        """
        async with self._lock:
            add = max(0, int(extra))
            add = min(add, max(0, int(cap) - self.limit)) if cap > 0 else 0
            self.limit += add
            return max(0, self.limit - self.used)


@dataclass
class ToolContext:
    """Context provided to tools during execution."""

    execution_id: str
    run_id: str
    agent_id: str
    user_id: str
    # Shared workspace root behind the ``backend`` / ``material_paths`` properties.
    # ``replace`` without ``_workspace=`` keeps following mid-turn rebinds; fork
    # paths pass a fresh :class:`WorkspaceSlot`. Build via :meth:`create` to seed it
    # from plain ``backend`` / ``material_paths`` arguments.
    _workspace: WorkspaceSlot = field(repr=False, compare=False)
    # The owning conversation, used by conversation-scoped tool state (e.g. the
    # read_url fetch cache, web/url_cache.py). Set once on the pipeline's base
    # context and inherited by every worker via ``dataclasses.replace``. Defaults
    # to "" for unscoped call sites (tests / evals) — a tool simply skips its
    # conversation-scoped optimisation when this is empty.
    conversation_id: str = ""
    # Session permission axes as JSON (PermissionAxes.to_dict()). Used by sandbox
    # network grading (P2: command=auto → restricted egress on cloud gVisor).
    permission_axes: str | None = None
    # 深度研究自治：会话旗标 + 本会话已自动开辩次数（cap 见 runtime.deep_research_auto）。
    # Set once on the pipeline base context; debate kickoff mutates the count in place
    # after a waived auto-adopt start so a same-turn second debate sees the cap.
    deep_research_auto: bool = False
    deep_research_auto_debate_count: int = 0
    # Intra-batch write-conflict guard (并行写隔离·硬约束). Set per delegated-worker
    # node by ``build_agent_executor``; ``None`` for the CEO / tests (no concurrent
    # siblings to coordinate, so ``file_write`` skips the check). ``write_ancestors`` is
    # this node's ``depends_on`` transitive closure ∪ nested ``parent_run_id``, so it
    # MAY overwrite a file owned by an upstream / lead it consolidates but not one a
    # concurrent sibling did.
    write_coordinator: WriteCoordinator | None = None
    write_ancestors: frozenset[str] = frozenset()
    # C3 desk×path ownership key: ``RunSpec.target_folder_id or session birth desk``.
    # ``None`` → ledger uses legacy sentinel (unit tests / bare stubs). Must match
    # declare-time desk so claim and dispatch reserve the same composite key.
    ownership_desk_id: str | None = None
    agent_role: str = ""
    # 升级实时可见 (escalation 实时 SSE): a run-scoped live channel for the worker-only
    # ``escalate`` tool to surface its escalation the INSTANT it is raised, called with
    # ``(question, assumption, blocking, kind)`` — kind is normal/scope/dep. Set per
    # delegated-worker node by ``build_agent_executor`` (it closes over the run's EventSink
    # + run/agent id to emit ``escalation_raised``); ``None`` for the CEO / tests — the tool
    # keeps working (escalate 非阻塞), the live banner is simply skipped, and the durable
    # record still rides the transcript into ``RunState.escalations``. A narrow callback
    # (not the EventSink itself) keeps tools off the event vocabulary — the executor owns
    # event shape (引擎纯化).
    on_escalate: Callable[[str, str, bool, str], None] | None = None
    # 阻塞式求决策 (escalate blocking=true): the per-run channel that suspends this worker
    # for the user when it hits a「只有用户能定、且猜错就作废」fork. Set per delegated-worker
    # node by ``build_agent_executor`` (closes over the interaction bridge + EventSink +
    # run/agent id); ``None`` for the CEO / tests / unarmed turns — then ``escalate`` stays
    # non-blocking (its existing behaviour). The tool owns the decision (whether to block,
    # the assumption fallback); this channel owns the mechanism (cap / suspend / events /
    # RunState recording) so the tool stays off the event vocabulary (引擎纯化).
    escalation: EscalationChannel | None = None
    # AI 协作白板 (AI协作白板.md §六 M2): the per-run channel that lets ``board_ops`` apply
    # structured ops to the user's open whiteboard canvas via the bound desktop. Set per
    # run by the assembler ONLY when the conversation is bound to a board (a 白板会话);
    # ``None`` for every ordinary chat / worker / test — then ``board_ops`` returns a clean
    # "not on a board" error instead of touching anything. The channel owns the mechanism
    # (suspend / emit / await the desktop); the tool owns only the op→result mapping (引擎纯化).
    board_channel: BoardChannel | None = None
    # Desktop Client Tools: per-run channel for ``desktop_notify`` + Host + MCP.
    # Set when the desktop client is online (local workspace **or** cloud +
    # ``desktop_online``) so tools can backfill via ClientTool SSE; ``None`` when
    # no desktop is attached. MCP stdio is fulfilled only on the desktop process.
    desktop_channel: DesktopClientChannel | None = None
    # Desktop-held workspace ops (terminal + language-service diagnostics): the
    # same ``workspace_op_required`` channel LocalWorkspace already uses for
    # file/execute. Reused from LocalWorkspace when present; for sidecar
    # (ServerWorkspace location=local) a channel is built so process ops and
    # ``diagnostics`` still leave the short-lived sidecar and land in the
    # desktop main process. ``None`` on cloud-only runs.
    workspace_channel: WorkspaceChannel | None = None
    # AI 协作白板 / 对话读图: optional vision port (``board_read`` / attachment
    # eye→text). Wired by ``resolve_vision_reader_for_conversation``: vision slot,
    # else image-accepting main credentials, else platform ``VISION_*``; ``None`` ⇒
    # clean「读图能力未配置」. CEO context only (not workers).
    vision_reader: VisionReader | None = None
    # Turn-level sink for priced ``role=vision`` ledger rows (board_read + conversation
    # image attachments). Shared by every derived run via ``replace`` (list by reference).
    # ``None`` in tests / paths with no vision billing.
    cost_sink: list[RunCost] | None = None
    # 项目共享工作区 (folder 绑定): True ⇒ CEO overview / worker manifest 用稀疏清单
    # (附件 + 少量最近触达 + 「另有 N 个」)；False ⇒ 裸聊 scratch，非附件文件照常列入。
    # Set on the pipeline base context from ``folder_id``; inherited by workers via
    # ``dataclasses.replace``. Defaults False for tests / evals / 裸聊.
    shared_workspace: bool = False
    # Turn attachment block (prepare bake). ``apply_target_desktop`` reuses it when
    # rebuilding a worker prompt for another desk. Empty on resume / tests.
    attachment_context: str = ""
    # 工具执行阶段进度 (联网搜索前端展示优化): a narrow callback a long-running tool fires to report
    # a coarse EXECUTION phase (web_search → "querying" 正在检索 / "queued" 排队中 / "fallback"
    # 改用备用引擎) so the waiting UI shows a live, honest state instead of a dead spinner. Called
    # with the phase token ONLY; the executor (``execute_tools``) owns event shape — it closes over
    # this call's tool_call_id / tool_name / run_id and emits the
    # transport-only ``tool_use_progress``
    # (引擎纯化, exactly like ``on_escalate``). ``None`` for call sites without a live
    # sink (tests / evals) — the tool simply skips the ping.
    on_phase: Callable[[str], None] | None = None
    # 工具执行流式进度 (code_execute 前端展示优化): a narrow callback a long-running tool fires
    # to push incremental output (``code_execute`` → phase ``"output"`` + ``{stream, chunk}``) so
    # the waiting UI shows live stdout/stderr instead of a bare spinner. Called with ``(phase, data)``
    # where ``data`` is an optional dict merged into the transport-only ``tool_use_progress`` payload
    # by the executor (引擎纯化, twin of ``on_phase``). ``None`` for call sites without a live sink
    # (tests / evals) — the tool simply skips the ping.
    on_progress: Callable[[str, dict[str, Any] | None], None] | None = None
    # 检索预算 (提案 A1): per-run counter for ``web_search`` / ``read_url``. Wired by the
    # worker executor from ``RunSpec.retrieval_budget``; ``None`` for CEO / tests without a
    # budget (no enforcement). Shared by reference across ``replace`` so parallel tool
    # calls in one run share one reserve/refund lock. Orthogonal to LoopController.
    retrieval_budget: RetrievalBudgetState | None = None
    # Per-run web_search posture (structured run signal — never prompt text).
    # ``""`` = default research; ``"debate_evidence"`` = debate investigator / debater
    # speech research (reject weak-tier + mall/dict/hospital-encyclopedia);
    # ``"academic_literature"`` = research_report literature posture (prefer
    # paper/DOI hosts, demote encyclopedia/dict/portal, stamp evidence_gap).
    # Wired from ``RunSpec.search_policy`` by the worker executor.
    search_policy: str = ""
    # ``""`` = outer verify allowed; ``"inner"`` = diagnose/review posture — refuse
    # full typecheck/build on ``test_run`` (use code_diagnostics / browser). Wired
    # from ``RunSpec.verify_policy``.
    verify_policy: str = ""
    # Same-round streamed prose length (chars) before tool calls. Set by tool_round so
    # ``handoff`` can log ``body_chars`` (deliverable) separately from ``chars`` (summary).
    # ``None`` when unset (CEO / tests / tools that do not need it).
    round_content_chars: int | None = None
    # 成篇交接：有下游依赖时 handoff 须带可消费交付（非空正文或已落盘 prose）；
    # 由 worker executor 按 DAG 写入。False/默认 = 叶节点或不强制。
    handoff_requires_body: bool = False
    # 有下游时正文地板字数；生产恒为 0（仅要求非空）。禁止从已删字段回填或发明地板。
    handoff_min_body_chars: int = 0
    # ``deliverable.form``（``prose`` / ``files`` / None）。有下游 + prose 时禁止
    # 用 summary 升格冒充交接地板正文；其它 form 仍可升格。
    handoff_deliverable_form: str | None = None
    # True when this run already landed at least one file (file_write / append /
    # str_replace) on the *current* ToolContext object. Best-effort same-ctx
    # signal only — ``dataclasses.replace`` drops this bool. Handoff / executor
    # body-floor exemption must read ``landed_artifact_kinds`` (prose) instead.
    has_landed_files: bool = False
    # Artifact-first Writing：本 execution 已落盘 path → ``skeleton`` | ``prose``（共享可变
    # dict；``dataclasses.replace`` 浅拷贝）。``prose`` = 成篇
    # 正文，同 path 后续 ``file_append`` 硬拒。配 ``landed_artifact_authors``：首次落盘
    # 该 path 的 ``agent_id``（归属/可观测）。
    landed_artifact_kinds: dict[str, Literal["skeleton", "prose"]] = field(
        default_factory=dict
    )
    # path → 首次落盘该 path 的 ``agent_id``（共享可变 dict，与 kinds 同生命周期）。
    landed_artifact_authors: dict[str, str] = field(default_factory=dict)
    # 冷启动探索幕未完成 + 写盘范围。放在共享盒里：引擎 ``replace(on_phase=…)``
    # 拷的是引用，``update_folder_profile`` 翻转后 ``delegate`` 在 pipeline base
    # 上立刻看见。bool 字段会被 replace 按值拷走，写在副本上正本仍 pending。
    # Worker 要不同 write_scope 时必须 :func:`fork_explore_write_scope` 换新盒，
    # 禁止 ``replace(..., write_scope=)``（那不再是字段）。
    _explore_gate: TurnExploreGate = field(
        default_factory=TurnExploreGate, repr=False, compare=False
    )
    # Sidecar/desktop-injected Folder local bind for explore workspace_key
    # (RPC ``localRootId`` / ``localSubpath``, same camelCase shape as ``folderId``).
    # ``folder_binding_injected`` True ⇒ assemble must not open PG for key resolve
    # (``folder_local_root_id`` None = cloud / unbound). False ⇒ DB fallback / degrade.
    folder_binding_injected: bool = False
    folder_local_root_id: str | None = None
    folder_local_subpath: str | None = None
    # 裸聊同回合先建/解析后的软默认目标桌（共享可变；``replace`` 浅拷贝同引用）。
    # 仅缺省 ``delegate`` 目标时消费；多 id 同回合清空。≠ 会话出生 ``folder_id``。
    turn_target_desk: TurnTargetDeskHint = field(default_factory=TurnTargetDeskHint)
    # 历史归位重放 + delivery_verdict 槽（共享可变；``replace`` 浅拷贝同引用）。
    promotion_ledger: TurnPromotionLedger = field(default_factory=TurnPromotionLedger)
    # 空桌工程壳剥段（共享可变；``replace`` / workspace fork 同引用）——见 ``TurnProjectShell``。
    project_shell: TurnProjectShell = field(default_factory=TurnProjectShell)
    # Bare-chat landing write desk (``Conversation.auto_desk_folder_id``). Orthogonal
    # to birth ``folder_id`` / sidebar / memory. When set, CEO file tools + overview
    # sit on this Folder while affiliation stays 裸聊. Never auto-promote.
    auto_desk_folder_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        backend: WorkspaceBackend,
        material_paths: frozenset[str] | None = None,
        **fields: Any,
    ) -> ToolContext:
        """Build a context owning a fresh :class:`WorkspaceSlot`."""
        write_scope = fields.pop("write_scope", None)
        pending = fields.pop("cold_start_explore_pending", None)
        if "_explore_gate" not in fields:
            gate = TurnExploreGate()
            if pending is not None:
                gate.pending = bool(pending)
            if write_scope is not None:
                gate.write_scope = (
                    write_scope
                    if write_scope in ("none", "explore_memory", "project")
                    else "project"
                )
            fields["_explore_gate"] = gate
        return cls(
            _workspace=WorkspaceSlot(
                backend=backend,
                material_paths=frozenset() if material_paths is None else material_paths,
            ),
            **fields,
        )

    @property
    def backend(self) -> WorkspaceBackend:
        return self._workspace.backend

    @backend.setter
    def backend(self, value: WorkspaceBackend) -> None:
        self._workspace.backend = value
        self._workspace.desk_visibly_empty = None

    @property
    def material_paths(self) -> frozenset[str]:
        return self._workspace.material_paths

    @material_paths.setter
    def material_paths(self, value: frozenset[str]) -> None:
        self._workspace.material_paths = value

    @property
    def cold_start_explore_pending(self) -> bool:
        return self._explore_gate.pending

    @cold_start_explore_pending.setter
    def cold_start_explore_pending(self, value: bool) -> None:
        self._explore_gate.pending = bool(value)

    @property
    def write_scope(self) -> WriteScope:
        return self._explore_gate.write_scope

    @write_scope.setter
    def write_scope(self, value: WriteScope) -> None:
        self._explore_gate.write_scope = value

    @property
    def turn_created_folder_ids(self) -> frozenset[str]:
        return frozenset(self._explore_gate.turn_created_folder_ids)

    def note_turn_created_folder(self, folder_id: str | None) -> None:
        """Record a cloud folder minted this turn (create_folder / first auto-desk)."""
        if not isinstance(folder_id, str):
            return
        cleaned = folder_id.strip()
        if cleaned:
            self._explore_gate.turn_created_folder_ids.add(cleaned)


@dataclass
class ToolResult:
    """Result of a tool execution.

    ``effect`` steers the ReAct loop and is the ONLY signal the engine acts on to
    decide whether the turn continues — never the tool's name or category (引擎纯化,
    设计 §8.5). The default ``ToolEffect.CONTINUE`` feeds ``output`` back to the
    model and loops; a terminal effect (``HANDOFF`` / ``INTERACT``) stops the loop
    because the tool already produced the turn's final user-facing answer, carried
    in ``final_text`` (so the model does not generate a second, duplicate reply).
    ``ask_user`` stop / timeout and team_preview cancel / timeout feed ``CONTINUE``
    so the CEO sees the拒答 or unanswered card and may short-close; ``delegate``
    likewise stays ``CONTINUE``
    (workers' products return to the CEO loop). ``final_text`` (when a terminal
    effect sets it) is persisted but NOT re-emitted and is exempt from ``output``
    truncation (which only guards the model-facing ``output`` string).

    ``output_limit`` overrides the default model-facing truncation budget for the
    ``output`` string. Most tools leave it ``None`` (4000 chars); read-heavy tools
    (e.g. ``read_url``) raise it so a full page body is not truncated into invalid
    JSON. An over-budget output is HEAD+TAIL trimmed (``core.text.truncate_head_tail``)
    so trailing details survive rather than being head-chopped. ``final_text`` is never
    subject to this cap.

    ``citations`` carries structured web sources a tool consulted (each a
    ``{url, title, snippet, site}`` dict). Research tools (``web_search`` /
    ``read_url``) populate it so the engine can aggregate per-turn sources and the
    client can render source cards under the answer; non-web tools leave it
    ``None``. The dicts themselves are UI metadata; the engine additionally
    assigns each source a canonical number (its card index) and folds *that
    number* back into the tool's model-facing output, so the model can cite by a
    card-aligned number (see ``engine._annotate_tool_citations``).

    ``audience`` is stamped at production: ``ceo`` means this ``output`` is
    orchestration text for the captain loop (coordination start/merge echo, host
    rejects). User-bubble writers (captain salvage) refuse it. Blocking delegate
    synthesis stays default ``user`` so salvage can still reuse a real deliverable.

    ``display`` is an OPTIONAL render-oriented payload, distinct from the
    model-facing ``output`` string: a tool that has a richer client rendering than
    plain text (``web_search`` → result cards, ``read_url`` → source card + body
    preview, ``code_execute`` → a terminal stdout/stderr view) populates it, and
    the desktop renders per tool — falling back to the ``output`` text when
    absent (工具结果富渲染). It rides the ``tool_use_end`` event → the process
    timeline / journal → the client (size-capped on the way,
    ``events._cap_display``), so a live turn and its reloaded twin render the
    same card. 形状是数据不是模式: the frontend keys the renderer off the tool
    name, so ``display`` is just the data that name's view needs (most tools
    leave it ``None``; edits like ``str_replace`` need nothing here — the client
    derives their diff from the call ``arguments`` it already has).
    """

    tool_call_id: str
    success: bool
    output: str
    error: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    effect: ToolEffect = ToolEffect.CONTINUE
    final_text: str | None = None
    output_limit: int | None = None
    citations: list[dict[str, Any]] | None = None
    display: dict[str, Any] | None = None
    # 落盘产物自报 (交付物台账事实口径 · agentcore.tools.file_products): a tool that
    # LANDED files declares them here — ``{path, kind, derived_from?}`` per product,
    # with ``path`` as the path it actually wrote (post-sanitize), not the requested
    # one. The engine stamps them onto the transcript message as a machine marker at
    # its single tool choke point, so ``files_touched`` / ``file_acceptance`` read
    # facts off the RESULT instead of guessing producers from call arguments. Report
    # only what really landed: a write that failed reports nothing (so failed / denied
    # calls stay out of the ledger), while a script that exited non-zero AFTER copying
    # files out still reports them. Tools that write nothing leave it empty.
    file_products: list[FileProduct] = field(default_factory=list)
    # Optional user-facing failure face (``tool_use_end.failure``). Most tools leave these
    # None — the engine's central mapper emits curated Chinese by stable code. Only set when
    # a tool truly needs product copy distinct from the model-facing ``error``/``output``.
    # Never put ``str(exc)`` / internal tokens here.
    failure_message: str | None = None
    failure_code: str | None = None
    # 参数契约拒绝 (零成本可修正的参数打回): a deterministic argument-contract rejection at
    # the tool boundary (e.g. web_search A3 query 过长/过多) whose ``error`` already carries a
    # concrete fix hint. It is an honest failed call for the model, but must NOT feed the
    # run-scoped tool-failure circuit breaker (warn/disable) — a research worker that fans out
    # several over-long queries in one round would otherwise burn the disable threshold before
    # ever seeing the「改用 2–4 个核心词重试」tip and permanently lose the tool. The engine
    # forwards this onto the ToolAttempt (loop_controller), which skips the cumulative breaker
    # tally; every other governance signal (REPEATED_FAILURE window, unproductive early-stop,
    # round recording) still treats it as a normal failure. Twin of ``ToolAttempt.policy_failure``
    # (upstream policy/environment block) but for a self-correctable参数打回, not a refusal.
    contract_failure: bool = False
    audience: ToolAudience = TOOL_AUDIENCE_USER

    _MAX_OUTPUT_LEN = 4000

    @property
    def is_terminal(self) -> bool:
        """Whether this result ends the turn (any non-``CONTINUE`` effect)."""
        return self.effect is not ToolEffect.CONTINUE

    def __post_init__(self):
        limit = self.output_limit if self.output_limit is not None else self._MAX_OUTPUT_LEN
        if len(self.output) > limit:
            # HEAD+TAIL, not a head-only chop: an agentic CEO leans on grep / file_read,
            # whose hits / numbers / 法条编号 often sit at the END — a head cut drops them
            # silently. Same primitive the dep-injection / compaction paths already use.
            self.output = truncate_head_tail(self.output, limit)


class Tool(Protocol):
    """Unified protocol for tool implementations."""

    @property
    def schema(self) -> ToolSchema:
        """Return tool metadata (name, description, parameters JSON Schema)."""
        ...

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with given arguments and context."""
        ...


def tool_schema_to_openai_format(schema: ToolSchema) -> dict:
    """Convert a ToolSchema to the OpenAI function calling format."""
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": schema.parameters,
        },
    }
