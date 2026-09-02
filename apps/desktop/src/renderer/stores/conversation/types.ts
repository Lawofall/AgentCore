import type { AskUiIntent } from "@/lib/checkpointIntent";
import type { ErrorAction } from "@/lib/errors";
import type { ExecutionJournal } from "@/stores/execution";
import type { components } from "@/types/api.generated";
import type {
  AskAssumption,
  AskQuestion,
  CeoReviewSummary,
  CheckpointDecision,
  Citation,
  ContextBlockWire,
  CostBreakdown,
  PlanReviewPending,
  PlanReviewStep,
  ProcessStep,
  UsageBreakdown,
} from "@/types/events";
import type { TurnPhase } from "./turnPhase";

export interface CheckpointDisplay {
  id: string;
  question: string;
  assumptions: AskAssumption[];
  questions: AskQuestion[];
  intent: AskUiIntent;
  status: "pending" | "resolved";
  decision: CheckpointDecision | null;
  note: string;
  selected: string[];
  /** Wire `browser_login` — CEO login gate; resume card mirrors escalate login UX. */
  browserLogin?: boolean;
}

export interface PlanReviewDisplay {
  id: string;
  steps: PlanReviewStep[];
  pending: PlanReviewPending[];
  status: "pending" | "resolved";
  decision: CheckpointDecision | null;
  note: string;
  /** 主 Agent 暂停前的把关摘要（拍板中心专属展示；旧数据 absent → 不渲染）。 */
  ceoReview?: CeoReviewSummary;
}

export interface Conversation {
  id: string;
  title: string;
  updatedAt: string;
  messageCount: number;
  lastMessagePreview: string | null;
  folderId?: string | null;
  localContainerRootId?: string | null;
  /**
   * Optimistic: folder bound via delivery/ask bind card (列表 API 暂无此字段).
   * Sidecar 寻址：`cache.rootId ?? localRootId ?? localContainerRootId`.
   */
  localRootId?: string | null;
  pinned?: boolean;
  archived?: boolean;
  /** Session permission axes (file_write / command / host). */
  permissionAxes?: {
    file_write: "ask" | "session";
    command: "ask" | "auto";
    host: "off" | "ask" | "session";
  };
  /**
   * 会话级模型组合 id：非空即本会话使用的组合（新建拍快照；改组合定义下一 turn 生效）。
   * null = 存量未钉死记录（展开时按账号默认）；勿再解释为「活跟随」。
   * 源自 `ConversationSummary.model_profile_id`，由
   * {@link import("@/components/chat/message-input/ModelPicker").ModelPicker} /
   * 建会话 POST 写入。
   */
  modelProfileId?: string | null;
  /**
   * 较早对话已压缩（`ConversationSummary.context_compacted`）。
   * 时间线隔断以 ``compactedThrough`` 为准；不携带摘要正文。
   */
  contextCompacted?: boolean;
  /**
   * 滚动压缩水印（`ConversationSummary.compacted_through`）。
   * 最后一条被折进摘要的消息 ``created_at``；有值时时间线在其后插隔断。
   */
  compactedThrough?: string | null;
  /**
   * 压缩没跟上，早期对话已掉出上下文窗口（`ConversationSummary.context_gap`）。
   * 非空 = 后端已证明「这一轮 AI 读不到那段」，展示降级提示；缺省 = 完好或未计算，保持安静。
   */
  contextGap?: ConversationContextGap;
}

/**
 * 「AI 现在看不见的那段早期对话」（后端 `ContextGapModel`）。
 *
 * `droppedMessages` 是窗口切掉的真实条数（原文仍在时间线上，只是没进模型上下文）；
 * `recoveryAt` 是上游自报的恢复时刻，ISO8601 UTC 瞬间（如 `2026-08-14T16:00:00Z`），
 * 由前端按用户本机时区成文；缺省 = 上游没给日期，只能说「稍后自动重试」，不得自行编时间。
 */
export interface ConversationContextGap {
  droppedMessages: number;
  recoveryAt?: string | null;
}

export interface MessageAttachmentMeta {
  id: string;
  name: string;
  path: string;
  truncated: boolean;
  kind?: "file" | "dir" | "conversation" | "document";
  workspacePath?: string;
  conversationId?: string;
  documentId?: string;
}

/** Conversation-page ``@`` role chip (soft mention; not an attachment kind). */
export interface AgentMentionMeta {
  agentId: string;
  role: string;
}

/** One applied change in a「记忆已更新」card (记忆更新对话内可见, Agent记忆与知识系统 §1.6).
 * `file` is a friendly label (偏好 / 画像 / 主题·<slug>); `scope` is `"global"` |
 * `"project"`; `content` is the bullet (add/update) or matched text (remove); `target`
 * is the synthetic memory-leaf path the card deep-links to (`""` = no leaf). */
export interface MemoryUpdateItem {
  action: components["schemas"]["MemoryUpdateItemView"]["action"];
  file: string;
  section: string;
  scope: string;
  content: string;
  target: string;
  /** Project folder id when ``scope`` is ``project`` (深链展开该文件夹 ``.agentcore`` 节点). */
  projectId?: string | null;
}

/** One memory-write notice on the conversation timeline.
 * `kind: "semantic"` → diff card (`items`);
 * `kind: "quota"` → the always pool is full: `summary` says so and `items` name what could
 * not be written plus the entries holding the pool (审计 CTX-A2).
 * Loaded with the latest messages window + pushed live on the firehose (`memory_updated`). */
export interface MemoryUpdate {
  id: string;
  createdAt: string;
  /**
   * 被总结的那一轮的末尾 —— 本次固化窗口最后一条消息的 `created_at`（`memory_updates.anchor_at`）。
   * live 语义卡带锚点。固化是回合结束后异步跑的，`createdAt`（落库时刻）
   * 比它总结的那一轮晚一两分钟，那时用户往往已经发出下一条消息，按 `createdAt` 锚定就会把卡片
   * 挤到后面去。时间线锚定与卡片时间戳一律走
   * {@link import("@/components/chat/messageTimeline").memoryAnchorTime}。
   */
  anchorAt?: string | null;
  kind: components["schemas"]["MemoryUpdateView"]["kind"];
  summary?: string | null;
  items: MemoryUpdateItem[];
}

export interface Message {
  id: string;
  /** 挂起即收口 (②): the SERVER's assistant message_id from `message_start` (the live
   * bubble's own `id` is a client UUID). It is the resume KEY — the id a durable frame is
   * persisted under and that `POST .../resume` claims — so a turn that ends paused
   * in-session must surface its resume card keyed by THIS, not the client id (which 404s).
   * Absent until message_start stamps it on the live path; on reload `toMessage` sets it
   * to the persisted row id (already the server id) so resume guards stay live-aligned. */
  serverMessageId?: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  process?: ProcessStep[];
  createdAt: string;
  executionId: string | null;
  isStreaming: boolean;
  /**
   * Local-only outbox sync hint for sidecar turns (as-built: 前端 UX §一B；双模式 §10.3).
   * `synced_pending` = on disk, cloud not acked; `synced` = cloud just acked.
   * Never on SSE / REST / conformance — desktop UI only.
   */
  syncStatus?: "synced_pending" | "synced";
  /** Progressive assistant-row lifecycle from ``messages.usage.status`` (P1 overlay /
   * P4 hydrate). ``running`` → stream-style partial; ``incomplete`` → empty shell
   * stops spinning, recovery = send a new turn (composer light hint; no resume
   * button). null for user / pre-feature rows. */
  status?: "running" | "complete" | "incomplete" | "failed" | null;
  composingTool?: { toolName: string; chars: number } | null;
  attachments?: MessageAttachmentMeta[];
  /** Conversation-page ``@`` role chips (REST ``agent_mentions``; 旁路 attachments). */
  agentMentions?: AgentMentionMeta[];
  citations?: Citation[];
  /** 回合调研台账（`evidence_ledger` SSE / Message.evidence_ledger）；缺省 []。 */
  evidenceLedger?: import("@/types/events").TurnEvidenceLedgerEntry[];
  cost?: CostBreakdown;
  usage?: UsageBreakdown & {
    error?: { code: string; message: string } | null;
  };
  rounds?: number;
  /** 回合墙钟用时 (ms)：live 自 message_end.duration_ms；重载自 MessageDetail.duration_ms。 */
  durationMs?: number;
  finishReason?: string;
  /**
   * Server-attested turn result (`message_end.outcome` live; REST
   * `MessageDetail.outcome` on reload). Product UI feeds this to the arbitrator
   * as `attestedKind`; local delivery/productLanded bits are fallback only.
   */
  outcome?: "ok" | "partial" | "paused" | "error" | null;
  /** 协作质量 (学·度量 §2.5): turn-level orchestration signals. Live via
   * message_end; reload via messages API (nested in usage column). Orchestration
   * counts also surface in the assistant footer; audit_drops 采集仍在、产品不展示. */
  collab?: import("@/types/events").TurnCollabMetrics;
  /** 本回合团队状态（`message_end.team_batch` live；REST 从 journal 派生）。 */
  teamBatch?: import("@/types/events").TeamBatchStatus;
  runs?: ExecutionJournal;
  captainContext?: ContextBlockWire[];
  error?: {
    code: string;
    message: string;
    context?: {
      upstream_status?: number;
      upstream_body_preview?: string | null;
      retry_attempts?: number;
      empty_diagnosis?: string;
      /** empty-response body class: html | json | text | empty (generated ErrorContext). */
      body_kind?: string;
      /** Provider endpoint root for BYOK empty-response 排查包. */
      base_url?: string;
      vendor_code?: string | null;
      model?: string | null;
      profile?: string | null;
      tool_count?: number | null;
      credential_source?: "user" | "platform" | string | null;
      /** 上游额度恢复的绝对时刻（ISO8601 UTC，如 `2026-08-14T16:00:00Z`）。后端句子里
       * 不再带时刻，红卡按用户本机时区成文（`lib/recoveryMoment`）。 */
      recovery_at?: string | null;
      /** 平台配额闸门的重置时刻（ISO8601 UTC），同上。 */
      reset_at?: string | null;
      /** 上游 429 Retry-After 秒数；生产上常缺（无 attested 头）。 */
      retry_after?: number | null;
    };
  };
  /** 回复反馈 (点赞/点踩, 对话基础功能补齐): the user's satisfaction rating on this assistant
   * reply — `"up"` / `"down"`, or `null` / undefined for 未评价. Persisted (messages.feedback
   * column) so a reloaded bubble replays the rating; toggled via the footer thumbs. */
  feedback?: "up" | "down" | null;
  traceId?: string;
  /** Preflight soft gate when the configured model may lack tool calling (turn_warning SSE). */
  turnWarning?: string;
  /**
   * 消息归因（如 `execution_harvest` 系统收口）。REST ``MessageDetail.origin`` 已投影；
   * 缺省时由 {@link import("@/lib/executionHarvest").isExecutionHarvestMessage} 从正文前缀推断。
   */
  origin?: string | null;
  /**
   * 「曾中断恢复」：这条助手回合中途崩过，由租约清扫重驱跑完，成果仍归本条消息。
   * REST ``MessageDetail.recovered`` 投影；不做正文猜测，缺字段即视为没崩过。
   */
  recovered?: boolean;
}

export interface ConversationRuntime {
  messages: Message[];
  /** Conversation-tail「记忆已更新」cards (记忆更新对话内可见, §1.6): what the AI
   * remembered FROM this conversation, appended after the last message. Loaded with
   * the latest window + appended live from the firehose. */
  memoryUpdates: MemoryUpdate[];
  isGenerating: boolean;
  /**
   * 回合停止生命周期（键随本切片 = conversationId）。
   * idle → preflight → streaming → stopping → stopped|completed|failed。
   * Abort 只断流；开流门禁与迟到事件过滤以本字段为准。
   */
  turnPhase: TurnPhase;
  abort: AbortController | null;
  error: string | null;
  retry: (() => void) | null;
  errorAction: ErrorAction | null;
  messageFocus: { id: string; nonce: number } | null;
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
  loadingOlder: boolean;
  loadingNewer: boolean;
  /** turn_warning received before message_start — stamped onto the next assistant bubble. */
  pendingTurnWarning: string | null;
  /**
   * SSE ``X-AgentCore-Trace`` (32-hex) waiting for this turn's assistant.
   * Stashed when the pump saw the header but the last bubble is not this
   * turn's empty streaming assistant (follow / queue: last is the previous
   * turn). Applied on create / resume / stamp onto an empty streaming bubble.
   */
  pendingTraceId: string | null;
  /** 桌面本地 · live-only：每个 CEO 工具调用的真实开始时刻（epoch ms，键 = tool_call_id）。
   * ToolLine 的「运行 · Ns」计时锚定于此而非组件挂载时刻，故过程折叠/展开、聊天列表虚拟化
   * 重挂后仍准。`addProcessTool` 盖章、`endProcessTool` 清理；不落 journal（重载后工具已完成，
   * 无需再计时），也不进 conformance ProjectedTurn——同 {@link ProcessStep} tool 步的 `phase`
   * 一样是仅生产流盖的短命态。 */
  toolStartedMs: Record<string, number>;
  /**
   * 桌面：本会话最近一回合的执行路径（绑本机工作区时有意义）。
   * `sidecar` = 本地引擎；`cloud_bridge` = 云端过桥（含探活失败 / 显式强制关）；
   * `null` = 纯云会话或尚未判定。不落盘；驱动最新助手泡脚注（非引擎切换器）。
   */
  executionVia: "sidecar" | "cloud_bridge" | null;
  /**
   * Live-only：写路径争用 workspace_lock（`workspace_lock_wait` SSE）。
   * true 时空 assistant 气泡显示「等待工作区…」而非 Thinking…（不得静默等锁）。
   * EPHEMERAL——reload 丢失；message_start / waiting=false 清除。
   */
  waitingForWorkspaceLock: boolean;
}
