"""Chat-bubble SSE payload wire models (factories: ``runtime/events/chat.py``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from agentcore.runtime.events.payloads._base import WirePayload, absent
from agentcore.runtime.events.payloads.shared import CostBreakdown
from agentcore.runtime.events.types import FinishReason


class MessageStartPayload(WirePayload):
    message_id: str
    conversation_id: str
    trace_id: str | None = absent(
        "The turn's log correlation id (32-hex), for one-step log lookup. Omitted when the "
        "turn ran without a trace context (e.g. conformance vectors built outside a turn)."
    )
    full_replay: bool | None = absent(
        "This frame opens a FULL REPLAY of the turn (an attach catch-up segment), not a "
        "live turn: the client MUST reset the local streaming state it holds for this "
        "`message_id` (streamed content / reasoning / process timeline) and then fold the "
        "frames that follow as the turn's whole story. Absent (or false) on a live first "
        "frame and on a repeated same-id stamp, which stay「同回合重开」(keep the bubble). "
        "The instruction is the server's — clients must not infer it by comparing the id "
        "against whatever bubble is on screen."
    )


# 整块帧标记（attach 回放段专用，live 帧永不带）。放在正文类 delta 上：`delta` 不是一小段
# 增量，而是该通道**末尾那个尚未闭合的文本块**的当前全文，客户端替换该块而不是往后追加。
# 两个来源：① 仍在流、尚未落 journal 的通道全文（stream_state / 内存段合成）；② 增量段里
# 跨游标那一步 process 行携带的整步全文——客户端手里可能已有它的前半截（live delta 不带
# `id:`，游标停在更早的耐久事件上）。两者互斥（journal 已覆盖的通道不会再合成全文块）。
# 客户端折法：该通道末尾是同类文本块就整块换掉（标量随之重算），否则当普通新块追加——
# 故全量段（客户端刚被 `full_replay` 清空）带不带此标记结果一致。
_REPLACE_DOC = (
    "This frame carries the COMPLETE current text of the channel's last still-open text "
    "block, not an increment: fold it by REPLACING that block (recomputing the scalar) "
    "instead of appending. Absent on live frames and on plain incremental deltas. When "
    "the channel's tail is not an open text block (a tool / marker step closed it), the "
    "frame folds as an ordinary new block."
)


class ContentDeltaPayload(WirePayload):
    delta: str
    replace: bool | None = absent(_REPLACE_DOC)


# 为什么发这次 reset——客户端按它决定「清正文之外还留不留痕迹」：
# - finish_guard：交付前核验回炉，唯一折出「已按交付规范重写」chip（didRework）的 reason；
# - retry：LLM 流式传输透明重试，丢弃上次尝试的临时输出（基础设施噪音，不留痕）；
# - soft_gate：captain 收尾草稿被软门控（组队/审计）打回重来（后续组队/审计动作自带痕迹）；
# - narration：worker 调非终止工具前的旁白回滚（正常流程，旁白只进 journal）；
# - ask_user：blocking ask_user 暂停时同轮正文被吸收进提问卡片。
ResetReason = Literal["finish_guard", "retry", "soft_gate", "narration", "ask_user"]


class ContentResetPayload(WirePayload):
    """清空当前流式气泡已累积正文的信号——客户端清正文后再接收重写版 `content_delta`。
    ``reason`` 表明本次 reset 的语义（见 `ResetReason`）；仅 ``finish_guard``（交付前核验
    回炉）折出「已按交付规范重写」痕迹，其余 reason 只清正文、不留 chip。Transport-only。"""

    reason: ResetReason = Field(json_schema_extra={"ts_type": "ResetReason"})


class ReasoningDeltaPayload(WirePayload):
    delta: str
    replace: bool | None = absent(_REPLACE_DOC)


class ToolProgressPayload(WirePayload):
    """The CEO captain is composing a tool call's ARGUMENTS (bubble-scoped twin of
    `RunToolProgressPayload`). Transport-only liveliness: never journaled."""

    tool_name: str
    chars: int


# A running tool's coarse EXECUTION phase (工具执行阶段进度). Known values:
# web_search → queued / querying / fallback; read_url → fetching / reading / blocked;
# code_execute / test_run → executing; git → git_queued (waiting behind another write on
# the same repo) / git_credentials (PAT / gh token lookup) / git_remote (push·pull·fetch
# network leg, create_pr's GitHub REST) / executing (local git command). Kept as a widened
# `string` on the wire so the backend can add phases without a client bump — an unknown
# value maps to a generic「处理中」.
ToolPhase = Literal[
    "queued",
    "querying",
    "fallback",
    "fetching",
    "reading",
    "executing",
    "blocked",
    "git_queued",
    "git_credentials",
    "git_remote",
]


class ToolUseProgressPayload(WirePayload):
    """A running tool reported an EXECUTION phase — emitted between `tool_use_start` and
    `tool_use_end` so the waiting UI shows a live, honest state instead of a bare spinner.
    Transport-only: NEVER journaled and NEVER folded into the process timeline. May carry
    extra tool-specific keys (e.g. code_execute output streaming `stream`/`chunk`)."""

    # Tools may ride extra progress data on this transport-only event (`extra=` merge in
    # the factory), so unknown keys are NOT drift here.
    model_config = ConfigDict(extra="allow")

    tool_call_id: str
    tool_name: str
    phase: str
    run_id: str | None = absent("Present for a delegated worker's call.")


class ToolUseStartPayload(WirePayload):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    run_id: str | None = absent(
        "Present (run id) when a DELEGATED WORKER raised this call — process folds skip a "
        "tagged call (it belongs to that worker's run node, not the CEO's timeline). "
        "Absent for the captain's own calls."
    )


class ToolFailure(WirePayload):
    """User-facing tool failure face — only on ``tool_use_end`` when ``status=error``.

    ``message`` is Chinese product copy for the process timeline; ``code`` is a stable
    error code. Model-facing technical detail stays in ``result`` (unchanged).
    """

    message: str
    code: str


class ToolUseEndPayload(WirePayload):
    tool_call_id: str
    tool_name: str
    result: str
    status: Literal["success", "error"]
    display: dict[str, Any] | None = Field(
        default=None,
        description=(
            "A tool's OPTIONAL render-oriented payload (工具结果富渲染), distinct from the "
            "model-facing `result` text."
        ),
        json_schema_extra={"ts_type": "ToolDisplay"},
    )
    failure: ToolFailure | None = absent(
        "Present only when status=error: Chinese product message + stable code. "
        "Model-facing technical text stays in result."
    )
    run_id: str | None = absent("Worker-call tag; absent for the captain's own calls.")
    partial_failure: bool | None = absent(
        "Delegate batch finished with FAILED/SKIPPED nodes (tool meta ``partial_failure``). "
        "Absent when false / non-delegate."
    )


class TitleGeneratedPayload(WirePayload):
    conversation_id: str
    title: str


class FollowupsGeneratedPayload(WirePayload):
    """CEO→用户「下一步推荐」: 2-4 quick-reply chips for the just-finished turn, emitted
    after `message_end`. Persisted on `Message.followups` (DERIVED), no-op in folds.
    `message_id` is the assistant row the chips belong to (same id as `set_followups`)."""

    conversation_id: str
    message_id: str
    followups: list[str]


class FollowupsUnavailablePayload(WirePayload):
    """Soft empty state when followups could not be minted (not a turn error).

    Emitted only on failure paths with zero chips — legitimate empty model output does
    not emit this. EPHEMERAL / fold no-op; clients show a quiet hint above the composer.
    """

    conversation_id: str
    message_id: str
    reason: str


class TurnSavedPayload(WirePayload):
    user_message_id: str


class TurnWarningPayload(WirePayload):
    """BYOK soft gate: preflight hint when probe says the user's model may lack tool
    calling. Transport-only — not journaled."""

    message: str


class ErrorContext(WirePayload):
    upstream_status: int | None = absent()
    upstream_body_preview: str | None = None
    retry_attempts: int | None = absent()
    empty_diagnosis: str | None = absent()
    body_kind: str | None = absent(
        "empty-response body class: html | json | text | empty (no raw HTML)."
    )
    base_url: str | None = absent(
        "Provider endpoint root for BYOK empty-response 排查包 (never the API key)."
    )
    retry_after: float | None = absent(
        "上游 429 Retry-After 秒数（原始值；工程重试仍截断 ≤30s）。"
    )
    recovery_at: str | None = absent(
        "上游额度恢复的绝对时刻（ISO-8601 UTC，如 2026-08-14T16:00:00Z）；"
        "文案不含时刻，由客户端按本机时区渲染。"
    )
    reset_at: str | None = absent(
        "平台配额窗口翻篇的绝对时刻（ISO-8601 UTC）；同 recovery_at 由客户端本地化。"
    )
    credential_source: str | None = absent(
        "LLM_KEY_INVALID CTA 分流：user=去设置换 Key；platform=接入自己的 Key / 联系管理员。"
    )


class ErrorPayload(WirePayload):
    code: str
    message: str
    context: ErrorContext | None = absent()


class MessageEndUsage(WirePayload):
    """Turn token totals (long-key form, contrast `UsageBreakdown` short keys on runs)."""

    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int


class TurnCollabMetrics(WirePayload):
    """协作质量: turn-level orchestration signals for 诊断模式. Omitted on single-agent
    turns and legacy streams."""

    boundary_yields: int
    scope_signals: int
    revises: int
    escalations: int
    audit_drops: int | None = absent("审计采集降级计数 (turn_metrics.audit_drops); 诊断模式 only.")
    # 「谁做的」分层：下面两个是上面同名计数的**子集**，不是新指标。用户面把「队友互相
    # 把关」算成 总数 − 用户那份，免得把用户自己点的操作说成队友互检；运营口径读总数不变。
    boundary_yields_by_user: int | None = absent(
        "boundary_yields 中由用户拍板造成的那部分 (plan_review checkpoint)。"
    )
    revises_by_user: int | None = absent(
        "revises 中由用户「立即改此人」促成的那部分 (redirect 热修)。"
    )


class TeamBatchNoBatch(WirePayload):
    """本回合未派出队员——确定态，不是信息缺失。"""

    kind: Literal["no_batch"]


class TeamBatchInFlight(WirePayload):
    """本波 kickoff 编制已派出、尚未全部收工。``worker_count`` 不含 captain / 历史队员。"""

    kind: Literal["in_flight"]
    worker_count: int


class TeamBatchSettled(WirePayload):
    """本波队员已全部终态（或本 execution 已发出 delivery_status）。"""

    kind: Literal["settled"]
    worker_count: int


class MessageEndPayload(WirePayload):
    """Terminal turn event. `finish_reason=paused` = 挂起即收口: the turn finalized AT a
    durable checkpoint and awaits POST .../resume — NOT done and not aborted."""

    finish_reason: FinishReason
    usage: MessageEndUsage | None = absent()
    cost: CostBreakdown | None = absent()
    rounds: int | None = absent()
    collab: TurnCollabMetrics | None = absent()
    team_batch: TeamBatchNoBatch | TeamBatchInFlight | TeamBatchSettled | None = absent(
        "本回合团队状态（turn journal 派生）。没派工是 no_batch，不是缺字段。",
        ts_type="TeamBatchStatus",
    )
    # 回合墙钟用时 (主回复 meta)：与 chat.turn_complete / turn_metrics 同锚；可选，旧向量可省略。
    duration_ms: int | None = absent()
    # 回合结果质量（与 finish_reason 正交）：ok | partial | paused | error。
    # ``paused`` 本波不产出（产品面卡下一波才落）。旧向量可省略，fold 从批次表达位回推。
    outcome: Literal["ok", "partial", "paused", "error"] | None = absent(
        "Turn-level result quality, independent of finish_reason. "
        "partial = landed product with gaps. paused is reserved (not produced this wave)."
    )
