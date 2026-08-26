"""Run model type core (统一 Run 模型 第一阶段) — the typed substrate the scheduler
and executor build on.

A turn holds one *tree* of Runs: the ``CAPTAIN`` root is the CEO chat loop (the
reply engine); every delegated worker / DAG node is an ``AGENT`` child. This module
fixes the *shape* of a Run
— its spec (:class:`RunSpec`) +
node policy (:class:`RunPolicy`) + live state (:class:`RunState`) — and the phases
it moves through (:class:`RunPhase`). The plan that holds nodes lives in
``runs.plan``; the scheduler that drives them in ``runs.wave``.

第一阶段范围：worker 以「内联角色」声明（无独立 Agent 实体），因此 ``RunSpec`` 直接携带
角色/目标/工具等执行所需字段；``agent_id`` 在本阶段即等于 ``run_id``（事件与图
节点标识沿用），``agent_name`` 取角色名做展示。

→ 见设计: docs/03-AI核心/执行引擎架构设计.md §八（Run 模型）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agentcore.llm.provider.protocol import LLMMessage
    from agentcore.runtime.events import FinishReason


class RunKind(StrEnum):
    """What a Run node *is*.

    The scheduler treats every kind uniformly; the kind only selects which
    executor and policy defaults apply when the node runs. A turn is one Run tree:
    the ``CAPTAIN`` root is the CEO chat loop itself (it owns the conversation
    voice and may ``delegate``); every delegated worker / DAG step is an ``AGENT``
    child.

    无独立 ARENA / debate kind：多轮辩论是带 stance/round 展示标记的普通 AGENT DAG——
    守「形状是数据不是模式」，不另立节点种类。
    """

    CAPTAIN = "captain"  # the turn's root run: the CEO chat loop (owns the reply)
    AGENT = "agent"  # a delegated / DAG-step worker run


class RunPhase(StrEnum):
    """A Run's lifecycle phase."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


# A run that reached one of these is done; the scheduler advances past it and the
# wave it sits in is complete once all its nodes are terminal.
# 查询入口（与 FinishReason / TurnOutcome / 关帧集合对照）→ runtime.terminal
TERMINAL_PHASES = frozenset(
    {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.SKIPPED, RunPhase.CANCELLED}
)


class RunOrigin(StrEnum):
    """Where a plan's nodes came from.

    ``TEMPLATE`` = built up-front from the delegate args; ``CAPTAIN`` = appended
    at runtime by a captain via ``delegate`` (阶段2 adaptive case).
    """

    TEMPLATE = "template"
    CAPTAIN = "captain"


@dataclass
class Deliverable:
    """一个 node 的完整交付物规格——描述性 + 约束性合一。"""

    output_format: str = "text"
    required_sections: list[str] = field(default_factory=list)
    # Structured deliverable form (always set after parse): ``prose`` = text body
    # only; ``files`` = land documents (default ``工作稿/``); ``workspace`` = edit
    # the user project tree in place (swallows ``workspace_native``; no dossier
    # landing). Missing / empty / invalid form → ``files``. Write-disk recognition
    # = ``files`` ∪ ``workspace`` ∪ non-empty ``artifacts``.
    form: Literal["prose", "files", "workspace"] = "files"
    # Declarative artifact path list (files / dirs / globs). When non-empty, the
    # contract gate reconciles each pattern against the live workspace (existence),
    # and a batch that declares any artifacts auto-enables completion acceptance.
    # Omit = no path enforcement.
    artifacts: list[str] = field(default_factory=list)
    # Dossier landing directory (workspace-relative, no trailing slash). Runtime may
    # fill from ``stage_dirs`` when form=files / files_written is dossier-semantic;
    # worker picks filenames under this dir. Acceptance: empty artifacts →
    # prefix-match this dir; non-empty artifacts → exact / trailing-/ / glob on
    # those paths only (this field is not a fallback).
    artifact_dir: str = ""
    # Playbook-internal alias swallowed into ``form=workspace`` at parse
    # (``form=workspace`` ⇔ no dossier landing). Leftover ``artifact_dir`` must
    # not pull a workspace node into ``工作稿/``. Direct constructions may still
    # set this; ``is_workspace_landing`` treats it as workspace.
    workspace_native: bool = False
    # When set (e.g. ``site/``), the contract gate cross-checks HTML↔CSS↔JS seams
    # across ALL web files under this workspace prefix — not only this run's batch.
    # Used by whole-page QA to catch integrated orphan selectors after parallel
    # section workers each patch a subset of files.
    web_seam_scope: str = ""
    # Placeholder hard-signal exemption (internal coordination docs): when True, every
    # content artifact in this run's contract check skips hard placeholder failures
    # (soft warnings remain). ``placeholder_hard_exempt_artifacts`` narrows exemption
    # to declared paths when the bool is False. Declared on playbook nodes — never
    # hardcode paths in placeholder_scan.
    placeholder_hard_exempt: bool = False
    placeholder_hard_exempt_artifacts: list[str] = field(default_factory=list)
    # DESIGN.md contract for static web quality (style id / tokens). Landed
    # HTML/CSS/JS/SVG always runs syntax / fake-contact / anti-slop; this flag
    # only gates the DESIGN.md hard checks and DESIGN prompt injection.
    # Separate from placeholder_scan / web_seam. ``visual_critic`` stays opt-in.
    web_quality_scan: bool = False
    # Skip soft anti-slop only (hard syntax / fake contacts still apply).
    web_quality_soft_exempt: bool = False
    # Skip named soft rules when the user explicitly asked for that style.
    web_quality_soft_exempt_labels: list[str] = field(default_factory=list)
    # P1c visual critic (screenshot → VisionReader): opt-in via deliverable ``visual_critic``.
    # Runs after web_quality hard; missing browser/vision ⇒ 未目验 (never fake pass).
    # Critical findings → up to 2 contract reworks, then partial warnings.
    visual_critic: bool = False
    strict: bool = False
    # 调研类两阶段引用验收（块 2）：``two_phase`` = 广搜落盘为 draft（A，不跑成稿
    # 引用闸 / 不因 cite 重试）→ 同 worker 自动升级 B（deep_read 或无编号综述）后再跑
    # 现有引用闸；过 → accepted，不过 → rejected(citations_unverified)。
    # 省略 / None = 现状（每次合同检查都跑引用闸）。draft 仅内部态，不进
    # ``delivery_status.artifacts`` / ``delivered_files``。
    citation_mode: Literal["two_phase"] | None = None
    # ``code_audit`` 报告结构闸（L2b）：验配套 ``*.audit.json`` 字段语义
    # （未读全不得中+、高须触发路径等）。与成篇审计硬门正交。
    code_audit_gate: bool = False

    def __post_init__(self) -> None:
        if self.form not in DELIVERABLE_FORMS:
            self.form = "workspace" if self.workspace_native else "files"
        if self.form == "prose":
            self.workspace_native = False
        elif self.form == "workspace" or self.workspace_native:
            self.form = "workspace"
            self.workspace_native = True


RunContract = Deliverable

DELIVERABLE_FORMS = frozenset({"prose", "files", "workspace"})
LANDING_FORMS = frozenset({"files", "workspace"})


def normalize_deliverable_form(
    form: object,
    *,
    workspace_native: bool = False,
) -> Literal["prose", "files", "workspace"]:
    """Map raw form + native stamp to the three-tier form.

    ``prose`` stays prose (native is cleared by the caller). Explicit
    ``workspace`` or ``workspace_native`` (non-prose) → ``workspace``.
    Missing / invalid → ``files``.
    """
    if form == "prose":
        return "prose"
    if form == "workspace" or workspace_native:
        return "workspace"
    return "files"


def is_workspace_landing(deliverable: Deliverable | None) -> bool:
    """True when the node has no agreed dossier landing (form=workspace / native)."""
    if deliverable is None:
        return False
    return deliverable.form == "workspace" or deliverable.workspace_native


def deliverable_expects_landing(deliverable: Deliverable | None) -> bool:
    """Expected on-disk landing: files ∪ workspace ∪ non-empty artifacts.

    ``None`` (legacy serialized specs) is not a landing node. Parsed plan nodes
    always carry a Deliverable; omitted form is ``files``.
    """
    if deliverable is None:
        return False
    if deliverable.form == "prose":
        return False
    return deliverable.form in LANDING_FORMS or bool(deliverable.artifacts)


def raw_deliverable_expects_landing(raw: object) -> bool:
    """Same landing predicate on a CEO/playbook task dict (before parse).

    No object / empty object / omitted form → files (must write). Only explicit
    ``form=prose`` is exempt. Does not scan ``task`` free text.
    """
    if not isinstance(raw, dict):
        return True
    form = raw.get("form")
    if form == "prose":
        return False
    if form in LANDING_FORMS:
        return True
    arts = raw.get("artifacts")
    if isinstance(arts, list) and any(isinstance(a, str) and a.strip() for a in arts):
        return True
    if bool(raw.get("workspace_native", False)):
        return True
    # 漏填 / 非法 form / 空对象 → files
    return True


@dataclass
class RunPolicy:
    """Node-level policy slots.

    实际生效的：``on_failure``（Wave 只读它做级联：skip/abort/degrade/
    retry 的「失败后怎么对待下游」——retry 不整节点重跑）；``timeout_s``；
    ``result_handling``（执行器拼装下游上下文）。
    """

    # on_failure (Wave enacts cascade only; a transient does not remount):
    #   retry   = record FAILED (one run_failed) then cascade-skip
    #             dependents (same cascade as skip — CEO replaces via replaces_run_id);
    #   skip    = cascade-skip every dependent (they never run unless revived);
    #   abort   = stop scheduling further waves;
    #   degrade = record failed, let dependents proceed (they see the gap).
    on_failure: Literal["abort", "skip", "degrade", "retry"] = "retry"
    timeout_s: int | None = None
    # Fidelity of this node's output when it feeds a dependent node (pass_through
    # / summarize). The executor reads this to size the upstream context block.
    result_handling: str = "pass_through"


@dataclass
class RunSpec:
    """The declared identity + dependencies + policy of one Run node.

    Immutable plan data. 第一阶段「内联角色」：worker 的角色/目标/工具直接挂在
    这里（无 Agent 实体）；``agent_id`` 由 builder 铸成 == ``run_id``，``agent_name``
    取角色名，仅用于 ``run_*`` 事件与图节点展示。``wave`` 不在此声明，由
    :meth:`RunPlan.waves` 拓扑推导。
    """

    run_id: str
    task: str
    kind: RunKind = RunKind.AGENT
    # ── identity / display (阶段1: agent_id == run_id, agent_name == role) ──
    agent_id: str = ""
    agent_name: str = ""
    # ── 内联 worker 定义（阶段1：替代独立 Agent 实体） ──
    role: str = ""
    system_prompt_supplement: str | None = None
    # 辩手两阶段发言（辩论编排设计 §4-2.5）：True → ReAct 检索产证据笔记（不进卡片正文），
    # 再以 draft_system + draft_brief + 笔记做无工具干净成稿（流式进 run_output_delta）。
    # 普通 worker 默认 False；执行器读取此执行策略字段（非 display-only 的 stance/group）。
    research_then_draft: bool = False
    draft_brief: str = ""
    draft_system: str = ""
    # 证据台账 id 闸（仅辩手两阶段成稿）：成稿【已核实·#eN】须 ∈ 场级台账。
    # 普通 worker 默认 False。台账对象经 AgentExecutorEnv / continue_run 注入，不进 RunSpec。
    evidence_ledger_check: bool = False
    # 辩论方键（登记 evidence_ledger.side_key）；非辩手恒空。
    side_key: str = ""
    # 真纯丙：历史上曾作 allow-list；builder 现忽略入参 tools，executor 亦不再用本字段
    # 收窄（``allowed_tools=None``）。字段保留兼容旧 session / 序列化；新派发恒为 ``None``。
    tools: list[str] | None = None
    # Explicit per-node model override (辩论辩手 / per-worker 目录身份编成的路由键).
    # Empty = resolve from the turn's ProfileSet Worker 槽 (ordinary default). When set,
    # the executor replaces only the resolved profile's ``model`` and dispatches through
    # the turn's ProviderRouter (``platform/{id}`` or ``{provider_id}/{id}``). Pricing
    # strips router prefixes. → runtime/delegate/task_models.py
    model: str = ""
    thinking: bool | None = None
    deliverable: Deliverable | None = None
    # ── 辩论/审查 呈现标记（前端UX设计.md §四，display-only） ──
    # An opposing-batch's display tags: ``stance`` is this node's side (pro/con),
    # ``group`` pairs opposing nodes into one comparison, and ``round`` is its
    # multi-round-debate turn number (1-based; 0 = not a multi-round debate). The
    # scheduler/executor NEVER read these — they ride RunSpec → run_plan → the
    # frontend, which renders tagged runs side-by-side under a「辩论」title and lays
    # multi-round debates out round-by-round. Empty/0 for ordinary parallel/DAG
    # work, so 守住「形状是数据不是模式」: a debate is普通并行 DAG + presentation hints.
    # (Note: ``round`` ≠ ``rounds`` below — the latter counts thinking-text segments.)
    stance: str = ""
    group: str = ""
    round: int = 0
    # ── topology / governance ──
    depends_on: list[str] = field(default_factory=list)
    # Plan-time structured-suspend marker (结构化挂起 2a): when True, the
    # WaveScheduler pauses *after* this node completes and *before* its dependents
    # run, awaiting a user plan_review (continue / stop) over the unified
    # interaction bridge — the one thing a CEO ``ask_user`` cannot express, since a
    # ``delegate`` is atomic to the CEO (it gets no wave-boundary control). Inert by
    # default and whenever the scheduler is driven without an ``on_boundary`` hook
    # (autonomous jobs / tests), so a plan with no checkpoint marks runs byte-for-
    # byte as before. → 见设计: docs/03-AI核心/执行引擎架构设计.md §检查点决策语义
    checkpoint_after: bool = False
    # 晚绑定标记（受监督的波循环）：为 True 的节点其 spec 关键
    # 字段（task/role/tools…）可先占位，依赖完成后由 CEO 在波边界经 ``replan`` 定稿再
    # dispatch——WaveScheduler 把「依赖已完成但本节点未定稿」当一个决策边界、YIELD 回 CEO
    # 的 ReAct 主循环（区别于 checkpoint_after 的「让给用户 plan_review」）。Inert by
    # default：未接 on_boundary 的调度（自治 / 测试）下完全无效，故一个无晚
    # 绑定节点的 plan 行为逐字不变。→ 见设计: docs/03-AI核心/执行引擎架构设计.md §受监督的波循环
    bind_after_deps: bool = False
    parent_run_id: str | None = None
    # Tree position — also the SOLE determinant of whether this worker may nest a
    # sub-team (阶段2 嵌套子任务). Any worker with ``depth < MAX_DELEGATION_DEPTH``
    # gets lead identity + ``delegate``+``replan`` (delegation is on by default;
    # the executor enforces the cap); workers at the cap are always leaves. There
    # is no per-node opt-in/opt-out flag.
    depth: int = 0
    # 回落换人 / 协调补派 (多轮编排 P-3): the failed (or cancelled) run this node
    # takes over. Graph shows「接手」/「接替」; ``RunPlan.add`` also rewrites other
    # nodes' ``depends_on`` that named the old id so downstream waits on this run.
    replaces_run_id: str | None = None
    # CEO explicit force-continue: when True, this node may run even with
    # ZERO successful upstreams (e.g. sole upstream CANCELLED). Orthogonal to
    # the default lenient fan-in (≥1 success → run) and to ``require_upstream``.
    force_continue: bool = False
    # Turn delivery reserve: when spent enters ``engine_turn_token_delivery_reserve``
    # window, WaveScheduler still admits these nodes and soft-skips ready non-priority
    # peers so lenient fan-in can run the acceptance tail (assemble+QA).
    ceiling_priority: bool = False
    # Wave3 B：开局从工作区注入这些相对路径的截断正文（契约/设计摘要），
    # 减少分区 worker 对同文件的反复 file_read。空 = 不注入。
    context_inject_files: list[str] = field(default_factory=list)
    # Strict fan-in: when True, every upstream must succeed (FAILED with
    # on_failure∈{skip, retry} or CANCELLED without force_continue → cascade-skip),
    # restoring the pre-lenient cascade. Default False = ≥1 upstream COMPLETED
    # is enough; absent upstreams are annotated in the worker's dependency input.
    require_upstream: bool = False
    # 同人续派：目标 run 的现场根（RunSession 键）。设了则执行走 continue_run 而非冷开局；
    # wire 的 continues_run_id 恒等于该值（星型）。校验与闸在驱动层，调度器不读。
    continue_from_run_id: str | None = None
    # 检索预算（提案 A1）：本 run ``web_search``+``read_url`` 合计次数上限。
    # ``None`` = 未解析（手工构造的 spec / 测试）；经 ``build_run_plan`` /
    # ``apply_retrieval_budgets`` 后恒为 ``>=0`` 的 int（全员统一默认；CEO 不可配置）。
    # 辩手有约定文档等内部 writer 可在 apply 后覆写为窄例外常量。
    # Enforce 在 engine ``tool_exec``（有 run 身份处），与 LoopController 正交。
    retrieval_budget: int | None = None
    # Per-run web_search posture (结构化信号，禁止靠 prompt 触发)。
    # ``""`` = 默认调研；``"debate_evidence"`` = 庭前取证员 / 辩手 speech research
    # （weak 档与商城/词典/医院百科硬剔）；``"academic_literature"`` = 成文综述
    # （偏论文/DOI、降权百科词典门户、junk 戳 evidence_gap）。经 task payload →
    # builder → ToolContext。
    search_policy: str = ""
    # Per-run verify posture (结构化信号，禁止靠 task 文案猜)。
    # ``""`` = 默认可跑外环 test_run；``"inner"`` = 调查/审查姿态：禁全仓
    # typecheck/build（改用 code_diagnostics / browser；验收员外环另派）。
    # Builder 对审查类角色默认回填；CEO 可显式传 ``outer`` 覆盖。
    verify_policy: str = ""
    # Worker 累计 token 硬顶（统一 backstop）：``None`` = 未解析（手工 / 测试 → 执行器
    # 回落 ``settings.engine_worker_token_ceiling``）；经 ``apply_worker_budgets`` 后为
    # 显式回填值。辩论 ``research_then_draft`` 与普通 worker 共用此顶。
    token_ceiling: int | None = None
    # Optional per-node ReAct round cap (repair / light posture). ``None`` = use
    # the agent profile default (80). Stamped by builder for light / repair_code.
    max_rounds: int | None = None
    policy: RunPolicy = field(default_factory=RunPolicy)
    # Fan-out awareness: a concise list of the *other* nodes that fanned out from
    # the same point — those sharing this node's exact ``depends_on`` set, i.e. the
    # peers it runs in parallel with toward the same juncture (never its own
    # upstream/downstream, which arrive separately via ``depends_on``). Injected into
    # the worker's child context so parallel siblings coordinate instead of
    # overlapping. Populated by ``build_run_plan`` for BOTH a flat parallel batch
    # (all share the empty dep set → all siblings) and a DAG (a「research → writer」
    # fan-out's parallel researchers share their deps → see each other). Narrower
    # than「same wave」on purpose: independent chains that coincidentally share a
    # topological layer are NOT siblings. A node with no same-fan-out peer (a
    # pipeline link, a lone writer) leaves it blank.
    sibling_summary: str = ""
    # plan_review CONTINUE：主 Agent llm 把关压缩要点（REPLACE，非 append）。
    # 与 ``steer`` 分通道；渲染在 steer 之前。空 = 无 / deterministic 不下发。
    gate_notes: str = ""
    # Mid-course user steer (结构化挂起 adjust): the note the user gave at a
    # plan_review checkpoint with the ``adjust`` decision, injected by the host hook
    # onto the checkpoint's not-yet-run (transitive) dependents — exactly the work
    # building on the reviewed output, not unrelated parallel branches — so the steer
    # redirects the remaining work (the executor renders it as a high-priority
    # instruction block). Empty for plan-time specs and for ``continue`` / ``stop``;
    # accumulates (one block per adjust) when a node is steered across multiple
    # checkpoints before it runs. → 见设计: docs/03-AI核心/执行引擎架构设计.md §检查点决策语义
    steer: str = ""
    # 跨文件夹指挥 · 形状甲：本 worker 的目标 Folder id（解析后的文件夹身份）。
    # 有值 → prepare_agent_node 另建 backend + 记忆跟该 folder；None → 坐会话默认桌。
    # 嵌套子派：省略时由 builder 填父目标（再点名才换）。不改会话 folder_id。
    target_folder_id: str | None = None


@dataclass
class ContextBlock:
    """One labeled segment of context a Run received at assembly time — the structured
    twin of a「## 标题 + 正文」section in the worker prompt.

    单一源 (用户看到的 == LLM 吃到的): a worker's opening user message is RENDERED from an
    ordered list of these blocks, and the SAME list rides the ``run_context`` event so the
    frontend shows exactly what fed the run — one assembly, two projections, no drift
    (避开补丁绊线: no second「拼给 LLM」vs「展示给用户」path to reconcile).

    ``channel`` buckets the block for the UI: ``request`` (团队级原始请求) / ``team_position``
    (DAG 拓扑：并行队友 + 产出去向) / ``dependency`` (上游产物注入) / ``workspace`` (工作区文件
    清单) / ``task`` / ``deliverable`` / ``gate_notes`` (用户已放行的主 Agent 把关) /
    ``steer`` (用户中途操舵，最后最高优先). A ``dependency`` block additionally records its
    provenance — the upstream ``source_role`` / ``source_run_id`` that produced it, the
    ``fidelity`` the executor chose (``pointer`` 递指针 / ``summarize`` / ``pass_through``),
    whether it was ``truncated`` to fit budget, and the artifact ``files`` it points at —
    so the user sees HOW a teammate's product was handed down, not just that it was.
    Non-dependency channels leave those defaults.

    → 见设计: docs/03-AI核心/上下文传递可视化.md（单一源/双投影、通道枚举、决策①–④）
    """

    channel: str
    heading: str
    body: str
    source_role: str = ""
    source_run_id: str = ""
    fidelity: str = ""
    truncated: bool = False
    files: list[str] = field(default_factory=list)


@dataclass
class RunState:
    """The mutable execution state of one Run — the live counterpart to the
    immutable :class:`RunSpec`.

    ``usage`` carries this node's token counts (short-key form: {"input",
    "output", "reasoning", "cache_hit", "cache_miss"}) so the caller folds them
    into the turn totals; ``cost`` is this run's priced money in integer nano-CNY
    ({"input", "cached", "output", "total"}), computed once by the executor so the
    per-run ledger and UI payroll read it without re-pricing. ``rounds`` counts
    the LLM calls this run made (summed across contract retries).
    """

    phase: RunPhase = RunPhase.QUEUED
    attempt: int = 0
    wave: int = 0
    # 确定性失败区分 (BL-6): ``llm_failure_class`` — not leaf ``exc.retryable``.
    # False = terminal (prompt 超长 / 400 / 鉴权 / 余额 / 合同硬失败 / 关客户端)：
    # waiting will not help; do not 整跑. True (default) = transient (rate-limit
    # stays True even after ``mark_llm_leaf_exhausted``) or any COMPLETED run.
    # A transient FAILED + transcript is the seed for in-node / hot continue.
    error_retryable: bool = True
    # Wire ``run_failed.error_code`` / ``retry_after`` (AgentCoreError semantics). Empty /
    # None when the failure was not a coded upstream error (contract hard-fail, crash).
    error_code: str = ""
    error_retry_after: float | None = None
    content: str = ""
    # The run's thinking text (the last attempt's, parallel to ``content``). Carried
    # so the CAPTAIN root run hands its reasoning to the pipeline for persistence AND
    # so a delegated worker's 思考全文 lands in its ``message_final`` fact — the reload
    # rebuilds the run node's thinking from this fact, not from the (transport-only,
    # no longer journaled) ``run_reasoning_delta`` stream (执行级事件溯源: deltas 退场).
    reasoning: str = ""
    error: str = ""
    # Soft contract shortfalls on a COMPLETED run: the output was accepted (a
    # non-strict contract failed after retries) but carries these caveats, which
    # the captain sees in the aggregated result so it can judge / re-delegate.
    warnings: list[str] = field(default_factory=list)
    # First-class delivery gaps on a terminal node (缺章软放行 / 超时缩水 / 部分失败).
    # Each row is ``{description, reason?}`` — mirrors ``delivery_status.gaps`` /
    # CEO tool meta so the three surfaces share one structured list. Empty when
    # the run completed cleanly with no residual shortfall.
    delivery_gaps: list[dict[str, str]] = field(default_factory=list)
    # 向上升级（worker → CEO）: decisions / blockers this worker raised via the
    # ``escalate`` tool — each ``{question, assumption, blocking}`` — harvested from the
    # transcript when the run finishes (mirrors ``files_touched``). The DelegateTool
    # surfaces these PROMINENTLY in the CEO-facing aggregate so the CEO resolves them
    # (ask_user / revise / re-delegate) before finalizing. Distinct from ``warnings``:
    # a warning is a soft quality caveat (判断是否返工), an escalation is a worker-flagged
    # 待决问题 it couldn't settle alone. Empty for a run that escalated nothing.
    # Escalation Gate (routing Phase 1) may also append scheme-layer signals here
    # (``source=escalation_gate``) so CEO synthesis sees deterministic gate trips.
    escalations: list[dict[str, Any]] = field(default_factory=list)
    # Web sources this worker consulted (web_search / read_url), de-duped across
    # contract retries. Collected un-numbered (the worker text is not annotated):
    # the DelegateTool folds these into the turn's shared source card so the user
    # sees what the WHOLE team researched, not just the CEO's own searches.
    citations: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    duration_ms: int = 0
    rounds: int = 0
    # B2: a non-default terminal finish the CAPTAIN root should stamp on the turn
    # instead of the rounds-derived END_TURN / MAX_ROUNDS — last stamp wins when the
    # engine appended more than one (e.g. ``UNPRODUCTIVE`` early-stop then ``PAUSED``
    # after force-finalize ``ask_user``). ``DEGRADED`` / ``UNPRODUCTIVE`` / ``PAUSED``
    # are the common cases. ``None`` = normal finish. Worker runs leave it None (their
    # emptiness is handled by the contract retry / soft-fail path, not the turn finish).
    finish_override: FinishReason | None = None
    # Workspace paths this worker landed, harvested from the transcript when the run
    # completes — the tools' OWN self-reports (``ToolResult.file_products``), so an
    # indirect landing (a ``code_execute`` script's copy-out) counts exactly like a
    # ``file_write``, and a new file-producing tool is on the ledger the day it reports.
    # The DelegateTool surfaces these in the CEO-facing aggregate as a「文件产出」manifest
    # so the CEO knows what landed WITHOUT re-listing the workspace (省掉收敛阶段的冗余
    # file_list 轮).
    files_touched: list[str] = field(default_factory=list)
    # Path-level acceptance for ``files_touched`` (块 1)：``[{path, status, kind?,
    # derived_from?, reason?, detail?}]`` with status ∈ accepted|rejected. ``kind`` /
    # ``derived_from`` come from the producing tool's self-report (产物类型；导出件指回
    # 源文件). Cite/contract failures that name a path reject it even on soft-COMPLETED;
    # ``delivery_status.delivered_files`` and CEO「已交付」only count accepted. Empty when
    # the run landed nothing.
    file_acceptance: list[dict[str, Any]] = field(default_factory=list)
    # Tool failure facts from this run's LoopController tally (tool_name / failure_count /
    # last_error / succeeded_after). Empty when no non-policy tool failure occurred.
    # CEO synthesis folds these into a structured ``tool_failures`` section — not a hard
    # gate (run may still COMPLETED). Distinct from ``warnings`` (contract soft-fails).
    tool_failures: list[dict[str, Any]] = field(default_factory=list)
    # 完工交接简报 (worker → 下游/CEO): a structured wrap-up the worker submits by calling the
    # ``handoff`` terminal tool (一句话结论 / 关键要点 / 采用的假设 / 建议下一步), harvested once
    # from that tool call at run finish (mirrors ``files_touched`` / ``escalations`` — all read
    # off the transcript). It is structured DATA, never re-parsed out of the prose, so the output
    # stays the pure deliverable and the brief can't overlap it. Lets a downstream dep block LEAD
    # with the author's own 结论 — most likely to survive budget-trim, cheapest to read — and the
    # CEO aggregate surface 建议下一步 to relay to the user, instead of every reader re-deriving
    # the gist from raw prose. ``None`` when the worker never called handoff (or its args were
    # unusable; harvest degrades gracefully). Keys: summary / key_points / assumptions /
    # next_steps (each present only when non-empty). Best-effort signal, never load-bearing.
    debrief: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost: dict[str, int] = field(default_factory=dict)
    # The run's full message transcript (system + task + every assistant/tool turn
    # + the final answer), captured so the run is RECOVERABLE: a 定向唤回 (revise)
    # appends an instruction to this and re-runs the loop — the same author
    # continuing on its own draft (统一「续写」原语, 见 runs/session.py). Empty for a
    # run that never produced one (skipped, or failed before any LLM answer). Typed
    # under TYPE_CHECKING so this module stays import-light.
    transcript: list[LLMMessage] = field(default_factory=list)
    # 收到的上下文 (上下文传递可视化): the ordered :class:`ContextBlock` list the executor
    # assembled this run's opening user message FROM — the structured twin of what the LLM
    # was fed. The executor emits it as the ``run_context`` event (the frontend's source);
    # held here too for the run model's completeness. NOT serialized into the resume/journal
    # seed (``state_to_json`` allow-lists fields), so it never bloats the fact log — a
    # reload rebuilds it from the journaled ``run_context`` event instead. Empty for a run
    # whose opening was not block-assembled (a 续写 revision continues a saved transcript).
    received_context: list[ContextBlock] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES


@dataclass(frozen=True)
class NodeTiming:
    """One node's occupancy window within a WaveScheduler run (多任务并行图 / 并行时间线).

    ``start_ms`` / ``end_ms`` are offsets from the scheduler's wall start — the SAME t0
    :attr:`BatchMetrics.wall_ms` measures — so the host can lay every node on one timeline
    and SEE the temporal truth the dependency DAG can't: real concurrency (overlapping
    bars), the critical path (the longest pole), and where the ``width`` cap serialized
    ready work (gaps before a bar that a free slot would have filled). ``outcome`` is the
    node's terminal :class:`RunPhase` value. Only **dispatched** nodes appear here —
    cascade-skipped nodes never ran, so they carry no window (their count still rides
    :attr:`BatchMetrics.skipped`).
    """

    run_id: str
    start_ms: int
    end_ms: int
    outcome: str


@dataclass(frozen=True)
class BatchMetrics:
    """Observability snapshot of one :class:`~agentcore.runtime.runs.wave.WaveScheduler`
    run (调度埋点量化).

    Computed by the scheduler and handed back through an optional ``metrics_sink`` (the
    pure ``usage_sink`` idiom): the scheduler stays free of a logging dependency and the
    HOST logs the snapshot. It answers the questions the continuous scheduler exists to
    make good on: did parallelism actually materialise (``busy_ms`` vs ``wall_ms`` →
    average concurrency), how wide did the fan-out get (``nodes`` / ``peak_running``), and
    was the ``width`` cap the bottleneck (``slot_starved`` > 0 — a dispatch cycle where a
    ready node had to wait for a free slot). Outcome counts round out a one-line health
    read. All timings are wall-clock ms; counts exclude resume-seeded nodes.

    受监督波循环埋点 (执行引擎架构设计.md §受监督的波循环, v1 决策「埋点用于调参与验证真痛」): the
    boundary + escalation tallies the design earmarked to quantify「自我纠偏」without捞日志阻塞
    开发. The boundary counts tally ``on_boundary`` YIELDs THIS run surfaced, split by reason —
    they count boundaries *fired*, not markers present: a plan carrying a bind/scope marker but
    driven with no hook (autonomous jobs / tests) fires none. The escalation counts are raw so
    the host derives「scope 信号占比」= ``scope_escalations / escalations`` itself, mirroring how
    it derives avg concurrency from ``busy_ms / wall_ms`` (the snapshot stays presentation-free).
    All zero for an ordinary plan (no late-binding, no escalation, no checkpoint).
    """

    nodes: int  # nodes THIS run dispatched (seed_completed nodes excluded)
    width: int  # final concurrency cap (may grow after live-plan merge recalc)
    peak_running: int  # high-water mark of concurrently in-flight nodes
    wall_ms: int  # scheduler wall time, entry → terminal
    busy_ms: int  # Σ per-node occupancy (dispatch → finish); busy_ms/wall_ms ≈ avg concurrency
    # Ready-but-blocked queue events (not 50ms cancel-poll cycles): +1 per contiguous
    # starvation episode when ready nodes exist but width is full.
    slot_starved: int
    completed: int
    failed: int
    skipped: int
    cancelled: int = 0
    # ── 受监督波循环边界埋点 (boundaries fired this run, by reason; see docstring) ──
    bind_boundaries: int = 0  # 晚绑定触发次数 (BIND yields)
    scope_boundaries: int = 0  # 计划漂移返工触发数 (SCOPE yields)
    checkpoint_boundaries: int = 0  # CHECKPOINT yields (user plan_review)
    # ── escalate 信号埋点 (raw → host derives scope 占比) ──
    escalations: int = 0  # total escalations harvested across THIS run's nodes
    scope_escalations: int = 0  # of which carried kind=scope (deviation signal)
    # ── 多任务并行图 (并行时间线): per-node occupancy windows (offsets from wall start) ──
    # so the host can render real temporal parallelism. Dispatched nodes only (skipped
    # omitted); ordered by completion (the host sorts by start for display).
    timeline: list[NodeTiming] = field(default_factory=list)
