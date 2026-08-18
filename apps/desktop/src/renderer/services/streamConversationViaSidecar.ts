import {
  getConversations,
  patchConversationCache,
} from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { StreamError } from "@/lib/errors";
import { logEvent } from "@/lib/log";
import {
  clearSidecarAccountAuth,
  looksLikeAccountTokenFailure,
  resolveSidecarAccountAuth,
} from "@/services/accountToken";
import { ApiError } from "@/services/api";
import {
  CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
  fetchChatContext,
} from "@/services/chatContext";
import {
  clearSidecarFoldersAuth,
  looksLikeFoldersTokenFailure,
  resolveSidecarFoldersAuth,
} from "@/services/foldersToken";
import {
  clearSidecarInference,
  looksLikeInferenceTokenFailure,
  resolveSidecarInference,
} from "@/services/inferenceToken";
import { resolveConversationPermissionAxes } from "@/services/permissionAxes";
import { claimSidecarTurnSink } from "@/services/sidecarEventPump";
import {
  clearActiveSidecarTurn,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import { takeRecentSidecarFailure } from "@/services/sidecarStatus";
import {
  type OutgoingAgentMention,
  type TurnCommitReport,
  dispatchSSEEvent,
  flushPendingContent,
  flushPendingFrames,
} from "@/services/streamConversation";
import {
  beginLocalConversationStream,
  claimPrimaryStream,
  releasePrimaryStream,
} from "@/services/turns/streamOwnership";
import { useAuthStore } from "@/stores/auth";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import {
  enterTurnStreaming,
  getTurnPhase,
  throwIfCannotOpenStream,
} from "@/stores/conversation/turnPhaseActions";
import { clearInteractionPrompts } from "@/stores/interactionPrompts";
import type { SSEEvent } from "@/types/events";
import type {
  SidecarHistoryEntry,
  SidecarTurnResult,
} from "@shared/sidecar-contract";

/**
 * 本地引擎（sidecar）对话流 —— 与 `streamConversation`（云 SSE）对偶的另一条链路。
 *
 * 双模式工作区：当一个会话绑定了本地授权根、且走 sidecar 时，回合由
 * 用户机器上的 `python -m agentcore.sidecar` 跑。本函数把那条 stdio JSON-RPC 链路在
 * renderer 这端「伪装成」一次普通流式回合：
 *
 * - 过程事件经主进程 `sidecar:event` 推来，**与服务端 SSE 同形状**，故原样喂给同一个
 *   `dispatchSSEEvent`——会话切片 / 执行图 / 工具时间线全部复用，零额外分支。
 * - `window.sidecarApi.startTurn` 的 Promise 在回合结束时 resolve（携带最终结果；流式
 *   细节已由事件给过）。主进程按 FIFO 先推完事件再回响应，故 resolve 时 `message_end`
 *   已派发、气泡已收尾。
 *
 * 持久化（as-built: 双模式工作区 §10.3；前端 UX §一B）：sidecar 渐进写入本机 outbox；主进程 Bearer
 * 回写器投递 `POST .../local-turns`。Renderer 只标记 `synced_pending`、冲刷该 turn、
 * 并对账乐观气泡——不再做 HTTP 重试 / toast 手动重试。
 */

export interface StreamViaSidecarOptions {
  conversationId: string;
  /** 绑定的本地授权根 id（主进程据此解析绝对路径并复用 / 拉起该根的 sidecar）。 */
  rootId: string;
  /** 工作区子路径（工作区对称化 D1a）：非空时主进程把 sidecar 绑定到 `容器根/子路径`，
   *  使懒建的 per 对话本地工作区各跑在自己目录里。空 = 该根自身（现行为）。 */
  subpath?: string;
  content: string;
  /** 已确认的服务端装配窗口（含空窗）。缺省则桌面用会话 cookie 拉同一窗口。 */
  history?: SidecarHistoryEntry[];
  /** 本轮用户气泡的乐观 id：回写落库后据此把它换成云端权威 id（仅当它仍是末条 user
   *  消息时——防用户在回写返回前又发了一条而误改）。 */
  optimisticUserId: string;
  /** 本发 outbox flush 成功时置 `committed`。Class B 回滚读这个事实，不嗅消息 id。 */
  turnCommit?: TurnCommitReport;
  /** Soft @Agent chips — prompt hint only; forwarded to startTurn. Empty / omit = none. */
  agentMentions?: OutgoingAgentMention[];
  /** 答非阻塞提问时与出站 ``question_posted.ask_id`` 对上。缺省 = 普通消息。 */
  askId?: string | null;
  signal?: AbortSignal;
}

export interface ResumeViaSidecarOptions {
  conversationId: string;
  rootId: string;
  /** 工作区子路径（同 {@link StreamViaSidecarOptions.subpath}）：寻址按 root+subpath 起的
   *  sidecar 进程，使子路径工作区的续跑也落在自己目录里。空 = 该根自身。 */
  subpath?: string;
  /** 挂起回合的 assistant message_id（续跑键；也是事件路由 / cancel 的寻址键）。 */
  messageId: string;
  decision: "continue" | "adjust" | "stop" | "research_first";
  note: string;
  selected?: string[];
  /** team_preview（delegate）continue 修正；与云 resume / SidecarResumeRequest 同形。 */
  excluded_run_ids?: string[];
  write_capability_overrides?: Array<{
    run_id: string;
    capability: "text_only";
  }>;
  model_overrides?: Record<
    string,
    { model: string; origin?: "platform" | "byok"; provider_id?: string }
  >;
  /** Structured website style pick (s0/s1/…). */
  /** 挂起回合的原始用户消息（来自帧）——续跑完成后随回写落库。 */
  userMessage: string;
  /** 挂起时已落库的原始 user 气泡 id（初始发送时的 optimisticUserId）——回写据此对账，
   *  续跑不再注入新气泡。 */
  userMessageId: string;
  signal?: AbortSignal;
}

/** 一个轻量 turnId（cancel 的寻址键）。crypto.randomUUID 在 Electron renderer 可用。 */
function newTurnId(): string {
  return `t_${crypto.randomUUID()}`;
}

/**
 * 本回合 trace_id：32-hex（去掉 UUID 连字符），与服务端 `core/log_context.new_trace_id`
 * （`uuid4().hex`）同形、契合 `Message.trace_id` 的 `String(32)` 列。随云代理 LLM 调用上报
 * 并随回写落库，使一次本地回合的推理日志↔气泡归并为同一条可 grep 的 trace（打通气泡↔日志）。
 */
function newTraceId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

/**
 * 从列表 / folders 缓存解析「项目归属 + 本地 FS 绑定」，供 startTurn / resume 下发。
 * 绑定字段与寻址用 `rootId`/`subpath` 分离：仅当文件夹在 folders 缓存有 `localRootId` 时
 * 填入；裸聊 / 云项目 / 无绑定 → 二者为 null（键仍由调用方写入 RPC）。
 */
function resolveProjectTurnBinding(conversationId: string): {
  folderId: string | null;
  localRootId: string | null;
  localSubpath: string | null;
} {
  const folderId =
    getConversations().find((c) => c.id === conversationId)?.folderId ?? null;
  if (!folderId) {
    return { folderId: null, localRootId: null, localSubpath: null };
  }
  const folder = getFolders().find((f) => f.id === folderId);
  const localRootId = folder?.localRootId ?? null;
  return {
    folderId,
    localRootId,
    localSubpath: localRootId != null ? (folder?.localSubpath ?? "") : null,
  };
}

/** 剥 Electron IPC / SidecarRpcError 包装，露出引擎真因原文；提不出则 `null`。 */
function unwrapSidecarRejectMessage(err: unknown): string | null {
  if (!(err instanceof Error)) return null;
  const unwrapped = err.message
    .replace(/^Error invoking remote method '[^']*':\s*/, "")
    .replace(/^(?:Error|SidecarRpcError):\s*/, "")
    .trim();
  return unwrapped || null;
}

/**
 * 从一次失败的回合 RPC（`startTurn` / `resume`）拒绝里提取本地引擎真因（onStatus 没记到时的兜底）。
 *
 * 回合中途引擎报错时进程仍健康（无 `error`/`exited` 推送），真因落在 RPC 错误的 message 里；
 * 而 Electron 会把主进程 handler 抛出的错误包成
 * `Error invoking remote method 'sidecar:startTurn': Error: <真因>`——剥掉这层包装与 `Error:`
 * 前缀，露出可读真因。提不出则返回 `null`，由调用方退到通用兜底文案。
 *
 * IPC 边界拒（`无效的 IPC 入参：…`）优先展字段级原因，避免与「浏览器未装配 / 服务不可用」混淆。
 */
/** 本机互斥拒——正常并发态，不是引擎故障。 */
function isSidecarTurnAlreadyRunning(err: unknown): boolean {
  const msg = unwrapSidecarRejectMessage(err) ?? "";
  return /turn already running/i.test(msg);
}

function describeSidecarTurnError(err: unknown): string | null {
  const unwrapped = unwrapSidecarRejectMessage(err);
  if (!unwrapped) return null;
  // 忙槽互斥：勿套「本地引擎出错」——与云端 turn_in_progress / resume_deferred 同属并发态。
  if (/turn already running/i.test(unwrapped)) {
    return "当前还有回合在进行，请稍候或先停止后再继续";
  }
  // Deferred 等待被同会话更新的一次提交顶替（服务端 last click wins）：settlement 已预写落库，
  // 放行不会丢——同属并发态，勿套引擎故障文案。
  if (/resume superseded/i.test(unwrapped)) {
    return "这次放行已由更新的一次提交接管";
  }
  const ipcMatch =
    /^无效的 IPC 入参：([^\s（]+)(?:（字段 (.+?) 期望 (.+?)）)?$/.exec(
      unwrapped,
    );
  if (ipcMatch) {
    const [, channel, field, expected] = ipcMatch;
    if (field && expected) {
      return `本地引擎出错：请求参数校验失败（${field} 期望 ${expected}，${channel}）`;
    }
    return `本地引擎出错：请求参数校验失败（${channel}）`;
  }
  return `本地引擎出错：${unwrapped}`;
}

/**
 * 诚实停止（`stopGeneration` 不 abort AbortSignal）下，sidecar `startTurn`/`resume`
 * 仍会以 `TURN_CANCELLED` / `"turn cancelled"` reject。须识别为用户停止并抛
 * `AbortError`，否则会误打「本地引擎出错」横幅。
 *
 * 判定：拒绝文案是引擎取消约定（IPC 后通常只剩 message，无 -32001 code）；
 * 或 turnPhase 仍为 `stopping`（message_end 尚未定格时的竞态）。
 * 不用 `stopped` 单独放行——避免 invoke 已成功后的 writeBack 失败被误吞。
 */
function isSidecarUserCancel(conversationId: string, err: unknown): boolean {
  const msg = unwrapSidecarRejectMessage(err)?.toLowerCase() ?? "";
  if (msg.includes("turn cancelled") || msg.includes("turn_cancelled")) {
    return true;
  }
  return getTurnPhase(conversationId) === "stopping";
}

/**
 * 发送一条用户消息，经本地 sidecar 跑完整回合并消费其事件流。
 *
 * 失败语义对齐云链路：用户停止（诚实停止 → cancel → RPC reject，或 AbortSignal）
 * 抛 `AbortError`；其余（拉不起 sidecar / 引擎异常）包成带本地引擎诊断的
 * `StreamError("sidecar")`（优先 onStatus 记下的生命周期诊断，见下），由
 * `services/turns.ts` 统一出**针对性**横幅 + 重试。
 */
export async function streamConversationViaSidecar({
  conversationId,
  rootId,
  subpath,
  content,
  history: historyArg,
  optimisticUserId,
  agentMentions,
  askId,
  signal,
  turnCommit,
}: StreamViaSidecarOptions): Promise<SidecarTurnResult> {
  const turnId = newTurnId();
  // 本回合 trace_id：贯穿云代理推理调用 + 回写落库，使推理日志↔气泡同 trace（打通气泡↔日志）。
  const traceId = newTraceId();
  // 云推理 / folders / account 窄票：TTL+skew 内复用，临近过期才 mint；三张并行。
  // 推理票：开跑前无票 → force remint 一次 → 仍无则 INFERENCE_TOKEN_EXPIRED、不发 RPC
  // （引擎硬拒空凭据；无本机平台模型回退）。folders / account 缺票仍可下发，工具侧诚实失败。
  // 开跑前鉴权失败（尚无事件）可对各票 force remint 一次，不对每回合 force。
  // 调用方已确认（含空窗）则不再拉；否则 cookie 与窄票并行，拉到即下发、sidecar 不打云。
  const needCookieWindow = historyArg === undefined;
  const [inferenceRaw, foldersAuthRaw, accountAuthRaw, cookieWindow] =
    await Promise.all([
      resolveSidecarInference({ conversationId }),
      resolveSidecarFoldersAuth(),
      resolveSidecarAccountAuth(),
      needCookieWindow
        ? fetchChatContext(conversationId).then(
            (rows) => ({ ok: true as const, rows }),
            () => ({ ok: false as const }),
          )
        : Promise.resolve({ ok: true as const, rows: historyArg }),
    ]);
  let inference = inferenceRaw ?? undefined;
  let foldersAuth = foldersAuthRaw ?? undefined;
  let accountAuth = accountAuthRaw ?? undefined;
  // 拉失败且有票：省略 history，让 sidecar 拉。拉失败且无票：本回合明确失败。
  let history: SidecarHistoryEntry[] | undefined;
  if (cookieWindow.ok) {
    history = cookieWindow.rows;
  }
  // 本会话权限轴随回合送达本地引擎；取不到则 sidecar 沿用其当前值。
  const permissionAxes =
    await resolveConversationPermissionAxes(conversationId);
  // 项目归属 + 本地绑定：列表 / folders 缓存已有，勿为此查本机库；裸聊 / 云 = null。
  const { folderId, localRootId, localSubpath } =
    resolveProjectTurnBinding(conversationId);
  throwIfCannotOpenStream(conversationId, signal);
  if (!cookieWindow.ok && !accountAuth) {
    throw new StreamError("sidecar", undefined, {
      serverMessage: CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
      recoverable: false,
    });
  }
  return runSidecarTurn({
    conversationId,
    rootId,
    subpath,
    turnId,
    op: "startTurn",
    signal,
    hasInference: inference !== undefined,
    failMessage: "本地引擎未能完成回合，请重试",
    invoke: () =>
      window.sidecarApi.startTurn({
        conversationId,
        rootId,
        subpath,
        turnId,
        traceId,
        // 登录账号透传；未登录回落 "local"（主进程 initialize / 引擎 resolve 同形）。
        userId: useAuthStore.getState().user?.id ?? "local",
        userMessage: content,
        userMessageId: optimisticUserId,
        history,
        ...(agentMentions && agentMentions.length > 0 ? { agentMentions } : {}),
        ...(askId ? { askId } : {}),
        inference,
        foldersAuth,
        accountAuth,
        permissionAxes,
        folderId,
        localRootId,
        localSubpath,
      }),
    remintInference: async () => {
      clearSidecarInference();
      inference =
        (await resolveSidecarInference({
          force: true,
          conversationId,
        })) ?? undefined;
      if (!inference) {
        throw new StreamError("sidecar", undefined, {
          code: "INFERENCE_TOKEN_EXPIRED",
          recoverable: false,
        });
      }
    },
    remintFolders: async () => {
      clearSidecarFoldersAuth();
      foldersAuth =
        (await resolveSidecarFoldersAuth({ force: true })) ?? undefined;
      if (!foldersAuth) {
        throw new Error("folders 凭证续铸失败，请重新登录后再试");
      }
    },
    remintAccount: async () => {
      clearSidecarAccountAuth();
      accountAuth =
        (await resolveSidecarAccountAuth({ force: true })) ?? undefined;
      if (!accountAuth) {
        throw new Error("account 凭证续铸失败，请重新登录后再试");
      }
    },
    writeBack: async () => {
      const committed = await persistAndReconcile(
        conversationId,
        optimisticUserId,
      );
      if (committed && turnCommit) turnCommit.committed = true;
    },
  });
}

/**
 * 续跑一个持久挂起的本地回合（结构化挂起 2b resume）—— `streamConversationViaSidecar` 的对偶。
 *
 * sidecar 回合暂停后应用关闭、帧落本机文件；重开会话经 recovery.paused 重现续跑卡，用户的决定经
 * 此函数下发到 sidecar 的 `resume`（claim 帧并跑 `resume_chat_pipeline`），过程事件与最终结果
 * 形态与一次普通本地回合完全一致，故复用同一套事件分发与回写。事件路由 / cancel 键用
 * message_id（一回合至多一个持久挂起）。
 */
export async function resumeConversationViaSidecar({
  conversationId,
  rootId,
  subpath,
  messageId,
  decision,
  note,
  selected,
  excluded_run_ids,
  write_capability_overrides,
  model_overrides,
  userMessageId,
  signal,
}: ResumeViaSidecarOptions): Promise<SidecarTurnResult> {
  console.warn(
    `[Resume] resumeConversationViaSidecar start conversationId=${conversationId} messageId=${messageId} decision=${decision} rootId=${rootId} subpath=${subpath}`,
  );
  // 续跑同 startTurn：TTL+skew 内复用三张窄票，并行解析；开跑前鉴权失败可各票 force remint 一次。
  // inference 铸票带本会话 id，使 model 与该会话组合一致；无票同样 force remint → 仍无则诚实失败。
  const [inferenceRaw, foldersAuthRaw, accountAuthRaw] = await Promise.all([
    resolveSidecarInference({ conversationId }),
    resolveSidecarFoldersAuth(),
    resolveSidecarAccountAuth(),
  ]);
  let inference = inferenceRaw ?? undefined;
  let foldersAuth = foldersAuthRaw ?? undefined;
  let accountAuth = accountAuthRaw ?? undefined;
  // 本会话权限轴（同 startTurn）：续跑期间的能力授权按会话当前轴。
  const permissionAxes =
    await resolveConversationPermissionAxes(conversationId);
  // 项目归属 + 本地绑定（同 startTurn）：续跑对称下发；folderId 覆盖帧内归属。
  const { folderId, localRootId, localSubpath } =
    resolveProjectTurnBinding(conversationId);
  // 本次续跑的 trace_id（同 startTurn）：贯穿续跑的推理调用 + 回写落库。
  const traceId = newTraceId();
  throwIfCannotOpenStream(conversationId, signal);
  try {
    const result = await runSidecarTurn({
      conversationId,
      rootId,
      subpath,
      turnId: messageId,
      op: "resume",
      signal,
      hasInference: inference !== undefined,
      failMessage: "本地引擎未能完成续跑，请重试",
      invoke: () =>
        window.sidecarApi.resume({
          rootId,
          subpath,
          conversationId,
          messageId,
          traceId,
          userId: useAuthStore.getState().user?.id ?? "local",
          userMessageId,
          decision,
          note,
          selected,
          ...(excluded_run_ids && excluded_run_ids.length > 0
            ? { excluded_run_ids }
            : {}),
          ...(write_capability_overrides &&
          write_capability_overrides.length > 0
            ? { write_capability_overrides }
            : {}),
          ...(model_overrides && Object.keys(model_overrides).length > 0
            ? { model_overrides }
            : {}),
          inference,
          foldersAuth,
          accountAuth,
          permissionAxes,
          folderId,
          localRootId,
          localSubpath,
        }),
      remintInference: async () => {
        clearSidecarInference();
        inference =
          (await resolveSidecarInference({
            force: true,
            conversationId,
          })) ?? undefined;
        if (!inference) {
          throw new StreamError("sidecar", undefined, {
            code: "INFERENCE_TOKEN_EXPIRED",
            recoverable: false,
          });
        }
      },
      remintFolders: async () => {
        clearSidecarFoldersAuth();
        foldersAuth =
          (await resolveSidecarFoldersAuth({ force: true })) ?? undefined;
        if (!foldersAuth) {
          throw new Error("folders 凭证续铸失败，请重新登录后再试");
        }
      },
      remintAccount: async () => {
        clearSidecarAccountAuth();
        accountAuth =
          (await resolveSidecarAccountAuth({ force: true })) ?? undefined;
        if (!accountAuth) {
          throw new Error("account 凭证续铸失败，请重新登录后再试");
        }
      },
      writeBack: async () => {
        await persistAndReconcile(conversationId, userMessageId);
      },
    });
    console.warn(
      `[Resume] resumeConversationViaSidecar completed conversationId=${conversationId} messageId=${messageId} decision=${decision}`,
    );
    return result;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.warn(
      `[Resume] resumeConversationViaSidecar failed conversationId=${conversationId} messageId=${messageId} decision=${decision} err=${errMsg}`,
    );
    throw err;
  }
}

interface RunSidecarTurnOptions {
  conversationId: string;
  rootId: string;
  /** 工作区子路径（D1a）：cancel / respond 据 root+subpath 寻址到正确的 sidecar 进程。 */
  subpath?: string;
  /** 事件路由 + cancel 的寻址键：新回合用 turnId，续跑用 message_id。 */
  turnId: string;
  /** RPC 名：拒因 `turn already running` 落 desktop.jsonl 时区分 start vs resume。 */
  op: "startTurn" | "resume";
  signal?: AbortSignal;
  /**
   * 开跑时是否已有云推理票。无则先经 `remintInference` force 换票一次；仍无则
   * `INFERENCE_TOKEN_EXPIRED`、不发 RPC（无本机平台模型回退、不改道云端）。
   */
  hasInference: boolean;
  /** 兜底错误文案（onStatus / RPC 真因都取不到时）。 */
  failMessage: string;
  /** 实际的 RPC 调用（startTurn / resume），Promise 在回合结束时携最终结果 resolve。
   *  可被调用多次：开跑前窄票鉴权失败时会清缓存换票后重调一次（仅 !sawAnyEvent）。 */
  invoke: () => Promise<SidecarTurnResult>;
  /** 无票 / 开跑前 inference 鉴权失败时：清缓存并换新票；仍无则抛 `INFERENCE_TOKEN_EXPIRED`。 */
  remintInference?: () => Promise<void>;
  /** 开跑前 folders 窄票鉴权失败时：清缓存并换新票（与 remintInference 同形）。 */
  remintFolders?: () => Promise<void>;
  /** 开跑前 account 窄票鉴权失败时：清缓存并换新票（与 remintInference 同形）。 */
  remintAccount?: () => Promise<void>;
  /** 回合结束后冲刷 outbox 并对账（主进程回写；renderer 只反映同步态）。 */
  writeBack: (result: SidecarTurnResult) => Promise<void>;
}

/**
 * 在 renderer 这端把一次 sidecar RPC（startTurn / resume）「伪装成」普通流式回合：订阅事件流
 * 并原样喂 `dispatchSSEEvent`、桥接停止按钮到 `cancel`、收尾后冲刷 outbox、把本地引擎失败统一
 * 包成带诊断的 `StreamError("sidecar")`。新回合与续跑共用这套脚手架，仅 `invoke` / `writeBack`
 * 不同（避免两条链路各写一份事件/中止/错误处理）。
 */
async function runSidecarTurn({
  conversationId,
  rootId,
  subpath,
  turnId,
  op,
  signal,
  hasInference,
  failMessage,
  invoke,
  remintInference,
  remintFolders,
  remintAccount,
  writeBack,
}: RunSidecarTurnOptions): Promise<SidecarTurnResult> {
  // 回合从干净的审批门开始（与云链路一致）。
  clearInteractionPrompts(conversationId);

  // 登记「本会话此刻是 sidecar 回合」（连同 root+subpath），使本回合内挂起的审批 / 交互结算
  // （统一入口 `resolveInteraction`）改走 `window.sidecarApi.respond` 回这条 stdio 链路（寻址到
  // 按 root+subpath 起的同一进程），而非云端 HTTP。
  setActiveSidecarTurn(conversationId, rootId, subpath, turnId);

  // 经单例泵 claim 本 turn 的唯一 sink（禁止再直接 onEvent——可叠 listener → 叠字）。
  // 本回合是否派发过任何 sidecar 事件——一个都没有 = 引擎没跑起来（启动期失败，无输出 /
  // 副作用），失败时据此标 `recoverable` 让 turns.sendTurn 安全降级回云端（阶段二）。
  let sawAnyEvent = false;
  const claim = claimSidecarTurnSink(conversationId, turnId, (push) => {
    sawAnyEvent = true;
    dispatchSSEEvent(push.event as SSEEvent, {
      conversationId,
      source: "sidecar",
    });
  });

  const primaryToken = claimPrimaryStream(conversationId);
  // 本端在折这个会话 → 对话级订阅让位（云侧若也有 run，两边同折会叠字）。
  const releaseLocalStream = beginLocalConversationStream(conversationId);
  try {
    // 开流门禁：已 abort / stopping|terminal → 不 invoke（H1）。
    // AbortSignal 只挡开流 / 表示 UI 观察结束——**禁止**据此 cancel 引擎（C1：断连 ≠
    // 取消；停引擎只走 stopConversation → user_stop）。
    throwIfCannotOpenStream(conversationId, signal);
    enterTurnStreaming(conversationId);

    // 开跑前无云推理票 → force remint 一次；仍无则诚实失败，绝不发 RPC。
    if (!hasInference) {
      if (!remintInference) {
        throw new StreamError("sidecar", undefined, {
          code: "INFERENCE_TOKEN_EXPIRED",
          recoverable: false,
        });
      }
      console.warn(
        "[sidecar] no inference token before turn; reminting once before RPC",
      );
      await remintInference();
    }

    let result: SidecarTurnResult;
    try {
      result = await invoke();
    } catch (firstErr) {
      // 仅开跑前失败（尚无任何事件）才换票重试一次；中途鉴权失败不能整回合重开。
      // 三张窄票对称：TTL 日常复用，遇对应鉴权失败 remint 一次（不对每回合 force）。
      if (sawAnyEvent) {
        throw firstErr;
      }
      if (remintInference && looksLikeInferenceTokenFailure(firstErr)) {
        console.warn(
          "[sidecar] inference token rejected before turn events; reminting and retrying once",
        );
        await remintInference();
      } else if (remintFolders && looksLikeFoldersTokenFailure(firstErr)) {
        console.warn(
          "[sidecar] folders token rejected before turn events; reminting and retrying once",
        );
        await remintFolders();
      } else if (remintAccount && looksLikeAccountTokenFailure(firstErr)) {
        console.warn(
          "[sidecar] account token rejected before turn events; reminting and retrying once",
        );
        await remintAccount();
      } else {
        throw firstErr;
      }
      result = await invoke();
    }
    // 本机已出结果 → 标 synced_pending，冲刷主进程 outbox 并对账。
    await writeBack(result);
    return result;
  } catch (err) {
    // 无票 / remint 失败：已带产品码，勿再包成通用 sidecar 文案或标 recoverable 改道云端。
    if (err instanceof StreamError && err.code === "INFERENCE_TOKEN_EXPIRED") {
      throw err;
    }
    // 拉窗失败：禁止标 recoverable 改道云端空跑；文案已是用户可见说明。
    if (
      err instanceof StreamError &&
      err.serverMessage === CHAT_CONTEXT_UNAVAILABLE_MESSAGE
    ) {
      throw err;
    }
    const chatContextMsg = unwrapSidecarRejectMessage(err);
    if (chatContextMsg?.includes("未能加载对话历史")) {
      throw new StreamError("sidecar", undefined, {
        serverMessage: chatContextMsg,
        recoverable: false,
      });
    }
    // 换票时被 CSRF 中间件拒（后端不补票的那种，见 inferenceToken）：本地引擎没病，别记坏
    // 这个根、也别把安全拒绝套成「本地引擎出错：API 403 …」。原样上抛走统一错误映射。
    if (err instanceof ApiError && err.code === "CSRF_FAILED") {
      throw err;
    }
    // 用户停止：与云链路一致地抛 AbortError（调用方据此不出错误横幅）。
    // 诚实停止不 abort signal，靠 phase / TURN_CANCELLED 文案识别（见 isSidecarUserCancel）。
    if (signal?.aborted || isSidecarUserCancel(conversationId, err)) {
      throw new DOMException("Aborted", "AbortError");
    }
    // 开流门禁抛出的 AbortError（phase 阻断、尚未 invoke）直接上抛。
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    // 本机互斥拒不进云端 jsonl——落到 desktop.jsonl；文案走并发态（勿套引擎故障）。
    // 优先于 onStatus 生命周期诊断，避免陈旧 spawn 失败文案盖住忙槽拒。
    const busy = isSidecarTurnAlreadyRunning(err);
    if (busy) {
      logEvent("warn", "sidecar.turn_already_running", {
        op,
        turn_id: turnId,
        conversation_id: conversationId,
        saw_any_event: sawAnyEvent,
      });
    }
    // 非忙槽：失败**来自本地引擎**（拉不起 / 初始化失败 / 引擎异常 / 进程退出），从不是
    // 真正的「网络」。优先用 onStatus 记下的生命周期诊断（uv/venv 找不到、退出码…）换出针对性
    // 横幅；没有（如回合中途引擎报错，进程仍健康）则退回从该次拒绝里提取真因，最后兜底。
    const detail = busy
      ? (describeSidecarTurnError(err) ??
        "当前还有回合在进行，请稍候或先停止后再继续")
      : (takeRecentSidecarFailure(rootId) ??
        describeSidecarTurnError(err) ??
        failMessage);
    // 启动期失败（一个事件都没派发）= 无任何输出 / 副作用，可安全改道云端重跑（阶段二降级）；
    // 中途失败（已开始流式 / 已调工具）则否。忙槽互斥不是引擎故障，也不降级云端。
    throw new StreamError("sidecar", undefined, {
      serverMessage: detail,
      // 忙槽不是引擎故障：不降级云端（sendTurn 看 recoverable）。
      recoverable: busy ? false : !sawAnyEvent,
      // 专用码：runResume 据此恢复冷卡；勿复用 turn_in_progress（会盖掉并发文案）。
      ...(busy ? { code: "sidecar_turn_busy" } : {}),
    });
  } finally {
    // Abort / engine failure skips message_end (and thus its flush); drain any
    // rAF-buffered content + worker frames so a partial answer keeps its last tokens.
    flushPendingContent(conversationId);
    flushPendingFrames(conversationId);
    clearActiveSidecarTurn(conversationId, turnId);
    claim.release();
    releasePrimaryStream(conversationId, primaryToken);
    releaseLocalStream();
  }
}

/**
 * 冲刷本机 outbox 并对账乐观气泡（as-built: 双模式工作区 §10.3；前端 UX §一B）。
 *
 * Sidecar 已把 finalize 渐进写入 outbox；主进程 Bearer 回写器投递云端。
 * Renderer 只反映同步态——失败保留 `synced_pending`，由主进程轮询续传（无 toast / 无
 * renderer HTTP 双写）。
 */
async function persistAndReconcile(
  conversationId: string,
  optimisticUserId: string,
): Promise<boolean> {
  const store = useConversationStore.getState();
  store.setTurnSyncStatus(optimisticUserId, "synced_pending", conversationId);

  if (!window.outboxApi?.flushTurn) {
    // Sidecar only runs in Electron where outboxApi is injected; keep pending hint.
    console.error(
      "[sidecar] outboxApi missing — sync left to main-process drain",
    );
    return false;
  }

  try {
    const flushed = await window.outboxApi.flushTurn({
      userMessageId: optimisticUserId,
    });
    if (flushed.ok && flushed.synced) {
      applyReconcile(conversationId, optimisticUserId, {
        user_message_id: flushed.synced.cloudUserMessageId || optimisticUserId,
        title: flushed.synced.title,
      });
      // onSynced from main also flips the hint; set here for snappy UI if push races.
      const anchor = flushed.synced.cloudUserMessageId || optimisticUserId;
      store.setTurnSyncStatus(anchor, "synced", conversationId);
      setTimeout(() => {
        store.setTurnSyncStatus(anchor, undefined, conversationId);
      }, 2500);
      return true;
    }
    // Auth/network — file stays; polling + synced_pending UI cover the rest.
    console.error("[sidecar] outbox writeback pending", flushed.error);
  } catch (err) {
    console.error("[sidecar] outbox flushTurn failed", err);
  }
  return false;
}

/**
 * 对账乐观气泡（等价云链路 turn_saved + title_generated）。user id 现已是客户端权威，故换 id
 * 通常是 X→X 无害交换；仍按「末条 user 仍是本轮乐观气泡」守卫——防用户在回写返回前又发了
 * 一条而改错对象。本会话首次产出的标题刷进侧栏缓存。
 */
function applyReconcile(
  conversationId: string,
  optimisticUserId: string,
  saved: {
    user_message_id: string;
    title?: string | null;
  },
): void {
  const messages = getRuntime(conversationId).messages;
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  if (lastUser?.id === optimisticUserId) {
    useConversationStore
      .getState()
      .reconcileLastTurn(saved.user_message_id, conversationId);
  }
  if (saved.title) {
    patchConversationCache(conversationId, { title: saved.title });
  }
}
