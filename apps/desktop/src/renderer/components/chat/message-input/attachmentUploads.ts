/**
 * 附加即上传：附件在「用户附加的那一刻」就开始驻留 / 上传，chip 先出、发送只等。
 *
 * 这个注册表按附件 id 记住在途的那一个 Promise，发送时 await 同一个 Promise 而不是
 * 重新跑一遍——重跑意味着几 MB 的文件被再传一次，正是「点发送后长时间没反应」的来源。
 * 只活在内存：草稿持久化不含在途上传（见 `stores/composer` 的 serializeAttachments）。
 */

import type { PendingAttachment } from "./composerAttachments";
import {
  type ResideResult,
  ensureAttachmentResident,
} from "./resideAttachment";

interface TrackedUpload {
  /** 上传的目标会话；草稿（null）与后来建出的会话不是同一个目标，不可复用。 */
  conversationId: string | null;
  promise: Promise<ResideResult>;
}

/**
 * 上限只为兜底：条目正常在 chip 移除 / 发送落地时被 forget。超限时按插入序淘汰最旧的，
 * 被淘汰的附件退化成发送时兜底驻留，不会丢。
 */
const MAX_TRACKED = 64;

const inFlight = new Map<string, TrackedUpload>();

/**
 * 上次成功驻留的目标会话 + 可再 PUT / 再暂存的字节。
 * in-flight Promise 在发送收口后就会忘，但开跑前拒绝会拆掉刚建的会话——这两份要
 * 活到芯片还回之后，重试才能驻进新工作区，而不是拿已删会话的路径跳过。
 */
const recoverBlobs = new Map<string, File>();
const residedIn = new Map<string, string>();

/** 记下这次驻留落在哪个会话、以及还能再拷的字节。 */
export function rememberAttachmentRecover(
  attachmentId: string,
  blob: File | undefined,
  conversationId: string,
): void {
  if (blob) recoverBlobs.set(attachmentId, blob);
  residedIn.set(attachmentId, conversationId);
}

export function peekAttachmentRecoverBlob(
  attachmentId: string,
): File | undefined {
  return recoverBlobs.get(attachmentId);
}

export function attachmentResidedIn(attachmentId: string): string | undefined {
  return residedIn.get(attachmentId);
}

/** 登记一条在途上传并原样返回它，调用方照常 await 拿结果去更新 chip。 */
export function trackAttachmentUpload(
  attachmentId: string,
  conversationId: string | null,
  promise: Promise<ResideResult>,
): Promise<ResideResult> {
  if (inFlight.size >= MAX_TRACKED) {
    const oldest = inFlight.keys().next();
    if (!oldest.done) inFlight.delete(oldest.value);
  }
  inFlight.set(attachmentId, { conversationId, promise });
  return promise;
}

/**
 * 发送时取回附加阶段的上传结果：仍在传就等它落地。
 * 目标会话对不上（草稿附件被发进新建会话）视作没有，交给兜底驻留。
 */
export async function awaitAttachmentUpload(
  attachmentId: string,
  conversationId: string,
): Promise<ResideResult | null> {
  const tracked = inFlight.get(attachmentId);
  if (!tracked || tracked.conversationId !== conversationId) return null;
  try {
    return await tracked.promise;
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "附件上传失败，请重试",
      cause: e,
    };
  }
}

/** chip 被移除 / 发送已真正上路：丢掉登记（同时放掉它引用的 File）。 */
export function forgetAttachmentUpload(attachmentId: string): void {
  inFlight.delete(attachmentId);
  recoverBlobs.delete(attachmentId);
  residedIn.delete(attachmentId);
}

/**
 * 暂存件（回形针选择 / @ 本机文件）的云端上传：这条路渲染进程没有 File，只能让主
 * 进程把字节交出来。仍然前移到附加时跑，用户点发送时至多是在等它收尾。
 * 本机工作区下区内引用已有 workspacePath，区外 stage 才写进 ``attachments/``，无需再来一趟。
 */
export function startStagedAttachmentUpload(
  conversationId: string,
  att: PendingAttachment,
): Promise<ResideResult> {
  const promise = ensureAttachmentResident(conversationId, att).then(
    (res): ResideResult => {
      if (!res.ok) return res;
      rememberAttachmentRecover(
        att.id,
        att.fileBlob ?? res.fileBlob,
        conversationId,
      );
      return {
        ok: true,
        name: res.name,
        path: res.workspacePath || att.path,
        text: res.text,
        truncated: res.truncated,
        binary: res.binary,
        workspacePath: res.workspacePath || undefined,
        fileBlob: att.fileBlob ?? res.fileBlob,
      };
    },
  );
  return trackAttachmentUpload(att.id, conversationId, promise);
}

/** @internal vitest */
export function __clearAttachmentUploadsForTests(): void {
  inFlight.clear();
  recoverBlobs.clear();
  residedIn.clear();
}
