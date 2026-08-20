"""Run-model constants (统一 Run 模型 第一阶段).

Self-contained tunables for the ``runs`` package so it imports nothing outside
itself. The tree-wide concurrency budget lives here (not in a broad
``runtime.constants``) to keep ``runs`` a clean, dependency-light primitive.
"""

from __future__ import annotations

# FALLBACK value for the tree-wide + single-scheduler parallel budget when settings
# are unavailable (unit stubs). The live knob is ``settings.engine_max_parallel_delegations``
# (config/engine.py, default 12); ``concurrency.resolve_max_parallel`` reads it with a lazy
# import and falls back HERE — so keep this in sync with that default. The budget is enforced
# tree-wide by the ContextVar in ``concurrency.py`` (分而不乘 across nested fan-outs) and, at
# a single wave, by ``WaveScheduler``'s dispatch width; both draw the same knob. Overflow
# queues, so this bounds latency, not team size. Tuning rationale lives on the config field.
MAX_PARALLEL_DELEGATIONS = 12

# Most worker tasks one delegate call may spawn. Excess tasks are rejected
# (整批拒绝 + 分批指引). A product-behaviour 口径 (how wide a single fan-out a CEO may请求),
# NOT a deployment-resource cap — so it stays a constant, unlike the parallel budget above.
# The delegate schema description interpolates this value (tools/builtin/delegate/schema.py),
# so it stays the single source of the「单次≤N」copy.
MAX_DELEGATION_TASKS = 20

# 补跑（replan add 带 replaces_run_id / continue_from_run_id）单次硬顶：按缺口限流，
# 禁止一次整团重开放大抖动。实际允许条数 = min(缺口数, 本常量)；与全局 max_parallel 无关。
MAX_GAP_FILL_ADDS = 3

# Hard ceiling on delegation nesting across one turn's Run tree. The CEO's direct
# workers are depth 1; a worker may itself delegate ONLY while its own
# depth < this cap. Legal chain: CEO → depth1 → depth2 → depth3 leaf.
# depth-3 sub-workers never receive a delegate tool. Bounds recursion (cost /
# latency / fan-out) on top of the tree-wide width budget (MAX_PARALLEL_DELEGATIONS).
MAX_DELEGATION_DEPTH = 3

# Per worker-captain cap on sub-workers spawned across all nested delegate calls
# in one turn (depth-1/2 leads; CEO uses MAX_DELEGATION_TASKS instead).
MAX_WORKER_SUBDELEGATIONS = 4

# Contract-gate retries are SEPARATE from infra: a transient 429 does not
# remount the node. The former re-runs on *content* not meeting its contract,
# re-prompting with the specific shortfalls. Default 1 (one correction chance),
# hard-capped so a pathological contract can't loop a worker forever.
DEFAULT_CONTRACT_RETRIES = 1
MAX_CONTRACT_RETRIES = 3

# Absolute ceiling on task / CEO ``max_rounds`` input (builder clamps; ``<1``
# still drops to None → profile default). Member default is 56; this bound
# equals that default so a CEO cannot inflate a single produce segment past
# the worker profile. Repair playbooks stamp 4–6 and are unaffected.
MAX_TASK_ROUNDS = 56

# Cross-attempt produce-round cap for one worker run (main pass +
# contract.retry + light_repair / write_pass). Per-pass default 56 is
# unchanged; a second full 56-round segment after contract retry is what
# this stops (online: 56+54 stacked to 109, 4.46M input tokens, no
# product). 80 = 56 + 24: after a full first pass the correction slice is
# less than half a default segment — enough to rewrite / handoff, not
# another investigation. 56+54 is strictly above this cap.
MAX_RUN_TOTAL_ROUNDS = 80

# 带现场续派（乙）唤回闸：一条作者链累计可被续写（CEO continue_from_run_id + redirect
# 热修共用）的次数上限，防无限打磨；辩论编排续写豁免（轮次上限归 RoundPolicy）。参照
# contract 的「一次自动返工」略宽到 3；超限后续派项拒绝并提示回落甲（冷 delegate）。
# → 见设计: docs/03-AI核心/多轮编排与同人续派.md §四 §五
DEFAULT_RECALL_LIMIT = 3

# 留人 roster（乙 续派）内存治理 (P2)：进程内留住 worker session 供【跨回合】带现场续派，
# 对齐 approvals / channel / locks 的单机 posture（多进程扩展时换 Redis）。三道闸防内存
# 无界增长 + 一道闸防跨会话泄漏：
# → 见设计: docs/03-AI核心/多轮编排与同人续派.md §四
# 单个 conversation 的 roster 最多留多少个可恢复 session（超出按 LRU 淘汰最久未访问的）。
DEFAULT_ROSTER_MAX_SESSIONS = 32
# 单个 conversation 的 roster transcript 总字节上限，防一个长会话的留存吃爆内存。
DEFAULT_ROSTER_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB
# session / conversation roster 的空闲存活时长：超过即视为过期，定向唤回 落空回落甲。
DEFAULT_ROSTER_TTL_SECONDS = 30 * 60.0  # 30 min
# 进程内最多同时留多少个 conversation 的 roster（超出按 LRU 淘汰最久未访问的会话）。
DEFAULT_ROSTER_MAX_CONVERSATIONS = 256

# Per-worker TOTAL budget (chars) for upstream dependency products injected into a
# downstream node's prompt, SHARED across all its pass_through deps (water-filled in
# runs/executor/: a dep needing less takes only what it needs, the remainder goes
# to larger deps). A shared total — not a per-dep cap — so a wide fan-in can't
# multiply into a huge prompt (the old flat 4000-per-dep cap let N deps reach
# N×4000 unbounded). Sized to align with the CEO's own delegate-output budget
# (tools/builtin/delegate.py ``_DELEGATE_OUTPUT_LIMIT`` = 16000): a downstream writer
# synthesizing upstream research deserves as much context as the CEO synthesizing the
# batch. Replaces the old 4000 cap, which starved a research→writer chain — a long
# upstream was head-truncated, silently dropping its tail (金额 / 法条编号). When a dep
# must still be trimmed it is HEAD+TAIL truncated (`runs/fidelity.py truncate_head_tail`),
# not head-only, so trailing details survive.
DEP_CONTEXT_BUDGET = 16000

# The CEO's synthesis reads the aggregated worker products as the delegate tool's
# output; raise the model-facing truncation budget well above the 4000 default so a
# multi-worker batch isn't clipped before the CEO can integrate it.
DELEGATE_OUTPUT_LIMIT = 16000

# Per-step product excerpt cap in a plan_review card: enough for the user to
# recognise what just finished without shipping the whole product over SSE.
# Prefer handoff debrief (summary + key_points); raw body markdown is fallback only
# — 480 leaves room for ~8 short outline chapter lines after a one-line conclusion.
PLAN_REVIEW_SUMMARY_CHARS = 480

# Chars a summarize-policy dep (result_handling="summarize") is compressed to — a
# tight digest for the large-fan-in token-saving case, independent of (and far
# smaller than) the pass_through budget above.
DEP_SUMMARY_CHARS = 600

# 递指针不递全文 (Agent协作模式.md 远期 → 现状): when an upstream dependency already
# WROTE its product to the shared workspace (its ``files_touched`` is non-empty), a
# downstream worker gets a POINTER — a tight prose digest + the artifact paths to
# ``file_read`` — instead of the whole product re-shipped through the prompt. The
# artifact is on disk and reachable; re-injecting it whole wastes tokens and risks
# tail-trimming (the budgeted full-text path). These bound the pointer: how far the
# prose digest is cut, and how many paths to list before eliding. A pointer dep does
# NOT draw on DEP_CONTEXT_BUDGET (only PROSE pass_through deps, which have no file to
# point at, still share it).
DEP_POINTER_SUMMARY_CHARS = 600
DEP_POINTER_MAX_FILES = 20

# Wave3 B：分区 worker 开局强制注入上游契约/设计摘要（少反复 file_read）。
# 单文件截断上限；多文件合计另受调用方清单约束。
CONTEXT_INJECT_CHARS = 2400

# Wave3 B：同一相对路径成功 file_read 次数上限。
# 从第 1 行要满安全顶（双省 offset/limit、只传 offset=1、只传 limit=行顶）计次；
# 开窗仅当本次请求行范围已被此前交付覆盖、且投影窗内仍有该 path 正文时计次。
# 新范围分页不计次。仅当投影窗口仍有该 path 正文且无再读授额时硬拒（逼模型吃已有正文）；
# 正文已清理或写成功（计数与已交付范围一并清）后可再读。
# 同轮并行同 path 多 call 在 execute_tools 扇出去重，只计一次成功读。
FILE_READ_SAME_PATH_MAX = 5

# CEO 综述输入瘦身: the prose pool shared across a batch's pass_through workers when
# their products are rendered into the CEO's synthesis input (ceo_format.format_for_ceo).
# Same fidelity discipline as a worker's dep-injection budget, but applied at the OTHER
# fan-in (all workers → the CEO's overview pass) instead of (a worker's deps → it). The
# motive is correctness, not only cost: an unbounded aggregate would hit the single
# ToolResult output_limit net and, by middle-elision, drop whole workers from the middle
# (防幻觉铁律 / 收尾指引 at the tail now survive — ToolResult keeps head+tail — but a
# worker silently vanishing from the synthesis input is still wrong). File-producers
# (digested — their full product is on disk + shown in the UI) don't draw on this pool.
# Sized well below DELEGATE_OUTPUT_LIMIT so digests + roster + closing instructions fit
# under CEO_SYNTHESIS_MAX_CHARS (prefer pointer + short bullets over full prose dump).
CEO_SYNTHESIS_BUDGET = 3600
# Per file-producer digest in CEO synthesis (tighter than DEP_POINTER_SUMMARY_CHARS —
# CEO only needs orientation; full artifact is on disk / in the UI).
CEO_SYNTHESIS_POINTER_CHARS = 240
# Hard cap on format_for_ceo final text (chars) when raw product is small.
# Blocks short-raw → ~6k packaging bloat; large-raw batches rely on BUDGET instead.
CEO_SYNTHESIS_MAX_CHARS = 4000
# Soft expansion guard used by tests / metrics (prefer_brief + POINTER_CHARS
# keep natural length under MAX; whole-doc chop only at MAX_CHARS).
CEO_SYNTHESIS_MAX_RATIO = 4.0

# 工作区产物清单: peer products (role-attributed) + sparse pre-existing paths
# (attachments / 裸聊 scratch; project shared trees → 「另有 N 个」summary). See
# ``workspace.sparse_listing`` + ``executor.context._workspace_manifest``.
WORKSPACE_MANIFEST_MAX_FILES = 40
WORKSPACE_MANIFEST_CHAR_BUDGET = 1800

# Canonical name of the worker-only「向上升级」tool (worker → CEO clarification
# channel). One source of truth shared by three sites that must agree without coupling
# ``runs`` to the ``tools`` package: the EscalateTool's schema name, the executor's
# allow-list guard (escalate is always offered even to a least-privilege worker), and
# serialize's transcript harvest (escalations_from_transcript). 见 docs/03-AI核心/Agent协作模式.md.
ESCALATE_TOOL_NAME = "escalate"

# Canonical name of the worker-only「贴便签」tool (worker → 并行队友 broadcast channel,
# §2.2 通·便签墙). Same single-source posture as ESCALATE_TOOL_NAME: the PostNoteTool's
# schema name + the executor's allow-list guard (post_note stays offered even to a
# least-privilege worker, so it can always broadcast to siblings).
POST_NOTE_TOOL_NAME = "post_note"

# Canonical name of the worker-only「翻便签墙」tool (worker → 团队便签墙 on-demand read,
# §2.4 变·worker 的「拉」). The pull dual of POST_NOTE_TOOL_NAME's push: same single-source
# posture (the ReadNotesTool's schema name + the executor's allow-list guard), so a
# least-privilege worker can always look up what a sibling already decided.
READ_NOTES_TOOL_NAME = "read_notes"

# Canonical name of the worker-only「改写 / 作废便签」tool (便签会过期 → supersession, §2.2). Lets a
# worker correct its OWN stale note (改写 with new text / 作废 by omitting it) so a sibling never
# builds on a dead decision. Same single-source posture as post_note / read_notes (the
# AmendNoteTool's schema name + the executor's allow-list guard).
AMEND_NOTE_TOOL_NAME = "amend_note"

# Canonical name of the worker-only「交接简报 + 收尾」tool (完工交接简报单一源). A delegated worker
# ends its run by calling this terminal tool ONCE, in the same turn as its finished deliverable,
# to submit a STRUCTURED brief (summary / key_points / assumptions / next_steps) — so the brief
# travels in a structured channel and is read straight off the call args
# (serialize.debrief_from_transcript), never parsed back out of markdown prose (its former,
# fragile「## 交接简报」form). Same single-source posture as ESCALATE_TOOL_NAME: the HandoffTool's
# schema name + serialize's transcript harvest. 见 docs/03-AI核心/Agent协作模式.md.
HANDOFF_TOOL_NAME = "handoff"

# Default per-node failure strategy (see RunPolicy.on_failure).
DEFAULT_ON_FAILURE = "retry"

# The accepted on_failure vocabulary the WaveScheduler enacts.
VALID_ON_FAILURE = frozenset({"abort", "skip", "degrade", "retry"})
