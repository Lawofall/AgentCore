/**
 * Sidecar IPC 契约 —— 主进程 / preload / renderer 三端共享的单一真相源。
 *
 * sidecar（双模式工作区 §十）是跑在用户本机的 Python 进程，**托管同一个
 * 运行时引擎**：桌面 spawn `python -m agentcore.sidecar`，经 stdio JSON-RPC 驱动它，
 * 一个回合完全在本机执行（文件 / 代码直接碰真实本地盘，不再每个 op 经 `WorkspaceChannel`
 * 往返云端）。
 *
 * 本文件只定义「主进程 ↔ renderer」这一段 IPC 的形状；主进程内部再把它翻译成与 Python
 * sidecar 之间的 JSON-RPC（见 `main/sidecar-service.ts`）。两侧字段刻意对齐 Python 端
 * `agentcore/sidecar/server.py` 的参数 / 返回，避免契约漂移。
 *
 * 与 `ipc-contract.ts`（本地文件系统）分文件：那是「云端引擎遥控桌面执行 op」的通道，
 * 本文件是「引擎本体就在本地」的通道，两者是双模式的两条独立链路。
 */

/** 一次回合的云代理推理凭据：把引擎的 LLM 调用指向云端推理代理（平台 key 不下放本机）。 */
export interface SidecarInference {
  baseUrl: string;
  apiKey: string;
  /** 服务端在铸 inference token 时解析的上游模型名（与推理代理一致）。 */
  model: string;
}

/**
 * 一次回合的 folders 窄票凭据（定案甲）：sidecar 问云账号名册 / 换桌绑定。
 * 与 inference 并列；形状 `{baseUrl, apiKey}`——`baseUrl` 为 folders 集合 URL
 *（`…/v1/folders`），`apiKey` 为 type=folders JWT；勿塞 access/cookie。
 */
export interface SidecarFoldersAuth {
  baseUrl: string;
  apiKey: string;
}

/**
 * 一次回合的 account 窄票凭据（定案 R3a）：sidecar 搜/读云端对话日志。
 * 与 folders/inference 并列；形状 `{baseUrl, apiKey}`——`baseUrl` 为 account 面根
 *（`…/v1/account`），`apiKey` 为 type=account JWT；勿塞 access/cookie。
 */
export interface SidecarAccountAuth {
  baseUrl: string;
  apiKey: string;
}

/**
 * DesktopBrowserBridge 本回合客户端句柄（与 inference 同构：长活 sidecar 随回合刷新）。
 * 主进程签发；勿经 renderer。缺省 / null → sidecar 本回合 browser=未装配（C4 明示，不静默 Sandbox）。
 */
export interface SidecarBrowserBridge {
  baseUrl: string;
  token: string;
}

/** 会话权限轴（安全权限与治理）——与服务端 `PermissionAxes` 逐字段对齐。
 *  sidecar 无会话库，桌面按回合把当前会话轴随参数送达本地引擎。 */
export type SidecarFileWriteAxis = "ask" | "session";
export type SidecarCommandAxis = "ask" | "kickoff" | "auto";
export type SidecarTeamKickoffAxis = "always" | "rules" | "skip";
export type SidecarHostAxis = "off" | "ask" | "session";

export interface SidecarPermissionAxes {
  file_write: SidecarFileWriteAxis;
  command: SidecarCommandAxis;
  team_kickoff: SidecarTeamKickoffAxis;
  host: SidecarHostAxis;
}

/** Conversation-page soft @Agent mention (prompt hint; not a hard route). */
export interface SidecarAgentMention {
  agent_id: string;
  role: string;
}

/** renderer 发起一次本地回合所需的入参（主进程据此驱动对应 root 的 sidecar）。 */
export interface SidecarStartTurnRequest {
  /** 目标会话 id —— 回流的 `turn/event` 用它定位 renderer 侧的会话切片。 */
  conversationId: string;
  /**
   * **主进程寻址专用**（选/起 sidecar 进程）：本地授权根 id。
   * 只出现在 Electron IPC，**不**写入 stdio JSON-RPC；与下方「项目本地绑定」
   * （`localRootId` / `localSubpath`，供引擎拼 workspace key）语义分离——即便数值常相同，
   * 也禁止把绑定字段当路由键、或把本字段冒充项目绑定下发给引擎。
   */
  rootId: string;
  /**
   * **主进程寻址专用**（同 `rootId`）：工作区子路径。非空时主进程把 sidecar 的
   * `workspaceRoot` 设为 `容器根 absPath + subpath`（工作区对称化 D1a）；缺省 / 空 =
   * 该根自身。只进 IPC、不进 stdio；勿与 `localSubpath`（项目绑定）混用。
   */
  subpath?: string;
  /** 本回合 id（cancel 的寻址键；renderer 自行铸造，需在该 sidecar 内唯一）。 */
  turnId: string;
  /** 本回合 trace_id（renderer 铸，32-hex，与服务端 new_trace_id 同形）：随每次云代理 LLM
   *  调用作 header 上报、并随回写落库到 assistant 消息，使推理日志↔气泡归并为同一条 trace
   *  （打通气泡↔日志）。 */
  traceId: string;
  /**
   * 登录账号 ``user.id``（透传至 sidecar ``ToolContext.user_id``）。缺省 / 空 =
   * 主进程与引擎回落字面量 ``"local"``（再经 ``resolve_sidecar_user_id`` 映射为稳定 UUID）。
   * 长活 sidecar 按回合重送：进程级 ``initialize`` 只作首次种子，不以它为唯一真相。
   */
  userId?: string;
  /**
   * 本轮用户气泡的乐观 id（干净 UUID）——outbox 幂等锚（as-built: 双模式工作区 §10.3）。
   * 主进程回写器据此组 `RecordTurnRequest.user_message_id`，与云端 finalize 去重对齐。
   */
  userMessageId: string;
  /** 用户本轮消息正文。 */
  userMessage: string;
  /**
   * Soft @Agent mentions (optional). Forwarded to ``run_chat_pipeline.agent_mentions``.
   * Omitted / empty = no prompt injection. Soft hint only — not a hard route.
   * Inner shape matches REST ``AgentMention`` (``agent_id`` / ``role``).
   */
  agentMentions?: SidecarAgentMention[];
  /**
   * 先前对话历史（`{role, content}` 列表）。已提供（含空窗 = 新会话）= 桌面
   * 已用会话 cookie 拉过同一 ``chat-context`` 窗口，sidecar **不再**打云。
   * 缺省 = 窗口未知：sidecar 用 account 窄票拉；拉不到 → 回合明确失败，
   * 禁止空窗开跑。主进程不得把缺省收成 ``[]``。禁止再从本地 store 拼全量原文。
   */
  history?: SidecarHistoryEntry[];
  /**
   * 云代理凭据。桌面侧开跑前必须已铸票；缺省时引擎 `build_turn_router` 硬拒空凭据
   * （无本机平台模型 / sidecar 自身配置回退）。
   */
  inference?: SidecarInference;
  /**
   * folders 窄票凭据（与 inference 并列）。缺省 / 铸票失败 = 不传键或 undefined，
   * 工具侧无凭据则旧行为 / 诚实失败——勿假装成功。
   */
  foldersAuth?: SidecarFoldersAuth;
  /**
   * account 窄票凭据（定案 R3a · 与 folders/inference 并列）。缺省 / 铸票失败 =
   * 不传键或 undefined，工具侧无凭据则本机 DB / 诚实失败——勿假装成功。
   */
  accountAuth?: SidecarAccountAuth;
  /**
   * DesktopBrowserBridge 本回合凭证（主进程注入）。与 inference 一样按回合重送，
   * 避免 spawn-env 过期 / 未注入导致 browser 永久未装配。
   */
  browserBridge?: SidecarBrowserBridge;
  /** 本会话当前权限轴。缺省 = sidecar 沿用当前值（初始默认少打断）。 */
  permissionAxes?: SidecarPermissionAxes;
  /**
   * 当前对话所属项目 folderId（与列表 / grouped 的 `conversation.folderId` 同形）。
   * `null` / 缺省 = 裸聊（无项目）；主进程原样写入 startTurn RPC `params.folderId`
   *（键在则引擎不查本机库；与 sidecar turns 约定对齐）。
   */
  folderId?: string | null;
  /**
   * 项目本地 FS 绑定（与 `FolderMeta.localRootId` 同形，camelCase）。
   * 有项目且 folders 缓存带 `localRootId` 时传入；裸聊 / 云项目 / 缓存无绑定 → `null`
   *（键仍下发，引擎拼记忆 workspace key 时勿再查本机 Folder 行）。
   * **不是** `rootId`：本字段进 startTurn RPC；`rootId` 仅主进程寻址。
   */
  localRootId?: string | null;
  /**
   * 项目本地子路径（与 `FolderMeta.localSubpath` 同形）。有 `localRootId` 时传
   * `folder.localSubpath ?? ""`；无绑定 → `null`。进 RPC；勿与寻址用 `subpath` 混淆。
   */
  localSubpath?: string | null;
}

/** 一条历史消息（与引擎 `run_chat_pipeline` 的 history 形状对齐）。 */
export interface SidecarHistoryEntry {
  role: "user" | "assistant";
  content: string;
}

/** 一条 web 来源（对齐服务端 `Citation`：url/title/snippet/site 恒在；台账加宽字段可选）。
 *  主进程 writebacker 原样写入 `POST .../local-turns`，故须与生成类型逐字段同形。 */
export interface SidecarCitation {
  url: string;
  title: string;
  snippet: string;
  site: string;
  id?: string | null;
  date?: string | null;
  tier?: string | null;
  query?: string | null;
  deep_read?: boolean | null;
  registrant?: string | null;
  citable?: boolean | null;
}

/** 回合调研台账条目（对齐服务端 `EvidenceLedgerEntryRest` / SSE TurnEvidenceLedgerEntry）。 */
export interface SidecarEvidenceLedgerEntry {
  id: string;
  url?: string;
  title?: string;
  snippet?: string;
  site?: string;
  date?: string;
  tier?: string;
  query?: string;
  deep_read?: boolean;
  registrant?: string;
  citable?: boolean;
}

/** 回合回放载荷（**严格**对齐服务端 `RunsPayload` schema：多 Agent 团队图事件 + 单 Agent
 *  思考·工具时间线 + per-run worker process）。主进程 writebacker **原样**写入云端落库，
 *  renderer 自身不解读；字段可选性与生成类型一致。 */
export interface SidecarRunsPayload {
  events?: Record<string, unknown>[];
  finish_reason?: string | null;
  process?: Record<string, unknown>[] | null;
  /** Per-run ProcessStep[] map (run_id → steps); mirrors RunsPayload.run_processes. */
  run_processes?: Record<string, Record<string, unknown>[]> | null;
}

/** 一次回合的最终结果（startTurn 的延迟响应——流式细节已由 `turn/event` 给过）。 */
export interface SidecarTurnResult {
  turnId: string;
  messageId: string | null;
  content: string;
  reasoningContent: string | null;
  finishReason: string;
  /** The chat model this turn ACTUALLY ran on (`resolve_turn_model` inside the sidecar).
   *  The renderer surfaces it on the model badge. */
  model: string;
  rounds: number;
  /** 全量 token 快照（引擎记账的五项）——原样回写落 `Message.usage`，使 sidecar 回合重载后
   *  的 meta 行与云回合一致（云 `persist_turn_result` 落同样键）。成本不随行（云代理权威计费）。 */
  usage: {
    inputTokens: number;
    outputTokens: number;
    reasoningTokens: number;
    cacheHitTokens: number;
    cacheMissTokens: number;
  };
  /** 助手回复的 web 来源（落库到 assistant 消息）。 */
  citations: SidecarCitation[];
  /** 回合调研台账（引用即出处 P1 · Q9）；缺省 []，不得丢 id。 */
  evidence_ledger?: SidecarEvidenceLedgerEntry[];
  /** 回放载荷（团队图 / 思考·工具时间线）；纯聊天回合为 null。 */
  runs: SidecarRunsPayload | null;
  error: string | null;
}

/**
 * 一个等待续跑的「持久挂起回合」摘要（结构化挂起 2b / 双模式工作区 §一.1 durable）。
 *
 * 字段**严格**对齐服务端 `PausedTurnSummary`（snake_case）——renderer 把它**原样**喂给
 * 同一个 `usePausedTurnStore.setForConversation`（云 / 本地共用一套挂起卡渲染，零重映射，
 * 同 `SidecarRunsPayload` 对齐云 schema 的姿态）。sidecar 回合暂停于 plan_review / ask_user 检查点
 * 且应用关闭后，帧落本机文件；重开会话时由主进程直接读盘列出（不拉起 Python）。
 */
export interface SidecarPausedTurn {
  message_id: string;
  /** 暂停点类型——决定续跑卡片形态。 */
  kind: "plan_review" | "ask_user";
  checkpoint_id: string;
  user_message: string;
  /** Client-minted id of the user bubble (pinned on pause write-back). */
  user_message_id?: string;
  /** plan_review：被复核的检查点步 / 被门控的下游步（ask_user 帧为空）。 */
  steps: Record<string, unknown>[];
  pending: Record<string, unknown>[];
  /** ask_user：统一卡片载荷（plan_review 帧为空）。 */
  question: string;
  assumptions: Record<string, unknown>[];
  questions: Record<string, unknown>[];
}

/** 续跑一个持久挂起的本地回合（结构化挂起 2b resume，经 sidecar 的 `resume` 方法）。 */
export interface SidecarResumeRequest {
  /**
   * **主进程寻址专用**（同 {@link SidecarStartTurnRequest.rootId}）：只进 IPC、不进
   * stdio；勿与 `localRootId` 项目绑定混淆。
   */
  rootId: string;
  /**
   * **主进程寻址专用**（同 {@link SidecarStartTurnRequest.subpath}）：只进 IPC；
   * 勿与 `localSubpath` 混淆。
   */
  subpath?: string;
  conversationId: string;
  /** 挂起回合的 assistant message_id（续跑键；续跑后的回复复用它）。 */
  messageId: string;
  /** 本次续跑的 trace_id（同 {@link SidecarStartTurnRequest.traceId}）：续跑也跑 LLM，故
   *  随云代理调用上报、并随回写落库，使这次续跑的推理↔气泡归并为同一条 trace。 */
  traceId: string;
  /**
   * 登录账号 ``user.id``（同 {@link SidecarStartTurnRequest.userId}）：续跑按回合重送，
   * 覆盖进程级 initialize / 帧内旧 ``suspension.user_id``。
   */
  userId?: string;
  /** 挂起时已落库的原始 user 气泡 id —— outbox 幂等锚（同 startTurn.userMessageId）。 */
  userMessageId?: string;
  /** continue（授权并开工）/ adjust / stop / research_first（辩论·先调研再辩）。 */
  decision: "continue" | "adjust" | "stop" | "research_first";
  /**
   * continue：可选开工嘱咐（非空则注入未跑队员，与 checkpoints CONTINUE+note 对齐）；
   * adjust：转向说明；stop：收尾语；research_first：忽略 note。
   */
  note: string;
  /** ask_user 的选项选择；plan_review 忽略。 */
  selected?: string[];
  /**
   * team_preview（delegate）开工修正：用户关闭的 `run_id`；缺省 / 空 = 全员开工。
   * 辩论 / 非 delegate / ask / plan_review：服务端忽略。stop 时客户端不传。
   */
  excluded_run_ids?: string[];
  /**
   * team_preview（delegate）写盘单向收紧：仅允许 `capability: "text_only"`。
   * 形状锁死为数组（不用 map）；stop 时客户端不传。
   */
  write_capability_overrides?: Array<{
    run_id: string;
    capability: "text_only";
  }>;
  /** Structured website style pick (s0/s1/…). */
  /**
   * 云代理凭据（同 `startTurn`）——续跑要跑 LLM；重启后续跑会新拉起引擎，故须随带。
   * 桌面侧开跑前必须已铸票；缺省则引擎硬拒空凭据（无本机平台模型回退）。
   */
  inference?: SidecarInference;
  /** folders 窄票凭据（同 `startTurn.foldersAuth`）。 */
  foldersAuth?: SidecarFoldersAuth;
  /** account 窄票凭据（同 `startTurn.accountAuth`）。 */
  accountAuth?: SidecarAccountAuth;
  /** DesktopBrowserBridge 本回合凭证（同 `startTurn.browserBridge`）。 */
  browserBridge?: SidecarBrowserBridge;
  /** 本会话当前权限轴（同 `startTurn.permissionAxes`）。 */
  permissionAxes?: SidecarPermissionAxes;
  /**
   * 当前对话所属项目 folderId（同 {@link SidecarStartTurnRequest.folderId}）：
   * 续跑对称下发；键在（含 `null`=裸聊）则覆盖帧内 `suspension.folder_id`；
   * 缺键保留帧内值（旧客户端兼容）。
   */
  folderId?: string | null;
  /**
   * 项目本地 FS 绑定（同 {@link SidecarStartTurnRequest.localRootId}）：续跑对称下发，
   * 供引擎拼 workspace key；`null` = 裸聊 / 云项目 / 无绑定。
   */
  localRootId?: string | null;
  /** 同 {@link SidecarStartTurnRequest.localSubpath}。 */
  localSubpath?: string | null;
}

/** A1+ 本机回合文件真 diff（相对 `AgentCore/baselines/{messageId}.zip`）。 */
export interface SidecarTurnFilesDiffRequest {
  rootId: string;
  subpath?: string;
  messageId: string;
  /** Optional; omit → sidecar resolves by messageId path convention. */
  baselineSnapshotId?: string | null;
}

/** Wire shape mirrors cloud ``GET …/files/diff`` (snake_case). */
export interface SidecarTurnFilesDiffResult {
  message_id: string;
  baseline_snapshot_id: string | null;
  available: boolean;
  data: Array<{
    path: string;
    change_type: "added" | "modified" | "deleted";
    base_sha: string | null;
    result_sha: string | null;
    is_binary: boolean;
    content: string | null;
    size_bytes: number;
    base_content?: string | null;
  }>;
  total: number;
  added: number;
  modified: number;
  deleted: number;
}

/** A2′ 本机回退到回合基线（unzip 覆盖工作区，不经云 restore）。 */
export interface SidecarRestoreTurnBaselineRequest {
  rootId: string;
  subpath?: string;
  snapshotId: string;
}

/**
 * 本地「留版本」· 创建：zip 工作区落 `AgentCore/versions/<id>/`。
 *
 * 与回合基线分轨：基线是 best-effort（失败静默返空），命名版本是用户显式动作 ——
 * 失败以 reject 上抛，UI 必须如实报错。列举 / 删除不走 sidecar（`fsApi` 更轻）。
 */
export interface SidecarCreateWorkspaceVersionRequest {
  rootId: string;
  subpath?: string;
  /** 用户输入的版本名（非空、≤200 字）；空名由 Python 侧拒绝。 */
  name: string;
}

/** Wire shape mirrors cloud ``SnapshotSummary`` (snake_case; `name` ↔ `label`)。 */
export interface SidecarWorkspaceVersionResult {
  version_id: string;
  name: string;
  created_at: string;
  size_bytes: number;
}

/** 本地「留版本」· 恢复：overlay 解压回工作区（不清空，不经云 restoreSnapshot）。 */
export interface SidecarRestoreWorkspaceVersionRequest {
  rootId: string;
  subpath?: string;
  versionId: string;
}

/** Local hydrate: list live browser sessions from sidecar Registry (not cloud). */
export interface SidecarListBrowserSessionsRequest {
  rootId: string;
  subpath?: string;
  conversationId: string;
}

/** Wire shape mirrors cloud ``GET …/browser/sessions`` (snake_case). */
export interface SidecarListBrowserSessionsResult {
  data: Array<{
    session_id: string;
    conversation_id: string;
    host_kind: "sandbox" | "local";
    control: "agent" | "user";
    run_id?: string | null;
    created_at: number;
    last_used: number;
    url?: string | null;
    title?: string | null;
  }>;
  active_session_id?: string | null;
}

/**
 * Build the Python JSON-RPC ``resume`` params from a renderer IPC request.
 *
 * ``rootId`` / ``subpath`` are main-process routing only — they never cross stdio.
 * ``folderId`` is project ownership (always sent, including null = bare chat).
 * ``localRootId`` / ``localSubpath`` are project binding for the engine workspace key
 * (always sent, including null) — not routing keys.
 * ``selected`` is always sent (empty array when absent) so Python never has to guess.
 */
export function buildSidecarResumeRpcParams(
  req: Pick<
    SidecarResumeRequest,
    | "messageId"
    | "conversationId"
    | "traceId"
    | "decision"
    | "note"
    | "selected"
    | "userId"
    | "userMessageId"
    | "permissionAxes"
    | "excluded_run_ids"
    | "write_capability_overrides"
    | "folderId"
    | "localRootId"
    | "localSubpath"
  >,
  inference?: SidecarInference,
  browserBridge?: SidecarBrowserBridge | null,
  foldersAuth?: SidecarFoldersAuth,
  accountAuth?: SidecarAccountAuth,
): Record<string, unknown> {
  return {
    messageId: req.messageId,
    conversationId: req.conversationId,
    traceId: req.traceId,
    decision: req.decision,
    note: req.note,
    selected: req.selected ?? [],
    // Project ownership (conversation.folderId 同形)；键始终下发，含 null=裸聊。
    folderId: req.folderId ?? null,
    // Project local binding (FolderMeta 同形)；键始终下发，含 null。
    localRootId: req.localRootId ?? null,
    localSubpath: req.localSubpath ?? null,
    ...(req.userId ? { userId: req.userId } : {}),
    ...(req.userMessageId ? { userMessageId: req.userMessageId } : {}),
    ...(inference ? { inference } : {}),
    ...(foldersAuth ? { foldersAuth } : {}),
    ...(accountAuth ? { accountAuth } : {}),
    // Explicit null clears sticky spawn-env leftovers on the Python side.
    ...(browserBridge !== undefined ? { browserBridge } : {}),
    ...(req.permissionAxes ? { permissionAxes: req.permissionAxes } : {}),
    // Optional team_preview corrections — omit when empty (keys documented in
    // packages/contract-types/src/sidecar-ipc.json as optional resume params).
    ...(req.excluded_run_ids && req.excluded_run_ids.length > 0
      ? { excluded_run_ids: req.excluded_run_ids }
      : {}),
    ...(req.write_capability_overrides &&
    req.write_capability_overrides.length > 0
      ? { write_capability_overrides: req.write_capability_overrides }
      : {}),
  };
}

/** 探活一个 `root + subpath` 的 sidecar：拉起进程并完成 initialize 握手即返回（不跑回合），
 *  用于在首次真正走 sidecar 前提前验证本机环境（Python / venv / 引擎导入 / 工作区绑定）能起
 *  得来（见 renderer `sidecarHealth`）。 */
export interface SidecarProbeRequest {
  rootId: string;
  /** 工作区子路径（同 `SidecarStartTurnRequest.subpath`）：按 root+subpath 寻址进程，使握手成功
   *  留存的进程正好被随后的首个回合复用（零额外拉起）。 */
  subpath?: string;
}

/**
 * 打开/登记本机项目后静默暖代码索引：ensure sidecar（同 root 复用）+ initialize 后
 * 主进程显式踢 `warmCodeIndex` JSON-RPC（不挡 UI；无进度条）。回合 ensure 不自动踢。
 * 形状与 {@link SidecarProbeRequest} 对齐。
 */
export type SidecarWarmCodeIndexRequest = SidecarProbeRequest;

/**
 * 打开/登记本机项目后静默暖 MCP 列表：ensure + initialize 后，主进程本机
 * `mcp-service` list_tools，再显式踢 `warmMcpDiscover` JSON-RPC 把 `{servers}` seed
 * 进 sidecar 进程缓存。回合 ensure 不自动踢。
 * 须带登录 ``userId``（与 prepare ``cache_scope`` / ``warmAccountRulesMemory`` 对齐）；
 * open/register 可 fire-and-forget，但 ``startTurn``/``resume`` 会 await 在途 warm。
 *
 * **续期是契约的一半**：服务端列表有 TTL，过期后 prepare 只读缓存 → 未装配 MCP
 * （不 await ClientTool）。回复带 {@link SidecarWarmMcpDiscoverResult.ttlSeconds}，
 * 主进程记有效期；``startTurn`` / ``resume`` 在过期后自动续暖，并在 detached
 * execution 存活期（``execution_detached`` → ``execution_completed``）按同一 TTL
 * 周期续暖，避免 sidecar 内部收口回合空装配。
 */
export interface SidecarWarmMcpDiscoverRequest {
  rootId: string;
  /** 工作区子路径（同 {@link SidecarProbeRequest.subpath}）。 */
  subpath?: string;
  /** 登录账号 id；与 startTurn.userId / prepare cache_scope 同形。 */
  userId?: string;
}

/** `warmMcpDiscover` JSON-RPC 的回复（主进程内部消费，不过 IPC 到 renderer）。 */
export interface SidecarWarmMcpDiscoverResult {
  ok: boolean;
  /**
   * 该列表在服务端进程缓存里的**剩余寿命**（秒）。缺省 / ≤0 视为「立即过期」。
   */
  ttlSeconds?: number;
}

/**
 * 打开/登记本机项目后静默暖 rules/memory instruction 快照：ensure + initialize 后，
 * 主进程显式踢 `warmAccountRulesMemory` JSON-RPC；sidecar 用 account 窄票自拉并 seed
 * 进进程缓存（与 prepare cache_only 同缓存）。
 *
 * **续期是契约的一半**：服务端快照有 TTL，过期后 prepare 只读缓存 → 空注入（不回落
 * 云端），表现为规则与长期记忆整体消失。故回复带 {@link
 * SidecarWarmAccountRulesMemoryResult.ttlSeconds}，主进程按「账号 + folderId」记有效期，
 * `startTurn` / `resume` 在过期后自动续暖，并在 **detached execution 存活期**
 * （`execution_detached` → `execution_completed`）按同一 TTL 周期续暖
 * （sidecar 内部收口回合不走桌面入口；CEO 回合 RPC 已返回后团队仍跑时必须续）。
 * open/register 可 fire-and-forget；回合发 RPC 前会 await 在途 warm。
 */
export interface SidecarWarmAccountRulesMemoryRequest {
  rootId: string;
  subpath?: string;
  /** 已知绑定 folder 时传入；缺省 / null = global-only 快照。 */
  folderId?: string | null;
  /** account 窄票；缺省则主进程跳过 RPC（暖需要票）。 */
  accountAuth?: SidecarAccountAuth;
  /** 登录账号 id（覆盖 initialize 时的 local）；与 startTurn.userId 同形。 */
  userId?: string;
}

/** `warmAccountRulesMemory` JSON-RPC 的回复（主进程内部消费，不过 IPC 到 renderer）。 */
export interface SidecarWarmAccountRulesMemoryResult {
  ok: boolean;
  /** 本次暖出的快照是否降级（部分云拉取失败）；降级条目 TTL 更短。 */
  degraded?: boolean;
  topicCount?: number;
  memoryFileCount?: number;
  /**
   * 该快照在服务端进程缓存里的**剩余寿命**（秒）——续期握手的权威值。
   * 主进程据此判何时重暖；缺省 / ≤0 视为「立即过期」，下个回合重暖（宁多暖勿谎报）。
   */
  ttlSeconds?: number;
}

/**
 * 主进程 → renderer 的回合事件推送。`event` 与服务端 SSE 的事件同形状
 * （`@/types/events` 的 `SSEEvent`），故 renderer 可把它**原样**喂给同一个
 * `dispatchSSEEvent`——云 / 本地两条链路共用一套事件处理，零额外分支。
 */
export interface SidecarEventPush {
  conversationId: string;
  turnId: string;
  event: {
    type: string;
    /** ISO-8601 字符串（与引擎 `SSEEvent.timestamp` 一致，便于原样喂 `dispatchSSEEvent`）。 */
    timestamp: string;
    payload: unknown;
  };
}

/**
 * 主进程 → renderer 的**本机履约帧**推送（与回合事件流分开的第二条链路）。
 *
 * 本机引擎的 CLIENT_TOOL（host / mcp / notify / board / board_read /
 * external_mount / terminal）不再经回合 EventSink 下发：sidecar 在自己进程内的
 * 履约中枢注册一个会话，帧经 `fulfill/frame` JSON-RPC 通知过来，主进程按
 * `payload.conversation_id` 投给持有该活回合的窗口。形状与云端设备级履约流
 * （`GET /v1/fulfill`）的帧一致，故 renderer 用同一套 ingress 消费，只是结算
 * 走 `respond`（`origin: "sidecar"`）而非云 HTTP。
 */
export interface SidecarFulfillPush {
  conversationId: string;
  frame: {
    /** `*_required` 之一，或 `client_tool_cancelled`（中断在飞 op）。 */
    type: string;
    /** `*_required` 帧带；取消帧不带。 */
    timestamp?: string;
    payload?: unknown;
  };
}

/** 主进程 → renderer 的 sidecar 生命周期/诊断推送（拉起失败、退出等）。 */
export interface SidecarStatusPush {
  rootId: string;
  /** spawned=已拉起并初始化；exited=进程退出；error=拉起/通信失败。 */
  phase: "spawned" | "exited" | "error";
  /** 人类可读说明（error/exited 时带原因，用于 UI 降级提示与排查）。 */
  detail?: string;
}

/** 结算一个被挂起的交互（审批 / ask_user / 本地工具）——经 sidecar 的 `respond` 方法。 */
export interface SidecarRespondRequest {
  rootId: string;
  /** 工作区子路径（同 `SidecarStartTurnRequest.subpath`）：寻址按 root+subpath 起的进程。 */
  subpath?: string;
  /** 被挂起交互的 id（即引擎 `ClientRequestBridge` 发出的 requestId）。 */
  requestId: string;
  conversationId: string;
  /** 该交互的应答载荷（形状随交互类型而定）。 */
  result: unknown;
}

/** 取消一个在跑的回合（对齐云 ``POST …/stop``）。 */
export interface SidecarCancelRequest {
  rootId: string;
  /** 工作区子路径（同 `SidecarStartTurnRequest.subpath`）：寻址按 root+subpath 起的进程。 */
  subpath?: string;
  turnId: string;
  /** 会话 id：sidecar cancel 级联协调会话用。 */
  conversationId?: string;
  /**
   * 来源指纹（写入 `sidecar.turn_cancel_requested` / `sidecar.turn_cancelled`）。
   * - `user_stop`：停止按钮硬取消
   * - `abort_signal` / `attach_abort`：遗留枚举（桌面已不再对 Abort/attach 调 cancel）
   */
  reason?: "user_stop" | "abort_signal" | "attach_abort";
}

/**
 * 按人干预回执 —— 引擎有没有真收下这次「只停这位队员 / 立即改此人」。
 *
 * `accepted=false` = **什么都没入队**（驱动循环已退出、run 不在当前计划里、或本地 sidecar
 * 不可达）；`queued` 只是这条 execution 的排队计数，不能拿它冒充受理成功。字段与云端
 * `run-stop` / `run-redirect` 响应同形，渲染层两条路只写一份处理。
 */
export interface SidecarInterveneAck {
  accepted: boolean;
  reason: string;
  /** 面向用户的一句话（由引擎给出）。 */
  detail: string;
  queued: number;
}

/** 用户中途改某个 worker 的方向（中间可见性 Phase 2a）。 */
export interface SidecarRunRedirectRequest {
  rootId: string;
  subpath?: string;
  conversationId: string;
  executionId: string;
  runId: string;
  feedback: string;
}

/**
 * 用户中途停某个 / 全部 worker（不杀回合、不杀 CEO）。
 * `runId` 省略或 null = 停该 execution 下全部在飞与排队 worker。
 */
export interface SidecarRunStopRequest {
  rootId: string;
  subpath?: string;
  conversationId: string;
  executionId: string;
  runId?: string | null;
}

/** 辩论 ambient 掌舵（fire-and-forget，下一轮边界生效）。 */
export interface SidecarDebateSteerRequest {
  rootId: string;
  subpath?: string;
  conversationId: string;
  executionId: string;
  decision: "continue" | "conclude";
  focus?: string;
  ask?: string;
  askTarget?: string;
}

/** 本机 outbox 未同步回合的投影自足摘要（recovery → renderer D5，不透传 journal）。 */
export interface SidecarUnsyncedTurnSummary {
  user_message_id: string;
  user_message: string;
  message_id: string | null;
  trace_id: string;
  /** `dead` = permanent writeback failure (dead-letter/), still recoverable in UI. */
  phase: "open" | "ready" | "dead";
  updated_at: number;
  content: string;
  reasoning_content: string | null;
  citations: SidecarCitation[];
  evidence_ledger?: SidecarEvidenceLedgerEntry[];
  runs: SidecarRunsPayload | null;
  finish_reason: string | null;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
}

/** 查询某会话的本地恢复面（活回合 + 未同步 outbox 摘要 + 挂起帧）。 */
export interface SidecarRecoveryRequest {
  conversationId: string;
}

export interface SidecarRecoveryResponse {
  liveRunning: boolean;
  /** 活回合键（startTurn=`turnId`，resume=`messageId`）。 */
  turnId?: string;
  unsynced: SidecarUnsyncedTurnSummary[];
  /** 本机冷路挂起帧（与原 listPaused 同源；一次 IPC 拿全本地事实）。 */
  paused: SidecarPausedTurn[];
  /**
   * message_id → pause 落盘时投影的 display runs（挂起重开协作图）。
   * 与 ``paused[]`` summary 分离：summary 只喂开工卡 store，runs 走 hydrate。
   */
  pausedRuns?: Record<string, SidecarRunsPayload>;
}

/** 重绑本窗口并取回缓冲事件快照（零 await 段在主进程 handler 内）。 */
export interface SidecarAttachRequest {
  conversationId: string;
}

export interface SidecarAttachResponse {
  attached: boolean;
  turnId?: string;
  rootId?: string;
  subpath?: string;
  /** startTurn 登记；resume 类可能缺省。 */
  userMessageId?: string;
  userMessage?: string;
  traceId?: string;
  /** resume 类活回合的助手行锚（= 登记键 messageId）。 */
  messageId?: string;
  /** `"start"` | `"resume"` —— renderer 据此选择 clear-then-fold vs 增量 fold。 */
  kind?: "start" | "resume";
  /** 裸 SSE 事件（与 `dispatchSSEEvent` 同形）；非 `SidecarEventPush` 信封。 */
  events?: Array<{
    type: string;
    timestamp: string;
    payload: unknown;
  }>;
}

/** IPC 通道名 —— 主进程与 preload 共用，避免硬编码漂移。 */
export const SIDECAR_CHANNELS = {
  startTurn: "sidecar:startTurn",
  cancel: "sidecar:cancel",
  respond: "sidecar:respond",
  runRedirect: "sidecar:runRedirect",
  runStop: "sidecar:runStop",
  debateSteer: "sidecar:debateSteer",
  resume: "sidecar:resume",
  probe: "sidecar:probe",
  warmCodeIndex: "sidecar:warmCodeIndex",
  warmMcpDiscover: "sidecar:warmMcpDiscover",
  warmAccountRulesMemory: "sidecar:warmAccountRulesMemory",
  recovery: "sidecar:recovery",
  attach: "sidecar:attach",
  turnFilesDiff: "sidecar:turnFilesDiff",
  restoreTurnBaseline: "sidecar:restoreTurnBaseline",
  createWorkspaceVersion: "sidecar:createWorkspaceVersion",
  restoreWorkspaceVersion: "sidecar:restoreWorkspaceVersion",
  listBrowserSessions: "sidecar:listBrowserSessions",
  event: "sidecar:event",
  fulfill: "sidecar:fulfill",
  status: "sidecar:status",
} as const;

/**
 * 暴露在 `window.sidecarApi` 上的 renderer 端 API 面。
 *
 * `startTurn` 的 Promise 在**回合结束**时才 resolve（携带最终结果）；过程中的流式
 * 事件经 `onEvent` 推来。失败（拉起不了 sidecar / 引擎异常）以 reject 抛出，调用方
 * 据此降级（如退回云模式或提示）。
 */
export interface SidecarApi {
  startTurn(req: SidecarStartTurnRequest): Promise<SidecarTurnResult>;
  cancel(req: SidecarCancelRequest): Promise<void>;
  respond(req: SidecarRespondRequest): Promise<{ resolved: boolean }>;
  runRedirect(req: SidecarRunRedirectRequest): Promise<SidecarInterveneAck>;
  runStop(req: SidecarRunStopRequest): Promise<SidecarInterveneAck>;
  /** `accepted=false` = 引擎未收（掌舵窗口已关 / sidecar 不可达）；调用方须如实回执。 */
  debateSteer(req: SidecarDebateSteerRequest): Promise<{ accepted: boolean }>;
  /** 续跑一个持久挂起的本地回合；Promise 在续跑结束时 resolve（同 `startTurn` 携最终结果，
   * 过程事件经 `onEvent` 推来）。 */
  resume(req: SidecarResumeRequest): Promise<SidecarTurnResult>;
  /** 探活一个 root 的 sidecar（拉起 + initialize 握手即返回，不跑回合）。成功 = 本机环境能起
   * 本地引擎（握手成功的进程留存、被首个回合复用）；失败 reject（诊断经 `onStatus` 推送）。 */
  probe(req: SidecarProbeRequest): Promise<void>;
  /**
   * 打开/登记本机项目后静默暖索引：ensure + initialize 后显式踢 `warmCodeIndex` RPC。
   * 失败可忽略（不 toast）；不挡 UI。回合 ensure / probe 不自动踢。
   */
  warmCodeIndex(req: SidecarWarmCodeIndexRequest): Promise<void>;
  /**
   * 打开/登记本机项目后静默暖 MCP：ensure + initialize 后本机 list_tools，再踢
   * `warmMcpDiscover` RPC seed（须带登录 userId）。失败可忽略；不挡 UI。
   * 回合 ensure / probe 不自动踢；startTurn/resume 会 await 在途 warm。
   */
  warmMcpDiscover(req: SidecarWarmMcpDiscoverRequest): Promise<void>;
  /**
   * 打开/登记本机项目后静默暖 rules/memory：ensure + initialize 后踢
   * `warmAccountRulesMemory`（带 accountAuth + folderId + userId）。失败可忽略；不挡 UI。
   * 有票完成暖后标记；无票跳过不锁死。startTurn/resume 会 await 在途 warm。
   */
  warmAccountRulesMemory(
    req: SidecarWarmAccountRulesMemoryRequest,
  ): Promise<void>;
  /** 查询本地恢复面（活回合 + 未同步 outbox + 挂起帧）；零 spawn。 */
  recovery(req: SidecarRecoveryRequest): Promise<SidecarRecoveryResponse>;
  /** 重绑本窗口并取回缓冲事件快照 + 续流；`attached:false` 时走投影/ghost 降级。 */
  attach(req: SidecarAttachRequest): Promise<SidecarAttachResponse>;
  /** A1+ 本机回合文件真 diff（相对工作区旁基线 zip）。 */
  turnFilesDiff(
    req: SidecarTurnFilesDiffRequest,
  ): Promise<SidecarTurnFilesDiffResult>;
  /** A2′ 本机回退到回合基线（unzip 覆盖，不经云）。 */
  restoreTurnBaseline(req: SidecarRestoreTurnBaselineRequest): Promise<void>;
  /** 本地留版本：zip 工作区为一个命名版本；失败 reject（用户显式动作，不静默）。 */
  createWorkspaceVersion(
    req: SidecarCreateWorkspaceVersionRequest,
  ): Promise<SidecarWorkspaceVersionResult>;
  /** 本地恢复命名版本：overlay 解压（不清空，不经云 restoreSnapshot）。 */
  restoreWorkspaceVersion(
    req: SidecarRestoreWorkspaceVersionRequest,
  ): Promise<SidecarWorkspaceVersionResult>;
  /** Local hydrate: list browser sessions from sidecar Registry. */
  listBrowserSessions(
    req: SidecarListBrowserSessionsRequest,
  ): Promise<SidecarListBrowserSessionsResult>;
  /**
   * 订阅本机回合事件流；返回取消订阅函数。
   *
   * Renderer 业务路径须经 `sidecarEventPump` 单例订阅（App 生命周期只订一次），
   * 再 `claimSidecarTurnSink`——禁止每 turn 直接 `onEvent`（可叠 listener → live 叠字）。
   */
  onEvent(cb: (e: SidecarEventPush) => void): () => void;
  /**
   * 订阅本机履约帧（CLIENT_TOOL `*_required` / `client_tool_cancelled`）；返回取消订阅函数。
   *
   * 与 {@link onEvent} 分流：履约不是显示态，不进回合缓冲、不喂 `dispatchSSEEvent`。
   * 业务侧只在 `clientToolIngress` 单例订阅一次（多订阅 = 同一 op 重复执行）。
   */
  onFulfillFrame(cb: (e: SidecarFulfillPush) => void): () => void;
  /** 订阅 sidecar 生命周期/诊断事件；返回取消订阅函数。 */
  onStatus(cb: (e: SidecarStatusPush) => void): () => void;
}
