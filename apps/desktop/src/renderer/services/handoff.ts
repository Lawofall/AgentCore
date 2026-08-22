import { clientHeaders } from "@/lib/clientBuildInfo";
import { StreamError, streamErrorFromResponse } from "@/lib/errors";
import {
  type ChangeType,
  type HandoffApplySelection,
  type HandoffFileChange,
  sha256HexFromBase64,
} from "@/lib/handoff-review";
import { logEvent } from "@/lib/log";
import { bearerAuthHeader, sessionCredentials } from "@/lib/sessionAuth";
import {
  ApiError,
  BASE_URL,
  api,
  captureCsrf,
  getCsrfHeaders,
  isReplayableCsrfRejection,
  notifyUnauthorized,
  tryRefresh,
} from "@/services/api";
import type { components } from "@/types/api.generated";
import type {
  HandoffApplyDonePayload,
  HandoffJobStartedPayload,
  HandoffSnapshotDonePayload,
  SSEEvent,
} from "@/types/events";
import type { WorkspaceOpName } from "@shared/ipc-contract";

type Schemas = components["schemas"];

export interface HandoffResult {
  snapshotId: string;
  sizeBytes: number;
}

/** 派发成功的回执：作业 id 与承载团队回放的隐藏作业对话 id。 */
export interface HandoffJobStarted {
  jobId: string;
  jobConversationId: string;
}

/**
 * 交接作业生命周期（§7.6）：跑批态 + 结果收口态。
 * ``succeeded`` = 可合回；``applied`` = 已合回本机；``discarded`` = 已丢弃。
 */
export type HandoffJobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "applied"
  | "discarded";

/** 一个本地→云交接作业（双模式工作区 P2e / e2，camelCase 域模型）。 */
export interface HandoffJob {
  id: string;
  sourceConversationId: string;
  jobConversationId: string;
  baseSnapshotId: string;
  resultSnapshotId: string | null;
  task: string;
  status: HandoffJobStatus;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
}

/** 一次交接结果的 diff：变更集 + 表头计数（双模式工作区 P2e / e3）。 */
export interface HandoffDiff {
  jobId: string;
  changes: HandoffFileChange[];
  total: number;
  added: number;
  modified: number;
  deleted: number;
}

/** 应用回写中单文件的结果，对齐后端 `ApplyOutcome`。 */
export interface HandoffApplyResultRow {
  path: string;
  status: "applied" | "skipped" | "conflict" | "error";
  changeType: ChangeType | null;
  detail: string;
}

/** 一次应用回写的汇总：逐文件结果 + 卷起的计数（双模式工作区 P2e / e3）。 */
export interface HandoffApplySummary {
  jobId: string;
  results: HandoffApplyResultRow[];
  applied: number;
  skipped: number;
  conflicts: number;
  errors: number;
}

/** Server handoff-job payload (`/handoff/jobs`), generated from OpenAPI. */
type BackendJob = Schemas["HandoffJobSummary"];

/**
 * Wire job with §7.6 status. Generated OpenAPI 尚缺 `applied`/`discarded` 与
 * discard 路径——域模型已对齐服务端；需主 Agent `gen:types`。
 */
type HandoffJobWire = Omit<BackendJob, "status"> & { status: HandoffJobStatus };

/** Server diff file-change row (`/handoff/jobs/{id}/diff`), generated from OpenAPI. */
type BackendFileChange = Schemas["HandoffFileChange"];

/** Server diff payload (`/handoff/jobs/{id}/diff`), generated from OpenAPI. */
type BackendDiff = Schemas["HandoffDiffResponse"];

function toJob(b: HandoffJobWire): HandoffJob {
  return {
    id: b.id,
    sourceConversationId: b.source_conversation_id,
    jobConversationId: b.job_conversation_id,
    baseSnapshotId: b.base_snapshot_id,
    resultSnapshotId: b.result_snapshot_id,
    task: b.task,
    status: b.status,
    error: b.error,
    createdAt: b.created_at,
    updatedAt: b.updated_at,
    finishedAt: b.finished_at,
  };
}

function toChange(b: BackendFileChange): HandoffFileChange {
  return {
    path: b.path,
    changeType: b.change_type,
    baseSha: b.base_sha,
    resultSha: b.result_sha,
    isBinary: b.is_binary,
    content: b.content,
    sizeBytes: b.size_bytes,
  };
}

/**
 * 一条工作区交接 SSE 流的通用消费器（e1 快照 / e2 派发 / e3 应用共用）。
 *
 * 完成事件仍走本流；CLIENT_TOOL `workspace_op_required` 已改投设备履约通道
 *（`clientToolIngress`），此处不再履行——避免与履约通道双份处理同一 request_id。
 * `onEvent` 只负责认出并映射完成事件（返回非 undefined 即为最终结果）。
 *
 * 复用 send 路径的鉴权：access token 过期则刷新一次重放，否则跳登录。这是独立于聊天流
 * 的专用消费器（聊天流改 store，交接流返回值），故不复用 `dispatchSSEEvent`。失败（内联
 * error 事件 / 传输失败 / 流结束仍无结果）抛出，以便 UI 收口。
 */
async function consumeWorkspaceStream<T>(
  path: string,
  _conversationId: string,
  opts: { body?: unknown; signal?: AbortSignal },
  onEvent: (event: SSEEvent) => T | undefined,
): Promise<T> {
  const hasBody = opts.body !== undefined;
  const doFetch = async (): Promise<Response> => {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      credentials: sessionCredentials(),
      headers: {
        Accept: "text/event-stream",
        ...clientHeaders(),
        ...bearerAuthHeader(),
        ...getCsrfHeaders("POST"),
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      body: hasBody ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
    captureCsrf(response); // SSE responses carry the rotated token too
    return response;
  };

  let response: Response;
  try {
    response = await doFetch();
    // 两条自愈共用一次重发预算：401 刷新重放与 CSRF 403 重放合计最多多发一次。
    let replayed = false;
    if (response.status === 401) {
      const outcome = await tryRefresh();
      if (outcome === "renewed") {
        response = await doFetch();
        replayed = true;
      } else if (outcome === "auth_dead") {
        notifyUnauthorized();
        throw new StreamError("auth");
      } else {
        throw new StreamError("network");
      }
      if (response.status === 401) {
        notifyUnauthorized();
        throw new StreamError("auth");
      }
    }
    // 交接 POST 会起云端作业，重发之所以不会派两份，全在判据本身：CSRF 中间件在 handler
    // 之前就拒了，服务端从未受理这次派发，且回发了新令牌（`doFetch` 已吸收；它每次调用重算
    // header，重发才带得上新令牌）。没回发令牌的 403 是服务端刻意不重新武装，原样失败。
    // 论证见 {@link isReplayableCsrfRejection}。
    if (!replayed && response.status === 403) {
      const refusal = new ApiError(
        response.status,
        await response.clone().text(),
        response.headers,
      );
      if (isReplayableCsrfRejection(response, refusal)) {
        logEvent("info", "auth.csrf_replay", { path, via: "workspace_stream" });
        replayed = true;
        response = await doFetch();
      }
    }
  } catch (err) {
    if (err instanceof StreamError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new StreamError("network");
  }
  // 与聊天 SSE 同一条收口：解析 `{error:{code,message}}` 体，否则同一个后端拒绝
  //（CSRF 403 / 配额 429 …）在交接链路上只剩一句「操作失败（403）」。
  if (!response.ok) throw await streamErrorFromResponse(response);

  const reader = response.body?.getReader();
  if (!reader) throw new StreamError("network");

  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | undefined;
  let failure: string | null = null;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let event: SSEEvent;
        try {
          event = JSON.parse(line.slice(6)) as SSEEvent;
        } catch {
          continue; // malformed event — skip
        }
        // Cloud CLIENT_TOOL ops ride the device fulfill stream; ignore stray
        // frames on the handoff completion SSE so we never double-fulfill.
        if (event.type === "workspace_op_required") continue;
        if (event.type === "error") {
          failure =
            (event.payload as { message?: string }).message ?? "操作失败";
          continue;
        }
        const mapped = onEvent(event);
        if (mapped !== undefined) result = mapped;
      }
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new StreamError("network");
  }

  if (failure) throw new Error(failure);
  if (result === undefined) throw new StreamError("network");
  return result;
}

/**
 * 本地→云交接（双模式工作区 P2e / e1）：把绑定的本地工作区快照到云端。
 *
 * POST 一个 SSE 端点：ARCHIVE 等 CLIENT_TOOL 经设备履约通道履行；本流末尾发
 * `handoff_snapshot_done` 带新快照 id。
 */
export async function runHandoff(
  conversationId: string,
  signal?: AbortSignal,
): Promise<HandoffResult> {
  return consumeWorkspaceStream(
    `/v1/conversations/${conversationId}/workspace/handoff`,
    conversationId,
    { signal },
    (event) => {
      if (event.type === "handoff_snapshot_done") {
        const p = event.payload as HandoffSnapshotDonePayload;
        return { snapshotId: p.snapshot_id, sizeBytes: p.size_bytes };
      }
      return undefined;
    },
  );
}

/**
 * 把任务交给云端团队（双模式工作区 P2e / e2）：快照本地文件后在云端后台跑一支 Agent
 * 团队。SSE 先下发 ARCHIVE op（本端履行），末尾发 `handoff_job_started` 带作业 id；云端
 * 运行在流关闭后继续，轮询 `listHandoffJobs` 看状态。仅本地模式可派发（否则 422）。
 */
export async function dispatchHandoffJob(
  conversationId: string,
  task: string,
  signal?: AbortSignal,
): Promise<HandoffJobStarted> {
  return consumeWorkspaceStream(
    `/v1/conversations/${conversationId}/workspace/handoff/dispatch`,
    conversationId,
    { body: { task }, signal },
    (event) => {
      if (event.type === "handoff_job_started") {
        const p = event.payload as HandoffJobStartedPayload;
        return { jobId: p.job_id, jobConversationId: p.job_conversation_id };
      }
      return undefined;
    },
  );
}

/** 一个对话的本地→云交接作业，按时间倒序（双模式工作区 P2e / e2）。 */
export async function listHandoffJobs(
  conversationId: string,
): Promise<HandoffJob[]> {
  const res = await api.get<{ data: HandoffJobWire[]; total: number }>(
    `/v1/conversations/${conversationId}/handoff/jobs`,
  );
  return res.data.map(toJob);
}

/**
 * 一个已完成交接的结果 diff（双模式工作区 P2e / e3）：result 对 base 快照的变更集，
 * 每条携 base 哈希供客户端三方判定。作业未成功时后端返回 409。
 */
export async function getHandoffDiff(
  conversationId: string,
  jobId: string,
): Promise<HandoffDiff> {
  const res = await api.get<BackendDiff>(
    `/v1/conversations/${conversationId}/handoff/jobs/${jobId}/diff`,
  );
  return {
    jobId: res.job_id,
    changes: res.data.map(toChange),
    total: res.total,
    added: res.added,
    modified: res.modified,
    deleted: res.deleted,
  };
}

/**
 * 应用一个已完成交接的所选变更回本地（双模式工作区 P2e / e3）。SSE 下发 WRITE_BYTES /
 * DELETE op（本端履行），末尾发 `handoff_apply_done` 带逐文件结果。冲突门服务端权威：
 * 本地自基线偏离的文件被拒（status `conflict`），除非该选择 `force`。请求体转回后端的
 * snake_case（`local_sha`）。
 */
export async function applyHandoffJob(
  conversationId: string,
  jobId: string,
  selections: HandoffApplySelection[],
  signal?: AbortSignal,
): Promise<HandoffApplySummary> {
  const body = {
    selections: selections.map((s) => ({
      path: s.path,
      decision: s.decision,
      local_sha: s.localSha,
      force: s.force,
    })),
  };
  return consumeWorkspaceStream(
    `/v1/conversations/${conversationId}/handoff/jobs/${jobId}/apply`,
    conversationId,
    { body, signal },
    (event) => {
      if (event.type === "handoff_apply_done") {
        const p = event.payload as HandoffApplyDonePayload;
        return {
          jobId: p.job_id,
          results: p.results.map((r) => ({
            path: r.path,
            status: r.status,
            changeType: r.change_type,
            detail: r.detail,
          })),
          applied: p.applied,
          skipped: p.skipped,
          conflicts: p.conflicts,
          errors: p.errors,
        } satisfies HandoffApplySummary;
      }
      return undefined;
    },
  );
}

/**
 * 放弃一份已结束交接的云端结果（§7.6）：不写回本机，标记 `discarded` 并回收云 host。
 * 路径约定与其它 handoff REST 一致；OpenAPI 生成类型尚未收录（需主 Agent gen:types）。
 */
export async function discardHandoffJob(
  conversationId: string,
  jobId: string,
): Promise<HandoffJob> {
  const res = await api.post<HandoffJobWire>(
    `/v1/conversations/${conversationId}/handoff/jobs/${jobId}/discard`,
  );
  return toJob(res);
}

/**
 * 卡面收口相（§7.6）：后端 `applied`/`discarded` 优先；`mergedOptimistic` 仅在
 * `succeeded` 上补「已合回」，不覆盖 discarded。
 */
export type HandoffCardPhase =
  | "pending"
  | "running"
  | "failed"
  | "awaiting"
  | "applied"
  | "discarded";

export function resolveHandoffCardPhase(
  job: Pick<HandoffJob, "status">,
  mergedOptimistic: boolean,
): HandoffCardPhase {
  if (job.status === "pending") return "pending";
  if (job.status === "running") return "running";
  if (job.status === "failed") return "failed";
  if (job.status === "discarded") return "discarded";
  if (job.status === "applied") return "applied";
  if (mergedOptimistic) return "applied";
  return "awaiting";
}

/**
 * 逐文件读本地字节并算 sha256 hex，供 e3 三方判定的「第三方输入」。对每个路径在绑定根上
 * 跑 `read_bytes`（服务端回 base64）→ 解码哈希；文件本地不存在/不可读/非桌面环境一律为
 * null（= 本地无此文件）。并发读取，返回 path→sha|null 映射。
 */
export async function readLocalShas(
  rootId: string,
  paths: string[],
): Promise<Map<string, string | null>> {
  const map = new Map<string, string | null>();
  const fsApi = typeof window !== "undefined" ? window.fsApi : undefined;
  if (!fsApi?.workspaceOp) {
    for (const p of paths) map.set(p, null);
    return map;
  }
  await Promise.all(
    paths.map(async (path) => {
      try {
        const res = await fsApi.workspaceOp(
          rootId,
          "read_bytes" as WorkspaceOpName,
          { path },
        );
        map.set(
          path,
          res.ok && typeof res.value === "string"
            ? await sha256HexFromBase64(res.value)
            : null,
        );
      } catch {
        map.set(path, null);
      }
    }),
  );
  return map;
}
