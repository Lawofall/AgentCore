/**
 * 发送前收口附件：等附加阶段就已开跑的上传落地，凑齐 `OutgoingAttachment[]`。
 *
 * 两条纪律：**并行**（多文件不再一个个 await，串行让 N 个文件慢 N 倍）、
 * **不重跑**（在途的只等，已成的直接用），发送按钮的等待因此只剩「最后一点尾巴」。
 */

import { logEvent } from "@/lib/log";
import { ApiError, NetworkError } from "@/services/api";
import type { OutgoingAttachment } from "@/services/streamConversation";
import {
  attachmentResidedIn,
  awaitAttachmentUpload,
  peekAttachmentRecoverBlob,
  rememberAttachmentRecover,
} from "./attachmentUploads";
import type { PendingAttachment } from "./composerAttachments";
import {
  OVERSIZE_REASON,
  type ResideFailure,
  type ResideResult,
  type ResidentAttachmentInput,
  ensureAttachmentResident,
} from "./resideAttachment";

export type SettledAttachments =
  | { ok: true; outgoing: OutgoingAttachment[] }
  | {
      ok: false;
      reason: string;
      /** 抛出的原始错误（后端 `ApiError` 等），失败提示靠它说出真实原因。 */
      cause?: unknown;
      /** 暂存已失效、留在草稿里也没用的附件——调用方应把它们摘掉。 */
      staleIds: string[];
    };

/** 调用方标记，只进日志：插话与普通发送是两条链路，事后要分得开。 */
export type SettleVia = "send" | "midflight";

/**
 * 失败落在哪一层。**只喂日志**，不参与任何分支决策。
 *
 * 本地失败走到这里只剩一句中文 `reason`——主进程的 `FsErrorCode` 没能穿过
 * `ResideFailure`，所以按可枚举的定值串归类，认不出的一律 `other`，绝不猜。
 */
type SettleFailureKind =
  | "http"
  | "network"
  | "oversize"
  | "staging_expired"
  | "local_workspace_unavailable"
  | "other";

function classifyFailure(
  f: ResideFailure,
  api: ApiError | null,
): SettleFailureKind {
  if (api) return "http";
  if (f.cause instanceof NetworkError) return "network";
  if (f.reason === OVERSIZE_REASON) return "oversize";
  if (f.reason.includes("暂存已失效")) return "staging_expired";
  if (f.reason.includes("本地工作区目录不可用")) {
    return "local_workspace_unavailable";
  }
  return "other";
}

/**
 * 一次发送失败记一条（不是一个附件一条），只留判层用的 status / code / 分类。
 *
 * 这条会落进 `desktop.jsonl`，所以 `reason` 原文绝不能进去：它可能是主进程的 fs 错误
 * （`EACCES … 'C:\…'`）或后端响应体，里面带着文件名与工作区路径。纯观测——不改任何
 * 成功 / 失败路径，也不做拦截或重试。
 */
function logSettleFailure(failure: ResideFailure, via: SettleVia): void {
  const api = failure.cause instanceof ApiError ? failure.cause : null;
  logEvent("warn", "attachment.settle_failed", {
    via,
    failure_kind: classifyFailure(failure, api),
    status: api?.status,
    code: api?.code,
  });
}

/** 有字节要落地的附件（文件类），而非对话 / 目录这类纯文本引用。 */
function needsResidency(a: PendingAttachment): boolean {
  return (
    a.kind === "file" &&
    Boolean(a.stagingId || a.workspacePath || a.binary || a.fileBlob)
  );
}

function passthrough(a: PendingAttachment): OutgoingAttachment {
  return {
    name: a.name,
    path: a.path,
    text: a.text,
    truncated: a.truncated,
    kind: a.kind,
    conversation_id: a.conversationId,
    document_id: a.documentId,
    binary: a.binary,
    workspace_path: a.workspacePath,
  };
}

/** 换了会话就不能信芯片上的旧路径；暂存被吃掉时改用还握着的字节。 */
function residencyInput(
  conversationId: string,
  a: PendingAttachment,
): ResidentAttachmentInput {
  const residedIn = attachmentResidedIn(a.id);
  const fileBlob = a.fileBlob ?? peekAttachmentRecoverBlob(a.id);
  const workspacePath =
    a.workspacePath && residedIn !== undefined && residedIn !== conversationId
      ? undefined
      : a.workspacePath;
  return {
    name: a.name,
    stagingId: a.stagingId,
    workspacePath,
    citedRootId: a.citedRootId,
    citedRelPath: a.citedRelPath,
    binary: a.binary,
    text: a.text,
    truncated: a.truncated,
    fileBlob,
  };
}

function rememberSettled(
  conversationId: string,
  a: PendingAttachment,
  blob: File | undefined,
): void {
  rememberAttachmentRecover(a.id, blob, conversationId);
}

async function settleOne(
  conversationId: string,
  a: PendingAttachment,
): Promise<{ ok: true; outgoing: OutgoingAttachment } | ResideFailure> {
  if (!needsResidency(a)) return { ok: true, outgoing: passthrough(a) };

  let res: ResideResult | null = await awaitAttachmentUpload(
    a.id,
    conversationId,
  );
  // 没登记（历史草稿 / 换了会话）或附加时就失败了 → 发送时再试一次。
  if (!res || !res.ok) {
    const input = residencyInput(conversationId, a);
    const resided = await ensureAttachmentResident(conversationId, input);
    if (resided.ok) {
      rememberSettled(conversationId, a, input.fileBlob ?? resided.fileBlob);
    }
    res = resided.ok
      ? {
          ok: true,
          name: resided.name,
          path: resided.workspacePath || a.path,
          text: resided.text,
          truncated: resided.truncated,
          binary: resided.binary,
          workspacePath: resided.workspacePath || undefined,
          fileBlob: input.fileBlob ?? resided.fileBlob,
        }
      : resided;
  } else {
    rememberSettled(conversationId, a, a.fileBlob ?? res.fileBlob);
  }
  if (!res.ok) return res;

  return {
    ok: true,
    outgoing: {
      name: res.name,
      path: res.workspacePath || a.path,
      text: res.binary ? "" : res.text,
      truncated: res.truncated,
      kind: "file",
      binary: res.binary,
      workspace_path: res.workspacePath || undefined,
    },
  };
}

export async function settleAttachments(
  conversationId: string,
  pending: readonly PendingAttachment[],
  via: SettleVia = "send",
): Promise<SettledAttachments> {
  if (pending.length === 0) return { ok: true, outgoing: [] };

  const settled = await Promise.all(
    pending.map((a) => settleOne(conversationId, a)),
  );

  const outgoing: OutgoingAttachment[] = [];
  const staleIds: string[] = [];
  let failure: ResideFailure | null = null;
  for (const [i, res] of settled.entries()) {
    if (res.ok) {
      outgoing.push(res.outgoing);
      continue;
    }
    failure ??= res;
    // 主进程暂存已被清掉：留在草稿里也发不出去，让调用方摘掉这条 chip。
    if (res.reason.includes("暂存已失效") && pending[i].stagingId) {
      staleIds.push(pending[i].id);
    }
  }
  if (failure) {
    logSettleFailure(failure, via);
    return {
      ok: false,
      reason: failure.reason,
      cause: failure.cause,
      staleIds,
    };
  }
  return { ok: true, outgoing };
}
