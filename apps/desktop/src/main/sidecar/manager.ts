import { randomUUID } from "node:crypto";
import {
  SIDECAR_CHANNELS,
  SIDECAR_QUEUE_NEED_START,
  type SidecarAccountAuth,
  type SidecarAttachRequest,
  type SidecarAttachResponse,
  type SidecarCancelQueuedTurnAck,
  type SidecarCancelQueuedTurnRequest,
  type SidecarCancelRequest,
  type SidecarCreateWorkspaceVersionRequest,
  type SidecarDebateSteerRequest,
  type SidecarDeliverMessageAck,
  type SidecarDeliverMessageRequest,
  type SidecarInference,
  type SidecarInterveneAck,
  type SidecarListBrowserSessionsRequest,
  type SidecarListBrowserSessionsResult,
  type SidecarListQueuedTurnsRequest,
  type SidecarListQueuedTurnsResult,
  type SidecarQueuedAttachment,
  type SidecarQueuedTurnItem,
  type SidecarRecoveryRequest,
  type SidecarRecoveryResponse,
  type SidecarRespondRequest,
  type SidecarRestoreTurnBaselineRequest,
  type SidecarRestoreWorkspaceVersionRequest,
  type SidecarResumeRequest,
  type SidecarRunRedirectRequest,
  type SidecarRunStopRequest,
  type SidecarStartTurnRequest,
  type SidecarStatusPush,
  type SidecarTurnFilesDiffRequest,
  type SidecarTurnFilesDiffResult,
  type SidecarTurnResult,
  type SidecarWarmAccountRulesMemoryResult,
  type SidecarWarmMcpDiscoverResult,
  type SidecarWorkspaceVersionResult,
  buildSidecarResumeRpcParams,
} from "@shared/sidecar-contract";
import { BrowserWindow, type WebContents } from "electron";
import { getDesktopBrowserBridgeCredentials } from "../browser";
import { listSessionRoots } from "../fs/roots";
import { logDesktop } from "../log-service";
import { listMcpToolsValue } from "../mcp-service";
import {
  handleOccupiedTurnSidecarFailure,
  listUnsyncedSummaries,
  recoverLocalPersistence,
  sidecarDataDir,
} from "../outbox-writeback";
import { occupyLocalTurnBegin } from "../outbox/projection";
import { SidecarEventBuffer } from "../sidecar-event-buffer";
import { SidecarClient, SidecarRpcError } from "./client";
import { buildExternalMounts } from "./externalMounts";
import { readLocalPausedRecovery } from "./recovery";
import {
  type SpawnConfig,
  type Transport,
  resolveSpawnConfig,
  spawnTransport,
} from "./transport";
import { entryKey } from "./workspace";

// 本地回合的审批门（双模式工作区 §十）。开启后，sidecar 引擎对 worker 的「碰真实
// 机器」工具（file_write / code_execute 等 GRANTABLE）挂起审批，与云端 local 模式同语义——
// 审批请求随回合事件流回 renderer，用户的决定经 `window.sidecarApi.respond` 结算回这条 stdio
// 链路（renderer 把统一结算入口 `resolveInteraction` 在本地回合改走 sidecar）。
const SIDECAR_APPROVALS_ENABLED = true;

/**
 * 本机履约帧的 JSON-RPC 通知名（Python `sidecar/fulfill_bridge.py` 同名常量）。
 * 与回合事件 `turn/event` 分开：履约走设备级中枢，不是回合显示流。
 */
const SIDECAR_FULFILL_NOTIFICATION = "fulfill/frame";

/**
 * 账号 rules/memory 暖的续期余量（ms）。
 *
 * 服务端回的 `ttlSeconds` 是那条快照**当下**的剩余寿命；扣掉这段余量再判过期，覆盖
 * 「暖回复 → 引擎 prepare 读快照」之间的间隔与时钟抖动。宁可早一点重暖：服务端快照
 * 过期后 prepare 只读缓存、不回落云端，注入直接变空（用户侧＝突然失忆）。
 */
const ACCOUNT_WARM_RENEW_MARGIN_MS = 15_000;

/**
 * 暖 RPC 本身失败时的退避（ms，取服务端降级负 TTL 同量级）。
 * 失败不再永久锁死该键——退避过后下个回合会重试。
 */
const ACCOUNT_WARM_RETRY_BACKOFF_MS = 30_000;

/**
 * 本地引擎够不着时的按人干预回执：没有活的驱动循环，请求哪儿也没去。
 * 与引擎自己给的「驱动已退出」同一句口径——用户不需要区分是进程沉睡还是循环结束。
 */
const UNREACHABLE_INTERVENE_ACK = {
  accepted: false,
  reason: "no_live_drive",
  detail: "本地引擎没在跑这批工作，这次操作没有生效。",
  queued: 0,
} as const satisfies SidecarInterveneAck;

/**
 * 服务端快照缓存键 `(user_id, folder_id)` 的桌面侧镜像（`folderId` 空白/缺省 = 裸聊，
 * 与服务端 `normalize_folder_id_param` 同规）。暖的有效期按此键记账，故切项目 / 换账号
 * 各自独立续期，不会因为「另一个键暖过」而漏暖。
 */
function accountWarmKey(
  userId: string,
  folderId: string | null | undefined,
): string {
  return `${userId}\u0000${folderId?.trim() ?? ""}`;
}

function folderIdFromAccountWarmKey(key: string): string | null {
  const i = key.indexOf("\u0000");
  const folder = i < 0 ? "" : key.slice(i + 1);
  return folder === "" ? null : folder;
}

function rootIdFromEntryKey(key: string): string {
  const i = key.indexOf("::");
  return i < 0 ? key : key.slice(0, i);
}

/**
 * 暖回复 → 本地可信新鲜窗口（ms）。缺 `ttlSeconds` / 非正数 ⇒ 0（下个回合重暖）：
 * 多暖一次只是一次 HTTP，谎报新鲜则是静默丢掉规则 / 长期记忆 / MCP 工具。
 */
function warmFreshMs(reply: unknown): number {
  const ttlSeconds = Number(
    (
      reply as
        | SidecarWarmAccountRulesMemoryResult
        | SidecarWarmMcpDiscoverResult
        | null
        | undefined
    )?.ttlSeconds,
  );
  if (!Number.isFinite(ttlSeconds) || ttlSeconds <= 0) return 0;
  return Math.max(0, ttlSeconds * 1000 - ACCOUNT_WARM_RENEW_MARGIN_MS);
}

/**
 * DesktopBrowserBridge 本回合句柄（B-Arch · 与 inference 同构）。
 * 主进程签发；经 initialize / startTurn / resume 下发，不再依赖 spawn env。
 */
function currentBrowserBridge(): { baseUrl: string; token: string } | null {
  const creds = getDesktopBrowserBridgeCredentials();
  if (!creds) return null;
  return { baseUrl: creds.baseUrl, token: creds.token };
}

interface SidecarEntry {
  client: SidecarClient;
  /** initialize 的就绪 Promise（失败则该 entry 已被逐出，需重拉）。 */
  ready: Promise<void>;
  /**
   * 该 sidecar 进程当前生效的账号 id（initialize 值；带 `userId` 的暖/回合随之刷新，
   * 与服务端 `_refresh_user_id` 同步）。用于拼 {@link accountWarmKey}。
   */
  userId: string;
  /**
   * 每个服务端快照键（{@link accountWarmKey}）的暖有效期（`Date.now()` 时间戳）。
   * 服务端过期即空注入，故这里存的是**服务端给的**剩余寿命减去续期余量，到点重暖。
   * 无票跳过不写入——晚登录/补票可再踢。
   */
  accountRulesMemoryFreshUntil: Map<string, number>;
  /** 每键在途暖（防同键并发双踢；不同键各自独立）。 */
  accountRulesMemoryWarmInflight: Map<string, Promise<void>>;
  /** 在途 MCP / account rules-memory 暖；startTurn/resume 发回合 RPC 前 await。 */
  inflightWarms: Set<Promise<void>>;
  /**
   * MCP 暖有效期（`Date.now()`）。`undefined` = 本进程还没成功记过 TTL，
   * 不在回合入口自动踢（仍由打开/登记项目显式暖）。记过之后过期即续暖。
   */
  mcpDiscoverFreshUntil?: number;
  mcpDiscoverWarmInflight?: Promise<void>;
  /** startTurn/resume 写入的续暖凭据；RPC 返回后仍保留，供 detached execution 续暖。 */
  warmLease: {
    rootId: string;
    folderId?: string | null;
    accountAuth?: SidecarAccountAuth;
    userId?: string;
  } | null;
  /** 嵌套 startTurn/resume 在途持有数（附着长回合）；与 {@link executionWarmIds} 独立。 */
  warmLeaseHolders: number;
  /** 已 `execution_detached`、尚未 `execution_completed` 的 execution_id。 */
  executionWarmIds: Set<string>;
  warmKeepaliveTimer?: ReturnType<typeof setTimeout>;
}

interface ActiveTurn {
  wc: WebContents;
  conversationId: string;
  rootId: string;
  subpath: string;
  kind: "start" | "resume";
  traceId: string;
  /** startTurn：用户行 id；resume：挂起时落库的 user 行 id（可缺省）。 */
  userMessageId?: string;
  userMessage?: string;
  /** resume 登记键 = assistant message_id；startTurn 由桌面铸造后写入。 */
  messageId?: string;
  buffer: SidecarEventBuffer;
  /** attach 零 await 段内为 true：只入缓冲、不转发（互斥不重不漏）。 */
  attaching: boolean;
  /**
   * sidecar 自发回合（如 harvest）按 cid 认领的临时登记。
   * 不进 {@link SidecarManager.findLiveTurn}，避免 refresh attach 把它当成用户活回合。
   * fulfill / D4 终态另走 ephemeral 专用路径。
   */
  ephemeral?: boolean;
}

/**
 * Electron：`isDestroyed()` 通过后到 `send` 仍可能竞态抛「已销毁」。
 * 仅识别这类竞态；其它真实错误原样上抛，避免被吞成静默丢事件。
 */
export function isDestroyedWebContentsError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    /has been destroyed/i.test(msg) ||
    /render frame was disposed/i.test(msg) ||
    /webframemain was disposed/i.test(msg)
  );
}

function isTurnEventTerminal(type: string): boolean {
  return type === "message_end" || type === "error";
}

/** Sidecar JSON-RPC `TURN_CANCELLED`（`protocol.TURN_CANCELLED` = -32001）。 */
const SIDECAR_TURN_CANCELLED = -32001;

function isSidecarTurnCancelled(err: unknown): boolean {
  if (err instanceof SidecarRpcError && err.code === SIDECAR_TURN_CANCELLED) {
    return true;
  }
  const raw = err instanceof Error ? err.message : String(err ?? "");
  const msg = raw.toLowerCase();
  return msg.includes("turn cancelled") || msg.includes("turn_cancelled");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function strField(
  row: Record<string, unknown>,
  camel: string,
  snake: string,
): string {
  const a = row[camel];
  const b = row[snake];
  if (typeof a === "string" && a.trim()) return a.trim();
  if (typeof b === "string" && b.trim()) return b.trim();
  return "";
}

function isQueuedTurnNotFound(err: unknown): boolean {
  if (err instanceof SidecarRpcError) {
    if (err.code === -32007) return true;
    const msg = err.message.toLowerCase();
    if (msg.includes("not_found") || msg.includes("404")) return true;
    if (err.code === 404) return true;
  }
  const raw = err instanceof Error ? err.message : String(err ?? "");
  return /not_found|404|排队项不存在/i.test(raw);
}

function parseDeliverMessageAck(reply: unknown): SidecarDeliverMessageAck {
  if (!isRecord(reply)) {
    throw new Error("本地引擎回执无效");
  }
  const status = String(reply.status ?? reply.kind ?? "").trim();
  if (status === "received") {
    const interjectionId = strField(reply, "interjectionId", "interjection_id");
    if (!interjectionId) throw new Error("本地引擎回执缺少 interjectionId");
    return { status: "received", interjectionId };
  }
  if (status === "queued") {
    const queueId = strField(reply, "queueId", "queue_id");
    if (!queueId) throw new Error("本地引擎回执缺少 queueId");
    const position =
      typeof reply.position === "number" && reply.position >= 1
        ? reply.position
        : 1;
    const queueDepthRaw = reply.queueDepth ?? reply.queue_depth;
    const queueDepth =
      typeof queueDepthRaw === "number" && queueDepthRaw >= 1
        ? queueDepthRaw
        : 1;
    const degraded = reply.degradedFrom ?? reply.degraded_from;
    return {
      status: "queued",
      queueId,
      position,
      queueDepth,
      ...(degraded === "steer" ? { degradedFrom: "steer" as const } : {}),
    };
  }
  if (status === "blocked") {
    return {
      status: "blocked",
      ...(typeof reply.code === "string" && reply.code
        ? { code: reply.code }
        : {}),
    };
  }
  throw new Error("本地引擎回执无法识别");
}

function parseCancelQueuedTurnAck(reply: unknown): SidecarCancelQueuedTurnAck {
  if (isRecord(reply) && reply.ok === true) {
    return { status: "cancelled" };
  }
  throw new Error("本地引擎取消回执无效");
}

function parseQueuedTurnItems(reply: unknown): SidecarQueuedTurnItem[] {
  const rawItems = Array.isArray(reply)
    ? reply
    : isRecord(reply) && Array.isArray(reply.items)
      ? reply.items
      : null;
  if (!rawItems) return [];
  const items: SidecarQueuedTurnItem[] = [];
  for (const raw of rawItems) {
    if (!isRecord(raw)) continue;
    const queueId = strField(raw, "queueId", "queue_id");
    if (!queueId) continue;
    const position =
      typeof raw.position === "number" && raw.position >= 1
        ? raw.position
        : items.length + 1;
    const interjectionId = strField(raw, "interjectionId", "interjection_id");
    const degraded = raw.degradedFrom ?? raw.degraded_from;
    const attachments = raw.attachments;
    const mentions = raw.agentMentions ?? raw.agent_mentions;
    items.push({
      queueId,
      content: typeof raw.content === "string" ? raw.content : "",
      position,
      ...(interjectionId ? { interjectionId } : {}),
      ...(degraded === "steer" ? { degradedFrom: "steer" as const } : {}),
      ...(Array.isArray(attachments)
        ? { attachments: attachments as SidecarQueuedTurnItem["attachments"] }
        : {}),
      ...(Array.isArray(mentions)
        ? { agentMentions: mentions as SidecarQueuedTurnItem["agentMentions"] }
        : {}),
    });
  }
  return items;
}

/** 流式/状态热路径：先查 isDestroyed，再 try/send，只吞销毁竞态。 */
function safeWcSend(wc: WebContents, channel: string, payload: unknown): void {
  if (wc.isDestroyed()) return;
  try {
    wc.send(channel, payload);
  } catch (err) {
    if (isDestroyedWebContentsError(err)) return;
    throw err;
  }
}

/**
 * 管理每个授权根的 sidecar：懒拉起 + 初始化、回合事件路由、cancel/respond、退出清理。
 *
 * `spawnFn` 可注入（默认真实 `spawnTransport`），便于单测用假传输驱动整条链路。
 */
export class SidecarManager {
  private readonly entries = new Map<string, SidecarEntry>();
  private readonly turns = new Map<string, ActiveTurn>();
  /**
   * 原 startTurn/resume finally 会 `turns.delete`；harvest 等自发 `turn/event`
   * 带着新 turnId 到来时仍须把事件送到该会话窗口。按 cid 记住最近 wc。
   */
  private readonly lastWindowByCid = new Map<
    string,
    { wc: WebContents; rootId: string; subpath: string }
  >();

  constructor(
    private readonly spawnFn: (
      config: SpawnConfig,
    ) => Transport = spawnTransport,
  ) {}

  /**
   * 拉起（或复用）某 `root + subpath` 的 sidecar，并完成一次性 initialize。
   *
   * `workspaceRoot` 已是绑定根目录（容器根 absPath 拼上子路径，由 IPC handler 算好），即引擎本
   * 回合的工作区；缓存键含 subpath，故同容器根下的不同子路径工作区互不串台。状态推送仍按容器
   * `rootId`（与 renderer 的 sidecarStatus / `takeRecentSidecarFailure(rootId)` 对齐——诊断按根聚合）。
   *
   * 不在此处踢 `warmCodeIndex` / `warmMcpDiscover`：服务端 `initialize` 已 schedule 代码
   * 索引；MCP list 须桌面打开/登记显式 IPC（{@link warmCodeIndex} /
   * {@link warmMcpDiscover}）。每回合 ensure（含 cache hit）再踢会与 prepare 叠跑。
   *
   * `warmAccountRulesMemory` / `warmMcpDiscover`：ensure 本身不踢；
   * {@link startTurn} / {@link resume}（及显式暖）在有票且该快照键**已过期**时续暖
   * （见 `accountRulesMemoryFreshUntil` / `mcpDiscoverFreshUntil`）；无票跳过不锁死。
   * 回合发 RPC 前 await 在途 warm（失败只记日志）。keepalive 绑 **detached
   * execution 存活期**（`execution_detached` → `execution_completed` 的
   * `turn/event`），不绑 startTurn RPC 在途：CEO 已 pause 返回后团队仍跑时继续续暖。
   */
  private ensure(
    rootId: string,
    subpath: string,
    workspaceRoot: string,
    inference: SidecarInference | undefined,
    /** 首次 initialize 的账号 id；缺省 / 空 → ``"local"``。已有登录 id 时应传入，
     *  避免暖路径无故钉死 local。长活进程后续回合以 startTurn/resume 的 per-turn
     *  ``userId`` + RPC ``_refresh_user_id`` 为准。 */
    userId?: string,
  ): SidecarEntry {
    const key = entryKey(rootId, subpath);
    const existing = this.entries.get(key);
    if (existing) {
      return existing;
    }

    const transport = this.spawnFn(resolveSpawnConfig());
    const client = new SidecarClient(transport);
    client.onNotification((method, params) =>
      this.onNotification(method, params),
    );
    client.onClosed((err) => {
      const gone = this.entries.get(key);
      if (gone) this.clearWarmKeepaliveTimer(gone);
      this.entries.delete(key);
      this.pushStatus({ rootId, phase: "exited", detail: err.message });
      this.finalizeEphemeralTurns(rootId, subpath, err);
      void recoverLocalPersistence();
    });

    const ready = client
      .request("initialize", {
        userId: userId?.trim() || "local",
        workspaceRoot,
        approvalsEnabled: SIDECAR_APPROVALS_ENABLED,
        // The app-private data dir for durable pause frames (双模式工作区 §一.1):
        // its presence flips the engine's local paused-turn store on.
        dataDir: sidecarDataDir(),
        ...(inference ? { inference } : {}),
        // Always send key (null when Bridge not Ready) so sidecar clears sticky env.
        browserBridge: currentBrowserBridge(),
      })
      .then(() => {
        this.pushStatus({ rootId, phase: "spawned" });
      })
      .catch((err: unknown) => {
        // 初始化失败（uv/venv 找不到、引擎导入失败等）——逐出，下次重拉；上抛给 startTurn。
        const gone = this.entries.get(key);
        if (gone) this.clearWarmKeepaliveTimer(gone);
        this.entries.delete(key);
        const detail = err instanceof Error ? err.message : String(err);
        this.pushStatus({ rootId, phase: "error", detail });
        client.dispose();
        throw err instanceof Error ? err : new Error(detail);
      });

    const entry: SidecarEntry = {
      client,
      ready,
      userId: userId?.trim() || "local",
      accountRulesMemoryFreshUntil: new Map(),
      accountRulesMemoryWarmInflight: new Map(),
      inflightWarms: new Set(),
      warmLease: null,
      warmLeaseHolders: 0,
      executionWarmIds: new Set(),
    };
    this.entries.set(key, entry);
    return entry;
  }

  /** 登记在途暖；settled 后移出，供 startTurn/resume await。 */
  private trackWarm(entry: SidecarEntry, work: Promise<void>): Promise<void> {
    const tracked = work.finally(() => {
      entry.inflightWarms.delete(tracked);
    });
    entry.inflightWarms.add(tracked);
    return tracked;
  }

  /**
   * 发回合 RPC 前等在途 MCP / account warm 落定。
   * 失败只记日志后继续（此后 cache miss＝真没有）。
   */
  private async awaitInflightWarms(
    entry: SidecarEntry,
    rootId: string,
  ): Promise<void> {
    const pending = [...entry.inflightWarms];
    if (pending.length === 0) return;
    const results = await Promise.allSettled(pending);
    for (const r of results) {
      if (r.status === "rejected") {
        const detail =
          r.reason instanceof Error ? r.reason.message : String(r.reason);
        logDesktop({
          level: "warn",
          event: "sidecar.inflight_warm_await_failed",
          fields: { rootId, detail },
        });
      }
    }
  }

  /**
   * 在某根的 sidecar 上跑一个回合；Promise 在回合结束时 resolve（携带最终结果），
   * 过程事件经 `sidecar:event` 推给 `wc`。
   */
  async startTurn(
    wc: WebContents,
    req: SidecarStartTurnRequest,
    workspaceRoot: string,
  ): Promise<SidecarTurnResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      req.inference,
      req.userId,
    );
    await entry.ready; // 初始化失败则在此抛出 → renderer 据此降级
    // 有票且快照已过期则续暖 account rules/memory；MCP 仅在曾经暖过且过期时续。
    this.maybeKickAccountRulesMemoryWarm(entry, req.rootId, {
      folderId: req.folderId,
      accountAuth: req.accountAuth,
      userId: req.userId,
    });
    this.maybeKickMcpDiscoverWarm(entry, req.rootId, { userId: req.userId });
    await this.awaitInflightWarms(entry, req.rootId);
    this.beginExecutionWarmLease(entry, {
      rootId: req.rootId,
      folderId: req.folderId,
      accountAuth: req.accountAuth,
      userId: req.userId,
    });

    this.dropEphemeralTurns(req.conversationId);
    const messageId = req.messageId.trim();
    let occupied = false;
    try {
      occupied = await occupyLocalTurnBegin({
        conversationId: req.conversationId,
        userMessage: req.userMessage,
        userMessageId: req.userMessageId,
        messageId,
        traceId: req.traceId,
        regenerate: req.regenerate,
        agentMentions: req.replaceMaterials
          ? (req.agentMentions ?? [])
          : req.agentMentions,
        attachments: req.replaceMaterials
          ? (req.attachments ?? [])
          : undefined,
      });
      if (!occupied) {
        throw new Error("云端占位失败，本地回合未启动");
      }
      this.turns.set(req.turnId, {
        wc,
        conversationId: req.conversationId,
        rootId: req.rootId,
        subpath: req.subpath ?? "",
        kind: "start",
        traceId: req.traceId,
        userMessageId: req.userMessageId,
        userMessage: req.userMessage,
        messageId,
        buffer: new SidecarEventBuffer(),
        attaching: false,
      });
      this.rememberWindow(
        req.conversationId,
        wc,
        req.rootId,
        req.subpath ?? "",
      );
      const externalMounts = buildExternalMounts(
        listSessionRoots(req.conversationId),
      );
      const result = await entry.client.request("startTurn", {
        turnId: req.turnId,
        conversationId: req.conversationId,
        traceId: req.traceId,
        userMessage: req.userMessage,
        // Outbox idempotency anchor (as-built: 双模式工作区 §10.3).
        userMessageId: req.userMessageId,
        messageId,
        // Omit when renderer did not confirm a window — ``[]`` would look like
        // a new chat and skip / empty-run instead of letting sidecar fetch.
        ...(req.history !== undefined ? { history: req.history } : {}),
        ...(req.agentMentions && req.agentMentions.length > 0
          ? { agentMentions: req.agentMentions }
          : {}),
        ...(req.queueId ? { queueId: req.queueId } : {}),
        ...(req.attachments && req.attachments.length > 0
          ? { attachments: req.attachments }
          : {}),
        // Per-turn account id (long-lived sidecar may have initialized as "local").
        ...(req.userId?.trim() ? { userId: req.userId.trim() } : {}),
        // W3: session read-only mounts (abs paths stay in main → sidecar only).
        ...(externalMounts.length > 0 ? { externalMounts } : {}),
        // Re-send the current cloud-proxy token every turn: the sidecar is long-lived
        // but the token rotates (12h TTL), so the engine adopts the fresh one per turn
        // (initialize-time creds would otherwise 401 after expiry).
        ...(req.inference ? { inference: req.inference } : {}),
        // folders 窄票（定案甲）：与 inference 并列按回合重送，供云名册工具鉴权。
        ...(req.foldersAuth ? { foldersAuth: req.foldersAuth } : {}),
        // account 窄票（定案 R3a）：与 folders 并列按回合重送，供搜/读云对话日志。
        ...(req.accountAuth ? { accountAuth: req.accountAuth } : {}),
        // Same for DesktopBrowserBridge (B-Arch): refresh every turn; null = 未装配.
        browserBridge: currentBrowserBridge(),
        // 会话权限轴按回合随送：中途切换后下一回合即生效。
        ...(req.permissionAxes ? { permissionAxes: req.permissionAxes } : {}),
        // 项目归属：键始终下发（含 null=裸聊），使引擎优先用 params、旧桌面缺键才查库。
        folderId: req.folderId ?? null,
        // 项目本地绑定（FolderMeta 同形）：进 RPC 供拼 workspace key；与 rootId/subpath
        // 寻址分离（后者只经 ensure，不进本 params）。
        localRootId: req.localRootId ?? null,
        localSubpath: req.localSubpath ?? null,
      });
      this.emitSyntheticTerminalIfNeeded(req.turnId, "message_end");
      return result as SidecarTurnResult;
    } catch (err) {
      if (occupied) {
        await handleOccupiedTurnSidecarFailure({
          conversationId: req.conversationId,
          userMessageId: req.userMessageId,
          messageId,
        });
      }
      if (this.turns.has(req.turnId)) {
        this.emitSyntheticTerminalIfNeeded(
          req.turnId,
          isSidecarTurnCancelled(err) ? "message_end" : "error",
          err,
        );
      }
      throw err;
    } finally {
      this.turns.delete(req.turnId);
      this.endExecutionWarmLease(entry);
    }
  }

  /**
   * 探活某 `root + subpath` 的 sidecar：拉起（或复用）进程并完成 initialize 握手即返回，不跑
   * 任何回合。用于在首次真正走 sidecar 前提前验证本机环境（Python / venv / 引擎导入 / 工作区
   * 绑定）能起得来；握手成功留存的进程正好被随后的首个回合复用（`ensure` 命中缓存、零额外拉
   * 起）。失败时 `ensure` 的 `ready` 已 pushStatus(error) + 逐出该 entry，错误上抛给调用方。
   */
  async probe(
    rootId: string,
    subpath: string,
    workspaceRoot: string,
  ): Promise<void> {
    // 不传 inference：探活只验证环境能起；真实回合的 startTurn 会按回合重发云代理凭据。
    const entry = this.ensure(rootId, subpath, workspaceRoot, undefined);
    await entry.ready;
  }

  /**
   * 打开/登记本机项目后：ensure sidecar + 等待 initialize，再显式踢 ``warmCodeIndex`` RPC。
   * 与 {@link probe} 同形；语义上专供「打开项目必走到」暖索引，不挡 UI（RPC 失败只记日志）。
   */
  async warmCodeIndex(
    rootId: string,
    subpath: string,
    workspaceRoot: string,
  ): Promise<void> {
    const entry = this.ensure(rootId, subpath, workspaceRoot, undefined);
    await entry.ready;
    try {
      await entry.client.request("warmCodeIndex", {});
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : String(err);
      logDesktop({
        level: "warn",
        event: "sidecar.warm_code_index_failed",
        fields: { rootId, detail },
      });
    }
  }

  /**
   * 打开/登记本机项目后：ensure + initialize → 本机 mcp-service list_tools →
   * 显式踢 ``warmMcpDiscover`` 把 ``{servers}`` seed 进 sidecar 进程缓存。
   * 须传登录 ``userId``（与 prepare cache_scope 对齐）。不挡 UI；list/RPC 失败只记日志。
   * 回合 ensure 不自动踢；promise 登记到 entry.inflightWarms 供 startTurn await。
   */
  async warmMcpDiscover(
    rootId: string,
    subpath: string,
    workspaceRoot: string,
    opts: { userId?: string } = {},
  ): Promise<void> {
    const entry = this.ensure(
      rootId,
      subpath,
      workspaceRoot,
      undefined,
      opts.userId,
    );
    await entry.ready;
    await this.kickMcpDiscover(entry, rootId, opts);
  }

  /**
   * 打开/登记本机项目后：ensure + initialize → 显式踢 ``warmAccountRulesMemory``，
   * 用 account 窄票让 sidecar 自拉 rules/memory 快照进进程缓存。无票则跳过（不发 RPC、
   * 不记有效期）。不挡 UI；失败只记日志。有效期与 startTurn/resume 的续暖共用记账。
   */
  async warmAccountRulesMemory(
    rootId: string,
    subpath: string,
    workspaceRoot: string,
    opts: {
      folderId?: string | null;
      accountAuth?: SidecarAccountAuth;
      userId?: string;
      /** 文件页写后：忽略 TTL，重新拉取。打开/回合续暖保持 skip-if-fresh。 */
      force?: boolean;
    } = {},
  ): Promise<void> {
    const entry = this.ensure(
      rootId,
      subpath,
      workspaceRoot,
      undefined,
      opts.userId,
    );
    await entry.ready;
    await this.maybeKickAccountRulesMemoryWarm(entry, rootId, opts);
  }

  /**
   * 本机文件页 / 记忆写成功后：对**已经在跑**的 sidecar 强制重暖 rules/memory 快照。
   * 不 spawn、不碰没起过的根。TTL 窗口内也重拉——写刚进云，skip-if-fresh 会把新正文丢掉。
   */
  async refreshLiveAccountRulesMemory(opts: {
    accountAuth?: SidecarAccountAuth;
    userId?: string;
  }): Promise<void> {
    if (!opts.accountAuth) return;
    const jobs: Promise<void>[] = [];
    for (const [key, entry] of [...this.entries]) {
      try {
        await entry.ready;
      } catch {
        continue;
      }
      const rootId = rootIdFromEntryKey(key);
      const userId = opts.userId?.trim() || entry.userId;
      const folderIds = new Set<string | null>();
      if (entry.warmLease) {
        folderIds.add(entry.warmLease.folderId ?? null);
      }
      for (const warmKey of [
        ...entry.accountRulesMemoryFreshUntil.keys(),
        ...entry.accountRulesMemoryWarmInflight.keys(),
      ]) {
        folderIds.add(folderIdFromAccountWarmKey(warmKey));
      }
      if (folderIds.size === 0) folderIds.add(null);
      for (const folderId of folderIds) {
        const work = this.maybeKickAccountRulesMemoryWarm(entry, rootId, {
          folderId,
          accountAuth: opts.accountAuth,
          userId,
          force: true,
        });
        if (work) jobs.push(work);
      }
    }
    await Promise.all(jobs);
  }

  /**
   * 有票且该快照键已过期时续暖 ``warmAccountRulesMemory``（登记 inflightWarms），
   * 返回本键的在途暖（无需暖 / 无票时 undefined）。
   *
   * 服务端快照有 TTL，过期后 prepare 只读缓存 → 空注入（规则与长期记忆整体消失），
   * 所以「暖过一次」不能当永久有效：按服务端回的 `ttlSeconds` 记有效期，到期重暖。
   * 无票只记跳过、不写有效期（晚登录可再踢）；RPC 失败按短退避记，不永久锁死。
   */
  private maybeKickAccountRulesMemoryWarm(
    entry: SidecarEntry,
    rootId: string,
    opts: {
      folderId?: string | null;
      accountAuth?: SidecarAccountAuth;
      userId?: string;
      force?: boolean;
    },
  ): Promise<void> | undefined {
    const userId = opts.userId?.trim();
    // 服务端按 per-turn userId 刷新缓存 scope；键要跟着走，否则续期记在旧账号名下。
    if (userId) entry.userId = userId;
    const key = accountWarmKey(entry.userId, opts.folderId);
    const inflight = entry.accountRulesMemoryWarmInflight.get(key);
    if (inflight && !opts.force) return inflight;
    if (
      !opts.force &&
      (entry.accountRulesMemoryFreshUntil.get(key) ?? 0) > Date.now()
    ) {
      return undefined;
    }
    if (!opts.accountAuth) {
      logDesktop({
        level: "info",
        event: "sidecar.warm_account_rules_memory_skipped",
        fields: { rootId, detail: "no_account_auth" },
      });
      return undefined;
    }
    if (inflight && opts.force) {
      // Self-ref in then(): const init trips TS2454.
      // biome-ignore lint/style/useConst: assigned after the promise closes over itself
      let joined!: Promise<void>;
      joined = inflight
        .catch(() => undefined)
        .then(() => {
          entry.accountRulesMemoryFreshUntil.delete(key);
          if (entry.accountRulesMemoryWarmInflight.get(key) === joined) {
            entry.accountRulesMemoryWarmInflight.delete(key);
          }
          return this.maybeKickAccountRulesMemoryWarm(entry, rootId, {
            folderId: opts.folderId,
            accountAuth: opts.accountAuth,
            userId: opts.userId,
            force: true,
          });
        })
        .then(() => undefined);
      entry.accountRulesMemoryWarmInflight.set(key, joined);
      this.trackWarm(entry, joined);
      return joined;
    }
    // Self-ref in finally: const + IIFE trips TS2454.
    // biome-ignore lint/style/useConst: assigned after the promise closes over itself
    let work!: Promise<void>;
    work = (async () => {
      let freshMs = ACCOUNT_WARM_RETRY_BACKOFF_MS;
      try {
        const reply = await entry.client.request("warmAccountRulesMemory", {
          folderId: opts.folderId ?? null,
          accountAuth: opts.accountAuth,
          ...(userId ? { userId } : {}),
        });
        freshMs = warmFreshMs(reply);
      } catch (err: unknown) {
        const detail = err instanceof Error ? err.message : String(err);
        logDesktop({
          level: "warn",
          event: "sidecar.warm_account_rules_memory_failed",
          fields: { rootId, detail },
        });
      } finally {
        entry.accountRulesMemoryFreshUntil.set(key, Date.now() + freshMs);
        if (entry.accountRulesMemoryWarmInflight.get(key) === work) {
          entry.accountRulesMemoryWarmInflight.delete(key);
        }
      }
    })();
    entry.accountRulesMemoryWarmInflight.set(key, work);
    this.trackWarm(entry, work);
    return work;
  }

  /**
   * 显式 / 周期续暖 MCP 列表。失败只记日志；成功按回复 ttl 记账。
   * 打开项目走这条（无 TTL 门槛）；回合入口走 {@link maybeKickMcpDiscoverWarm}。
   */
  private kickMcpDiscover(
    entry: SidecarEntry,
    rootId: string,
    opts: { userId?: string },
  ): Promise<void> {
    const inflight = entry.mcpDiscoverWarmInflight;
    if (inflight) return inflight;
    const userId = opts.userId?.trim();
    if (userId) entry.userId = userId;
    const work = (async () => {
      let freshMs: number | undefined;
      try {
        const listed = await listMcpToolsValue();
        const servers = Array.isArray(listed.servers) ? listed.servers : [];
        const reply = await entry.client.request("warmMcpDiscover", {
          servers,
          ...(userId ? { userId } : {}),
        });
        freshMs = warmFreshMs(reply);
      } catch (err: unknown) {
        const detail = err instanceof Error ? err.message : String(err);
        logDesktop({
          level: "warn",
          event: "sidecar.warm_mcp_discover_failed",
          fields: { rootId, detail },
        });
        if (entry.mcpDiscoverFreshUntil !== undefined) {
          freshMs = ACCOUNT_WARM_RETRY_BACKOFF_MS;
        }
      } finally {
        entry.mcpDiscoverWarmInflight = undefined;
        if (freshMs !== undefined) {
          entry.mcpDiscoverFreshUntil = Date.now() + freshMs;
        }
      }
    })();
    entry.mcpDiscoverWarmInflight = work;
    this.trackWarm(entry, work);
    return work;
  }

  /**
   * 本 sidecar 曾经暖过 MCP 且 TTL 已过期时续暖。从未暖过则跳过
   * （仍由打开/登记项目显式 {@link warmMcpDiscover} 做第一次）。
   */
  private maybeKickMcpDiscoverWarm(
    entry: SidecarEntry,
    rootId: string,
    opts: { userId?: string },
  ): Promise<void> | undefined {
    if (entry.mcpDiscoverFreshUntil === undefined) return undefined;
    if (entry.mcpDiscoverWarmInflight) return entry.mcpDiscoverWarmInflight;
    if (entry.mcpDiscoverFreshUntil > Date.now()) return undefined;
    return this.kickMcpDiscover(entry, rootId, opts);
  }

  private beginExecutionWarmLease(
    entry: SidecarEntry,
    lease: SidecarEntry["warmLease"],
  ): void {
    entry.warmLease = lease;
    entry.warmLeaseHolders += 1;
    this.scheduleWarmKeepalive(entry);
  }

  private endExecutionWarmLease(entry: SidecarEntry): void {
    entry.warmLeaseHolders = Math.max(0, entry.warmLeaseHolders - 1);
    this.scheduleWarmKeepalive(entry);
  }

  /** Keepalive while a turn RPC is in-flight **or** a detached execution is live. */
  private isWarmKeepaliveLive(entry: SidecarEntry): boolean {
    return entry.warmLeaseHolders > 0 || entry.executionWarmIds.size > 0;
  }

  private onExecutionWarmLifecycle(
    type: string,
    params: Record<string, unknown>,
    payload: unknown,
  ): void {
    const fromPayload =
      payload && typeof payload === "object"
        ? String(
            (payload as { conversation_id?: unknown }).conversation_id ?? "",
          ).trim()
        : "";
    const conversationId =
      String(params.conversationId ?? "").trim() || fromPayload;
    const executionId =
      payload && typeof payload === "object"
        ? String(
            (payload as { execution_id?: unknown }).execution_id ?? "",
          ).trim()
        : "";
    if (!executionId) return;
    const resolved = conversationId
      ? this.resolveConversationWindow(conversationId)
      : null;
    if (!resolved) return;
    const entry = this.entries.get(entryKey(resolved.rootId, resolved.subpath));
    if (!entry) return;
    if (type === "execution_detached") {
      entry.executionWarmIds.add(executionId);
    } else {
      entry.executionWarmIds.delete(executionId);
    }
    this.scheduleWarmKeepalive(entry);
  }

  private clearWarmKeepaliveTimer(entry: SidecarEntry): void {
    if (entry.warmKeepaliveTimer !== undefined) {
      clearTimeout(entry.warmKeepaliveTimer);
      entry.warmKeepaliveTimer = undefined;
    }
  }

  /**
   * 按快照剩余寿命调度下一次续暖。租约跟 detached execution 存活期
   * （及附着回合 RPC 在途），不在 startTurn finally 里无条件停。
   */
  private scheduleWarmKeepalive(entry: SidecarEntry, afterTick = false): void {
    this.clearWarmKeepaliveTimer(entry);
    if (!this.isWarmKeepaliveLive(entry)) return;
    if (!entry.warmLease) return;
    const dueAt = this.nextWarmDueAt(entry);
    if (!Number.isFinite(dueAt)) return;
    let delay = Math.max(0, dueAt - Date.now());
    // 缺 ttl / 刚踢完仍到期：不要 0ms 空转猛踢，按失败退避再探。
    if (afterTick && delay === 0) delay = ACCOUNT_WARM_RETRY_BACKOFF_MS;
    entry.warmKeepaliveTimer = setTimeout(() => {
      entry.warmKeepaliveTimer = undefined;
      void this.tickWarmKeepalive(entry);
    }, delay);
  }

  private nextWarmDueAt(entry: SidecarEntry): number {
    const lease = entry.warmLease;
    const dues: number[] = [];
    if (lease?.accountAuth) {
      const key = accountWarmKey(entry.userId, lease.folderId);
      dues.push(entry.accountRulesMemoryFreshUntil.get(key) ?? 0);
    }
    if (entry.mcpDiscoverFreshUntil !== undefined) {
      dues.push(entry.mcpDiscoverFreshUntil);
    }
    if (dues.length === 0) return Number.POSITIVE_INFINITY;
    return Math.min(...dues);
  }

  private async tickWarmKeepalive(entry: SidecarEntry): Promise<void> {
    if (!this.isWarmKeepaliveLive(entry)) return;
    const lease = entry.warmLease;
    if (!lease) return;
    logDesktop({
      level: "info",
      event: "sidecar.warm_cache_keepalive",
      fields: { rootId: lease.rootId },
    });
    this.maybeKickAccountRulesMemoryWarm(entry, lease.rootId, {
      folderId: lease.folderId,
      accountAuth: lease.accountAuth,
      userId: lease.userId,
    });
    this.maybeKickMcpDiscoverWarm(entry, lease.rootId, {
      userId: lease.userId,
    });
    await this.awaitInflightWarms(entry, lease.rootId);
    this.scheduleWarmKeepalive(entry, true);
  }

  /** A1+ 本机真 diff：ensure sidecar → `turnFilesDiff` RPC（相对本地基线 zip）。 */
  async turnFilesDiff(
    req: SidecarTurnFilesDiffRequest,
    workspaceRoot: string,
  ): Promise<SidecarTurnFilesDiffResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      undefined,
    );
    await entry.ready;
    const params: Record<string, unknown> = { messageId: req.messageId };
    if (req.baselineSnapshotId) {
      params.baselineSnapshotId = req.baselineSnapshotId;
    }
    return entry.client.request(
      "turnFilesDiff",
      params,
    ) as Promise<SidecarTurnFilesDiffResult>;
  }

  /** A2′ 本机回退：ensure sidecar → `restoreTurnBaseline`（unzip，不经云）。 */
  async restoreTurnBaseline(
    req: SidecarRestoreTurnBaselineRequest,
    workspaceRoot: string,
  ): Promise<void> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      undefined,
    );
    await entry.ready;
    await entry.client.request("restoreTurnBaseline", {
      snapshotId: req.snapshotId,
    });
  }

  /** 本地留版本：ensure sidecar → `createWorkspaceVersion`（zip 只在 Python 侧）。 */
  async createWorkspaceVersion(
    req: SidecarCreateWorkspaceVersionRequest,
    workspaceRoot: string,
  ): Promise<SidecarWorkspaceVersionResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      undefined,
    );
    await entry.ready;
    return entry.client.request("createWorkspaceVersion", {
      name: req.name,
    }) as Promise<SidecarWorkspaceVersionResult>;
  }

  /** 本地恢复命名版本：ensure sidecar → `restoreWorkspaceVersion`（overlay unzip）。 */
  async restoreWorkspaceVersion(
    req: SidecarRestoreWorkspaceVersionRequest,
    workspaceRoot: string,
  ): Promise<SidecarWorkspaceVersionResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      undefined,
    );
    await entry.ready;
    return entry.client.request("restoreWorkspaceVersion", {
      versionId: req.versionId,
    }) as Promise<SidecarWorkspaceVersionResult>;
  }

  /** Local hydrate: ensure sidecar → `listBrowserSessions`（同进程 Registry）。 */
  async listBrowserSessions(
    req: SidecarListBrowserSessionsRequest,
    workspaceRoot: string,
  ): Promise<SidecarListBrowserSessionsResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      undefined,
    );
    await entry.ready;
    return entry.client.request("listBrowserSessions", {
      conversationId: req.conversationId,
    }) as Promise<SidecarListBrowserSessionsResult>;
  }

  /**
   * 续跑一个持久挂起的本地回合（结构化挂起 2b）。
   *
   * 与 `startTurn` 同构：拉起 / 复用该根 sidecar，claim 本机帧并跑 `resume_chat_pipeline`，
   * Promise 在续跑结束时携最终结果 resolve（供 renderer 回写云端），过程事件经 `sidecar:event`
   * 推回。事件路由键用 message_id（一回合至多一个持久挂起）。
   */
  async resume(
    wc: WebContents,
    req: SidecarResumeRequest,
    workspaceRoot: string,
    inference: SidecarInference | undefined,
  ): Promise<SidecarTurnResult> {
    const entry = this.ensure(
      req.rootId,
      req.subpath ?? "",
      workspaceRoot,
      inference,
      req.userId,
    );
    await entry.ready;
    // 同 startTurn：有票按 TTL 续暖，发 resume RPC 前 await 在途 warm。
    this.maybeKickAccountRulesMemoryWarm(entry, req.rootId, {
      folderId: req.folderId,
      accountAuth: req.accountAuth,
      userId: req.userId,
    });
    this.maybeKickMcpDiscoverWarm(entry, req.rootId, { userId: req.userId });
    await this.awaitInflightWarms(entry, req.rootId);
    this.beginExecutionWarmLease(entry, {
      rootId: req.rootId,
      folderId: req.folderId,
      accountAuth: req.accountAuth,
      userId: req.userId,
    });

    this.dropEphemeralTurns(req.conversationId);
    this.turns.set(req.messageId, {
      wc,
      conversationId: req.conversationId,
      rootId: req.rootId,
      subpath: req.subpath ?? "",
      kind: "resume",
      traceId: req.traceId,
      userMessageId: req.userMessageId,
      messageId: req.messageId,
      buffer: new SidecarEventBuffer(),
      attaching: false,
    });
    this.rememberWindow(req.conversationId, wc, req.rootId, req.subpath ?? "");
    try {
      const externalMounts = buildExternalMounts(
        listSessionRoots(req.conversationId),
      );
      const result = await entry.client.request("resume", {
        ...buildSidecarResumeRpcParams(
          req,
          inference,
          currentBrowserBridge(),
          req.foldersAuth,
          req.accountAuth,
        ),
        ...(externalMounts.length > 0 ? { externalMounts } : {}),
      });
      this.emitSyntheticTerminalIfNeeded(req.messageId, "message_end");
      return result as SidecarTurnResult;
    } catch (err) {
      this.emitSyntheticTerminalIfNeeded(
        req.messageId,
        isSidecarTurnCancelled(err) ? "message_end" : "error",
        err,
      );
      throw err;
    } finally {
      this.turns.delete(req.messageId);
      this.endExecutionWarmLease(entry);
    }
  }

  /**
   * Local recovery query: live turn + outbox unsynced + paused frames. Zero spawn.
   */
  async recovery(
    req: SidecarRecoveryRequest,
  ): Promise<SidecarRecoveryResponse> {
    const live = this.findLiveTurn(req.conversationId);
    const [unsynced, localPaused] = await Promise.all([
      listUnsyncedSummaries(req.conversationId),
      readLocalPausedRecovery(req.conversationId),
    ]);
    const { paused, pausedRuns } = localPaused;
    // Exclude the live turn's open row — D5 projects ready + dead-open only;
    // live content comes from attach replay.
    const filtered = live
      ? unsynced.filter((u) => {
          if (live.turn.kind === "start" && live.turn.userMessageId) {
            return u.user_message_id !== live.turn.userMessageId;
          }
          if (live.turn.kind === "resume" && live.turn.messageId) {
            return u.message_id !== live.turn.messageId;
          }
          return true;
        })
      : unsynced;
    let queuedTurns: SidecarQueuedTurnItem[] | undefined;
    const process = this.findSidecarProcess(req.conversationId);
    if (process) {
      try {
        queuedTurns = parseQueuedTurnItems(
          await process.client.request("listQueuedTurns", {
            conversationId: req.conversationId,
          }),
        );
      } catch {
        // 问不到本机队 ≠ 空队：omit，hydrate 不得冲掉已 keep 的条。
      }
    }
    logDesktop({
      level: "info",
      event: "sidecar.recovery",
      fields: {
        conversation_id: req.conversationId,
        live_running: live !== null,
        unsynced_count: filtered.length,
        paused_count: paused.length,
        paused_runs_count: Object.keys(pausedRuns).length,
        queued_turns_count: queuedTurns?.length ?? null,
      },
    });
    return {
      liveRunning: live !== null,
      ...(live ? { turnId: live.turnId } : {}),
      unsynced: filtered,
      paused,
      ...(Object.keys(pausedRuns).length > 0 ? { pausedRuns } : {}),
      ...(queuedTurns !== undefined ? { queuedTurns } : {}),
    };
  }

  /**
   * Rebind the live turn's WebContents and snapshot the event buffer (D4).
   *
   * **Zero-await hard constraint** between rebind and snapshot: every event is
   * either in the returned snapshot or forwarded to the new wc — never both,
   * never lost. Callers must not insert awaits in this method.
   */
  attach(wc: WebContents, req: SidecarAttachRequest): SidecarAttachResponse {
    const live = this.findLiveTurn(req.conversationId);
    if (!live) {
      logDesktop({
        level: "info",
        event: "sidecar.attach",
        fields: {
          conversation_id: req.conversationId,
          attached: false,
          buffer_length: 0,
        },
      });
      return { attached: false };
    }
    // --- zero-await section (do not await) ---
    live.turn.attaching = true;
    live.turn.wc = wc;
    this.rememberWindow(
      req.conversationId,
      wc,
      live.turn.rootId,
      live.turn.subpath,
    );
    const events = live.turn.buffer.snapshot();
    live.turn.attaching = false;
    // --- end zero-await section ---
    logDesktop({
      level: "info",
      event: "sidecar.attach",
      fields: {
        conversation_id: req.conversationId,
        attached: true,
        turn_id: live.turnId,
        buffer_length: events.length,
      },
    });
    return {
      attached: true,
      turnId: live.turnId,
      rootId: live.turn.rootId,
      subpath: live.turn.subpath,
      userMessageId: live.turn.userMessageId,
      userMessage: live.turn.userMessage,
      traceId: live.turn.traceId,
      messageId: live.turn.messageId,
      kind: live.turn.kind,
      events,
    };
  }

  /**
   * 取消一个在跑的回合。无对应 sidecar / RPC 失败时抛错，供 FE 可见提示
   * （勿静默吞——请求失败时用户需要知道信号没发出去，可再点停止）。
   */
  async cancel(req: SidecarCancelRequest): Promise<void> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) {
      throw new Error("本地引擎未运行，无法停止");
    }
    await entry.client.request("cancel", {
      turnId: req.turnId,
      ...(req.conversationId ? { conversationId: req.conversationId } : {}),
      ...(req.reason ? { reason: req.reason } : {}),
    });
  }

  /** 用户中途改某个 worker 的方向（本地引擎受理后即取消在飞工作 + 重跑）。 */
  async runRedirect(
    req: SidecarRunRedirectRequest,
  ): Promise<SidecarInterveneAck> {
    return this.intervene(req, "runRedirect", {
      conversationId: req.conversationId,
      executionId: req.executionId,
      runId: req.runId,
      feedback: req.feedback,
    });
  }

  /** 用户中途停某个 / 全部 worker（不杀回合）。 */
  async runStop(req: SidecarRunStopRequest): Promise<SidecarInterveneAck> {
    return this.intervene(req, "runStop", {
      conversationId: req.conversationId,
      executionId: req.executionId,
      runId: req.runId ?? null,
    });
  }

  /**
   * 按人干预的共同提交路径：把本地引擎的受理回执原样带回渲染层。
   *
   * 引擎沉睡 / RPC 失败都是「没有活的驱动循环」——以前这里静默吞掉，UI 照样弹
   * 「引擎将停下这位队员」，而实际上什么都没发生。
   */
  private async intervene(
    req: { rootId: string; subpath?: string },
    method: "runRedirect" | "runStop",
    params: Record<string, unknown>,
  ): Promise<SidecarInterveneAck> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) return UNREACHABLE_INTERVENE_ACK;
    try {
      const reply = (await entry.client.request(method, params)) as Partial<
        Record<keyof SidecarInterveneAck, unknown>
      > | null;
      if (reply == null) return UNREACHABLE_INTERVENE_ACK;
      return {
        accepted: reply.accepted === true,
        reason: String(reply.reason ?? "no_live_drive"),
        detail: String(reply.detail ?? ""),
        queued: Number(reply.queued ?? 0),
      };
    } catch {
      return UNREACHABLE_INTERVENE_ACK;
    }
  }

  /** 辩论 ambient 掌舵（不阻塞主持人，下一轮边界生效）。
   *
   * `accepted=false` = 引擎没收：掌舵窗口已关（辩论没在跑 / 已过末轮边界）或 sidecar 不可达。
   * 回执要诚实，故这里不吞——由调用方据此改口，而非照样显示「已发送」。 */
  async debateSteer(
    req: SidecarDebateSteerRequest,
  ): Promise<{ accepted: boolean }> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) return { accepted: false };
    try {
      const reply = (await entry.client.request("debateSteer", {
        conversationId: req.conversationId,
        executionId: req.executionId,
        decision: req.decision,
        focus: req.focus ?? "",
        ask: req.ask ?? "",
        askTarget: req.askTarget ?? "",
      })) as { ok?: boolean } | null;
      return { accepted: reply?.ok === true };
    } catch {
      return { accepted: false };
    }
  }

  /**
   * 本机 live 插话 / 排队。无 sidecar 进程或 RPC 失败须上抛——不得收成 received/queued。
   */
  async deliverMessage(
    req: SidecarDeliverMessageRequest,
  ): Promise<SidecarDeliverMessageAck> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) {
      throw new Error("本地引擎未运行，无法发送");
    }
    try {
      const reply = await entry.client.request("deliverMessage", {
        conversationId: req.conversationId,
        content: req.content,
        delivery: req.delivery,
        userMessageId: req.userMessageId,
        messageId: req.messageId,
        traceId: req.traceId,
        ...(req.attachments && req.attachments.length > 0
          ? { attachments: req.attachments }
          : {}),
        ...(req.agentMentions && req.agentMentions.length > 0
          ? { agentMentions: req.agentMentions }
          : {}),
      });
      return parseDeliverMessageAck(reply);
    } catch (err) {
      // JSON-RPC error −32006 = HTTP 409 pending；收成 blocked ack，勿当发送失败。
      if (err instanceof SidecarRpcError && err.code === -32006) {
        return { status: "blocked", code: "pending_interactions_awaiting" };
      }
      throw err;
    }
  }

  /** 取消本机 FIFO 排队。无进程 / 已不在队 → ``not_found``（同云 404）。 */
  async cancelQueuedTurn(
    req: SidecarCancelQueuedTurnRequest,
  ): Promise<SidecarCancelQueuedTurnAck> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) return { status: "not_found" };
    try {
      const reply = await entry.client.request("cancelQueuedTurn", {
        conversationId: req.conversationId,
        queueId: req.queueId,
      });
      return parseCancelQueuedTurnAck(reply);
    } catch (err) {
      if (isQueuedTurnNotFound(err)) return { status: "not_found" };
      throw err;
    }
  }

  /** 列出本机 FIFO 排队。无进程 → 空表（不 spawn）。 */
  async listQueuedTurns(
    req: SidecarListQueuedTurnsRequest,
  ): Promise<SidecarListQueuedTurnsResult> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) return { items: [] };
    const reply = await entry.client.request("listQueuedTurns", {
      conversationId: req.conversationId,
    });
    return { items: parseQueuedTurnItems(reply) };
  }

  /** 结算一个被挂起的交互（审批 / ask_user / 本地工具）。 */
  async respond(req: SidecarRespondRequest): Promise<{ resolved: boolean }> {
    const entry = this.entries.get(entryKey(req.rootId, req.subpath));
    if (!entry) {
      throw new Error(
        `本地引擎未就绪（root=${req.rootId} subpath=${req.subpath ?? ""}），无法结算交互`,
      );
    }
    const reply = (await entry.client.request("respond", {
      requestId: req.requestId,
      conversationId: req.conversationId,
      result: req.result,
    })) as { resolved?: boolean } | null;
    return { resolved: Boolean(reply?.resolved) };
  }

  /** 退出时清理所有 sidecar（尽力发 shutdown 再终止进程）。 */
  disposeAll(): void {
    for (const [, entry] of this.entries) {
      this.clearWarmKeepaliveTimer(entry);
      void entry.client.request("shutdown", {}).catch(() => {});
      entry.client.dispose();
    }
    this.entries.clear();
    this.turns.clear();
    this.lastWindowByCid.clear();
  }

  /**
   * FIFO 出队：与点发送同一条 startTurn（先占位再开跑）。
   * 无窗口 / 无进程 / 占位失败 → 不发 RPC，sidecar 超时后诚实 start_failed。
   */
  private async startQueuedTurnFromSidecar(
    params: Record<string, unknown>,
  ): Promise<void> {
    const conversationId = String(params.conversationId ?? "").trim();
    const userMessageId = String(params.userMessageId ?? "").trim();
    const messageId = String(params.messageId ?? "").trim();
    const traceId = String(params.traceId ?? "").trim();
    const userMessage = String(params.userMessage ?? "");
    const queueId = String(params.queueId ?? "").trim();
    if (!conversationId || !userMessageId || !messageId || !traceId) {
      logDesktop({
        level: "error",
        event: "sidecar.queue_need_start_invalid",
        fields: { conversation_id: conversationId },
      });
      return;
    }
    const resolved = this.resolveConversationWindow(conversationId);
    if (
      !resolved ||
      !this.entries.get(entryKey(resolved.rootId, resolved.subpath))
    ) {
      logDesktop({
        level: "warn",
        event: "sidecar.queue_need_start_unrouted",
        fields: { conversation_id: conversationId, queue_id: queueId },
      });
      return;
    }
    const mentions = Array.isArray(params.agentMentions)
      ? (params.agentMentions as SidecarStartTurnRequest["agentMentions"])
      : undefined;
    const attachments = Array.isArray(params.attachments)
      ? (params.attachments as SidecarQueuedAttachment[])
      : undefined;
    try {
      await this.startTurn(
        resolved.wc,
        {
          conversationId,
          rootId: resolved.rootId,
          subpath: resolved.subpath,
          turnId: randomUUID(),
          traceId,
          userMessageId,
          messageId,
          userMessage,
          ...(queueId ? { queueId } : {}),
          ...(mentions && mentions.length > 0
            ? { agentMentions: mentions }
            : {}),
          ...(attachments && attachments.length > 0 ? { attachments } : {}),
        },
        ".",
      );
    } catch (err) {
      logDesktop({
        level: "error",
        event: "sidecar.queue_need_start_failed",
        fields: {
          conversation_id: conversationId,
          queue_id: queueId,
          error: err instanceof Error ? err.message : String(err),
        },
      });
    }
  }

  private onNotification(
    method: string,
    params: Record<string, unknown>,
  ): void {
    if (method === SIDECAR_FULFILL_NOTIFICATION) {
      this.onFulfillFrame(params);
      return;
    }
    if (method === SIDECAR_QUEUE_NEED_START) {
      void this.startQueuedTurnFromSidecar(params);
      return;
    }
    if (method !== "turn/event") return;
    const raw = params.event;
    const event =
      raw && typeof raw === "object"
        ? (raw as {
            type?: string;
            timestamp?: string;
            payload?: unknown;
          })
        : null;
    if (!event?.type) return;

    if (
      event.type === "execution_detached" ||
      event.type === "execution_completed"
    ) {
      this.onExecutionWarmLifecycle(event.type, params, event.payload);
    }

    const turnId = String(params.turnId ?? "");
    const turn = this.turns.get(turnId) ?? this.adoptOrphanTurn(turnId, params);
    if (!turn) return;

    const buffered = {
      type: String(event.type),
      timestamp:
        typeof event.timestamp === "string"
          ? event.timestamp
          : new Date().toISOString(),
      payload: event.payload,
    };
    // Buffer first (even when wc is destroyed) so refresh attach can replay.
    turn.buffer.record(buffered);

    // During attach's zero-await window: buffer only — snapshot owns those events.
    if (turn.attaching || turn.wc.isDestroyed()) {
      if (turn.ephemeral && isTurnEventTerminal(buffered.type)) {
        this.turns.delete(turnId);
      }
      return;
    }
    safeWcSend(turn.wc, SIDECAR_CHANNELS.event, {
      conversationId: turn.conversationId,
      turnId,
      event: buffered,
    });
    if (turn.ephemeral && isTurnEventTerminal(buffered.type)) {
      this.turns.delete(turnId);
    }
  }

  /**
   * 本机履约帧（`fulfill/frame`）→ 该会话窗口。
   *
   * 与回合事件分流：履约不是显示态，不入 `SidecarEventBuffer`（attach 重放会让
   * 同一 op 再执行一次），也不喂 `dispatchSSEEvent`。会话 id 取自帧 payload。
   * 用户活回合优先，否则打到 ephemeral harvest；都找不到只记日志丢弃
   * （op 随后按通道超时诚实失败，不猜窗口）。
   */
  private onFulfillFrame(params: Record<string, unknown>): void {
    const raw = params.event;
    const frame =
      raw && typeof raw === "object"
        ? (raw as { type?: string; timestamp?: string; payload?: unknown })
        : null;
    if (!frame?.type) return;

    const payload = frame.payload;
    const conversationId =
      payload && typeof payload === "object"
        ? String(
            (payload as { conversation_id?: unknown }).conversation_id ?? "",
          )
        : "";
    const live = conversationId ? this.findFulfillTurn(conversationId) : null;
    if (!live) {
      logDesktop({
        level: "warn",
        event: "sidecar.fulfill_unrouted",
        fields: {
          conversation_id: conversationId || null,
          frame_type: frame.type,
        },
      });
      return;
    }
    safeWcSend(live.turn.wc, SIDECAR_CHANNELS.fulfill, {
      conversationId,
      frame: {
        type: String(frame.type),
        ...(typeof frame.timestamp === "string"
          ? { timestamp: frame.timestamp }
          : {}),
        payload: frame.payload,
      },
    });
  }

  /**
   * Before `turns.delete`: if the window never saw a terminal event,
   * synthesize one so the bubble cannot hang on「生成中」(D4 收尾必达).
   *
   * Live 用户回合：泵上已有 terminal 则跳过（禁双终态）。不得因从未
   * attach 而跳过——用户停止时 sidecar 可能只回 TURN_CANCELLED、泵从未打出
   * message_end。取消合成 `message_end(finish_reason=cancelled)`；成功收口
   * 缺帧则 `end_turn`（禁止把正常结束打成停止）。不要空 payload，也不要把
   * 取消打成 error（失败脸）。
   *
   * ephemeral harvest：没有 attach 槽，无 terminal 也要合成（进程退出走 error）。
   */
  private emitSyntheticTerminalIfNeeded(
    turnId: string,
    kind: "message_end" | "error",
    err?: unknown,
  ): void {
    const turn = this.turns.get(turnId);
    if (!turn) return;
    if (turn.buffer.hasTerminal()) return;

    const event = {
      type: kind,
      timestamp: new Date().toISOString(),
      payload:
        kind === "error"
          ? {
              code: "sidecar_turn_ended",
              message:
                err instanceof Error
                  ? err.message
                  : err
                    ? String(err)
                    : "本地回合异常结束",
            }
          : {
              finish_reason: isSidecarTurnCancelled(err)
                ? "cancelled"
                : "end_turn",
            },
    };
    turn.buffer.record(event);
    safeWcSend(turn.wc, SIDECAR_CHANNELS.event, {
      conversationId: turn.conversationId,
      turnId,
      event,
    });
  }

  private findLiveTurn(
    conversationId: string,
  ): { turnId: string; turn: ActiveTurn } | null {
    for (const [turnId, turn] of this.turns) {
      if (turn.ephemeral) continue;
      if (turn.conversationId === conversationId) {
        return { turnId, turn };
      }
    }
    return null;
  }

  /** 本会话对应的 sidecar 进程（不 spawn）：活回合 → 记住的窗 → 任意同 cid 登记。 */
  private findSidecarProcess(conversationId: string): SidecarEntry | null {
    const live = this.findLiveTurn(conversationId);
    if (live) {
      const hit = this.entries.get(
        entryKey(live.turn.rootId, live.turn.subpath),
      );
      if (hit) return hit;
    }
    const remembered = this.lastWindowByCid.get(conversationId);
    if (remembered) {
      const hit = this.entries.get(
        entryKey(remembered.rootId, remembered.subpath),
      );
      if (hit) return hit;
    }
    for (const turn of this.turns.values()) {
      if (turn.conversationId !== conversationId) continue;
      const hit = this.entries.get(entryKey(turn.rootId, turn.subpath));
      if (hit) return hit;
    }
    return null;
  }

  /** 履约帧：用户活回合优先，否则打到 ephemeral harvest。 */
  private findFulfillTurn(
    conversationId: string,
  ): { turnId: string; turn: ActiveTurn } | null {
    const live = this.findLiveTurn(conversationId);
    if (live) return live;
    for (const [turnId, turn] of this.turns) {
      if (turn.ephemeral && turn.conversationId === conversationId) {
        return { turnId, turn };
      }
    }
    return null;
  }

  private rememberWindow(
    conversationId: string,
    wc: WebContents,
    rootId: string,
    subpath: string,
  ): void {
    if (!conversationId) return;
    this.lastWindowByCid.set(conversationId, { wc, rootId, subpath });
  }

  private dropEphemeralTurns(conversationId: string): void {
    for (const [turnId, turn] of [...this.turns]) {
      if (turn.ephemeral && turn.conversationId === conversationId) {
        this.emitSyntheticTerminalIfNeeded(turnId, "message_end");
        this.turns.delete(turnId);
      }
    }
  }

  private finalizeEphemeralTurns(
    rootId: string,
    subpath: string,
    err?: unknown,
  ): void {
    for (const [turnId, turn] of [...this.turns]) {
      if (!turn.ephemeral) continue;
      if (turn.rootId !== rootId || turn.subpath !== subpath) continue;
      this.emitSyntheticTerminalIfNeeded(turnId, "error", err);
      this.turns.delete(turnId);
    }
  }

  /**
   * 认窗：活回合 → 未销毁的 remembered wc → `getAllWindows` 回退。
   * remembered 已销毁不得当有效（reload / HMR 死窗）。
   */
  private resolveConversationWindow(
    conversationId: string,
  ): { wc: WebContents; rootId: string; subpath: string } | null {
    const live = this.findLiveTurn(conversationId);
    if (live && !live.turn.wc.isDestroyed()) {
      return {
        wc: live.turn.wc,
        rootId: live.turn.rootId,
        subpath: live.turn.subpath,
      };
    }
    const remembered = this.lastWindowByCid.get(conversationId);
    if (remembered && !remembered.wc.isDestroyed()) {
      return remembered;
    }
    for (const win of BrowserWindow.getAllWindows()) {
      if (win.isDestroyed()) continue;
      const wc = win.webContents;
      if (wc.isDestroyed()) continue;
      return {
        wc,
        rootId: remembered?.rootId || live?.turn.rootId || "",
        subpath: remembered?.subpath ?? live?.turn.subpath ?? "",
      };
    }
    return null;
  }

  /**
   * 未知 turnId 的自发 `turn/event`：用 cid 找回窗口、登记、转发。
   * ``origin=queue`` = 本机 FIFO 出队的用户回合（进 findLiveTurn / attach）。
   * 其它 = harvest 临时回合（跳过 findLiveTurn）。
   * 禁止因「不在 this.turns」丢弃。找不到该会话最近窗口才放弃。
   */
  private adoptOrphanTurn(
    turnId: string,
    params: Record<string, unknown>,
  ): ActiveTurn | null {
    if (!turnId) return null;
    const conversationId = String(params.conversationId ?? "").trim();
    if (!conversationId) return null;

    const live = this.findLiveTurn(conversationId);
    const resolved = this.resolveConversationWindow(conversationId);
    if (!resolved) {
      logDesktop({
        level: "warn",
        event: "sidecar.turn_unrouted",
        fields: {
          conversation_id: conversationId,
          turn_id: turnId,
        },
      });
      return null;
    }

    const { wc, rootId, subpath } = resolved;
    this.rememberWindow(conversationId, wc, rootId, subpath);

    const queued = String(params.origin ?? "").trim() === "queue";
    const traceId = queued
      ? String(params.traceId ?? "").trim()
      : (live?.turn.traceId ?? "");
    const userMessageId = queued
      ? String(params.userMessageId ?? "").trim()
      : "";
    const messageId = queued ? String(params.messageId ?? "").trim() : "";

    const adopted: ActiveTurn = {
      wc,
      conversationId,
      rootId,
      subpath,
      kind: "start",
      traceId,
      ...(userMessageId ? { userMessageId } : {}),
      ...(messageId ? { messageId } : {}),
      buffer: new SidecarEventBuffer(),
      attaching: false,
      ephemeral: !queued,
    };
    this.turns.set(turnId, adopted);
    logDesktop({
      level: "info",
      event: "sidecar.orphan_turn_adopted",
      fields: {
        conversation_id: conversationId,
        turn_id: turnId,
      },
    });
    return adopted;
  }

  private pushStatus(push: SidecarStatusPush): void {
    for (const win of BrowserWindow.getAllWindows()) {
      safeWcSend(win.webContents, SIDECAR_CHANNELS.status, push);
    }
  }
}
