/**
 * 附加文件：区内引用原路径；区外才复制进对话工作区 ``attachments/``
 *（本地直写 / 云端 PUT），返回可塞进 PendingAttachment 的字段。绝对路径不进本模块状态。
 *
 * 云端会话的上传发生在**附加那一刻**（见 {@link residentAttachmentForFile}），不再
 * 推迟到点发送——推迟意味着用户点发送后才开始读盘 + 跨 IPC 搬字节 + PUT，点击看起来
 * 「毫无反应」。渲染进程手里已经有 File 时也绝不再走「主进程写盘 → consumeStagedBytes
 * 读回 → 重新包 Blob」那条路：几 MB 会被完整拷贝好几次，全跑在主线程上。
 */

import { hasLocalFiles } from "@/lib/capabilities";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import { uploadWorkspaceFile } from "@/services/workspace";
import { getWorkspaceBinding } from "@/services/workspaceBinding";
import { workspaceRelFromCite } from "@shared/citeWorkspacePath";
import type {
  StageAttachmentDest,
  StagedAttachment,
} from "@shared/ipc-contract";
import { TEXT_PREVIEW_CAP } from "./composerAttachments";

/** Align with main-process ``ATTACH_MAX_BYTES`` / IM ChatComposer. */
export const ATTACH_MAX_BYTES = 50 * 1024 * 1024;

export type ResideResult =
  | {
      ok: true;
      name: string;
      /** 展示用相对路径（工作区原路径 / ``attachments/…`` / 文件名）。 */
      path: string;
      text: string;
      truncated: boolean;
      binary: boolean;
      workspacePath?: string;
      stagingId?: string;
      citedRootId?: string;
      citedRelPath?: string;
      /** 浏览器草稿：无会话时持 File，发送时再 PUT。 */
      fileBlob?: File;
    }
  | ResideFailure;

/**
 * 驻留失败。`reason` 是给 chip / 菜单直接显示的字符串；`cause` 是抛出的原始错误
 * （后端 `ApiError` 等），只有它带着 code / serverMessage，`describeError` 靠它
 * 才能给出真实文案与补救动作——拆成字符串就再也拼不回来了。
 */
export interface ResideFailure {
  ok: false;
  reason: string;
  cause?: unknown;
}

/** Align with main-process ``safeName`` (basename + strip leading dots). */
export function safeBrowserFileName(name: string): string {
  const base = (name || "")
    .replace(/\\/g, "/")
    .trim()
    .split("/")
    .pop()
    ?.replace(/^\.+/, "");
  return base || "attachment";
}

function destFromTarget(t: {
  rootId: string;
  subpath: string;
}): StageAttachmentDest {
  return { rootId: t.rootId, subpath: t.subpath || undefined };
}

/** 已有会话时解析本地落盘目标；云端 / 草稿 → null（走暂存）。 */
export async function resolveAttachDest(
  conversationId: string | null,
): Promise<StageAttachmentDest | null> {
  if (!conversationId || !window.fsApi) return null;
  try {
    const binding = await getWorkspaceBinding(conversationId);
    if (binding.mode !== "local") return null;
  } catch {
    return null;
  }
  const target = await resolveConversationLocalTarget(conversationId);
  if (!target) return null;
  return destFromTarget(target);
}

function fromStaged(s: StagedAttachment): ResideResult {
  return {
    ok: true,
    name: s.name,
    path: s.workspacePath ?? s.name,
    text: s.text,
    truncated: s.truncated,
    binary: s.binary,
    workspacePath: s.workspacePath,
    stagingId: s.stagingId,
    ...(s.citedRootId && s.citedRelPath
      ? { citedRootId: s.citedRootId, citedRelPath: s.citedRelPath }
      : {}),
  };
}

/** 回形针：系统文件选择器。取消 → null。 */
export async function pickLocalFileAttachment(
  conversationId: string | null,
): Promise<ResideResult | null> {
  if (!window.fsApi?.pickAndStageAttachment) {
    return { ok: false, reason: "当前环境无法附加本机文件" };
  }
  const dest = await resolveAttachDest(conversationId);
  const res = await window.fsApi.pickAndStageAttachment(dest ?? undefined);
  if (res === null) return null;
  if (!res.ok) return { ok: false, reason: res.reason };
  return fromStaged(res.data);
}

/** @ 菜单：已授权根内相对路径（含二进制）。 */
export async function stageRootFileAttachment(
  conversationId: string | null,
  rootId: string,
  relPath: string,
): Promise<ResideResult> {
  if (!window.fsApi?.stageFromRoot) {
    return { ok: false, reason: "当前环境无法附加本机文件" };
  }
  const dest = await resolveAttachDest(conversationId);
  const res = await window.fsApi.stageFromRoot(
    rootId,
    relPath,
    dest ?? undefined,
  );
  if (!res.ok) return { ok: false, reason: res.reason };
  return fromStaged(res.data);
}

/** 拖拽 / 粘贴。 */
export async function stageDroppedFileAttachment(
  conversationId: string | null,
  file: File,
): Promise<ResideResult> {
  if (!window.fsApi?.stageDroppedFile) {
    return { ok: false, reason: "当前环境无法附加本机文件" };
  }
  const dest = await resolveAttachDest(conversationId);
  const res = await window.fsApi.stageDroppedFile(file, dest ?? undefined);
  if (!res.ok) return { ok: false, reason: res.reason };
  return fromStaged(res.data);
}

/** 附件预览元信息（名字 / 内联正文 / 是否二进制）。 */
export interface AttachmentMeta {
  name: string;
  text: string;
  truncated: boolean;
  binary: boolean;
}

export const OVERSIZE_REASON = `文件超过 ${Math.round(
  ATTACH_MAX_BYTES / (1024 * 1024),
)}MB 上限`;

const LOCAL_ROOT_UNAVAILABLE = "本地工作区目录不可用，请重新打开文件夹后再附加";

/** 只读头部 {@link TEXT_PREVIEW_CAP} 字节判类型 + 取内联预览，绝不整读大文件。 */
export async function describeFileAttachment(
  file: File,
): Promise<AttachmentMeta> {
  const name = safeBrowserFileName(file.name);
  const head = await file.slice(0, TEXT_PREVIEW_CAP + 1).arrayBuffer();
  const bytes = new Uint8Array(head);
  // 图片 MIME 常无 NUL，不能只靠 sniff；按 binary 驻留，避免当 UTF-8 正文内联。
  const binary = file.type.startsWith("image/") || bytes.includes(0);
  const truncated = !binary && file.size > TEXT_PREVIEW_CAP;
  const text = binary
    ? ""
    : new TextDecoder("utf-8").decode(
        bytes.subarray(0, Math.min(bytes.length, TEXT_PREVIEW_CAP)),
      );
  return { name, text, truncated, binary };
}

/** 云端工作区 PUT：Blob 体由浏览器直接流出，渲染进程不再自拷字节。 */
async function putFileToCloudWorkspace(
  conversationId: string,
  file: Blob,
  meta: AttachmentMeta,
): Promise<ResideResult> {
  const workspacePath = `attachments/${meta.name}`;
  try {
    await uploadWorkspaceFile(conversationId, workspacePath, file);
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "上传附件到云端工作区失败",
      cause: e,
    };
  }
  return { ok: true, path: workspacePath, workspacePath, ...meta };
}

/**
 * 浏览器：回形针 / 拖贴共用。校验大小；有会话则立即云端 PUT，
 * 无会话则持 ``fileBlob`` 到发送。允许二进制（图片 / docx / pdf 等）；
 * 识图是否可用由后端与模型配置决定，此处不硬拒。
 */
export async function prepareBrowserFileAttachment(
  conversationId: string | null,
  file: File,
  known?: AttachmentMeta,
): Promise<ResideResult> {
  if (file.size > ATTACH_MAX_BYTES) {
    return { ok: false, reason: OVERSIZE_REASON };
  }

  const meta = known ?? (await describeFileAttachment(file));

  if (!conversationId) {
    return { ok: true, path: meta.name, fileBlob: file, ...meta };
  }

  // 有会话：立即 PUT（引用即驻留）。本地 binding 在无本机根时不可用。
  try {
    const binding = await getWorkspaceBinding(conversationId);
    if (binding.mode === "local") {
      return { ok: false, reason: LOCAL_ROOT_UNAVAILABLE };
    }
  } catch {
    /* binding unknown — try cloud upload */
  }

  return putFileToCloudWorkspace(conversationId, file, meta);
}

/**
 * 附加即驻留（拖 / 贴 / 浏览器选择）——调用方先把 chip 画出来，再 await 这里。
 *
 * - 本机工作区：区内引用原路径；区外才交主进程复制进 ``attachments/``。
 * - 云端会话：渲染进程手里就是 File，直接 PUT。
 * - 桌面草稿（尚无会话）：主进程暂存留底（重启可恢复；建会话后若仍在该根则引用，
 *   否则 finalize），同时把 File 留在内存，发送时直传云端而不必跨 IPC 把字节读回来。
 */
export async function residentAttachmentForFile(
  conversationId: string | null,
  file: File,
  known?: AttachmentMeta,
): Promise<ResideResult> {
  if (file.size > ATTACH_MAX_BYTES) {
    return { ok: false, reason: OVERSIZE_REASON };
  }
  if (!hasLocalFiles()) {
    return prepareBrowserFileAttachment(conversationId, file, known);
  }

  if (!conversationId) {
    const staged = await stageDroppedFileAttachment(null, file);
    // 暂存失败不致命：内存里的 File 仍能在建会话后直传，只是重启后不留底。
    if (!staged.ok) return prepareBrowserFileAttachment(null, file, known);
    return { ...staged, fileBlob: file };
  }

  let isLocalWorkspace = false;
  try {
    isLocalWorkspace =
      (await getWorkspaceBinding(conversationId)).mode === "local";
  } catch {
    /* binding unknown — treat as cloud and try the upload */
  }
  if (isLocalWorkspace) {
    return stageDroppedFileAttachment(conversationId, file);
  }

  const meta = known ?? (await describeFileAttachment(file));
  return putFileToCloudWorkspace(conversationId, file, meta);
}

export interface ResidentAttachmentInput {
  name: string;
  stagingId?: string;
  workspacePath?: string;
  citedRootId?: string;
  citedRelPath?: string;
  binary?: boolean;
  text: string;
  truncated: boolean;
  fileBlob?: File;
}

export type ResidentAttachment =
  | {
      ok: true;
      workspacePath: string;
      name: string;
      binary: boolean;
      text: string;
      truncated: boolean;
      /** 还握着的字节：开跑前拒绝拆掉会话后，用它再驻留进新工作区。 */
      fileBlob?: File;
    }
  | ResideFailure;

function isStagingExpired(reason: string): boolean {
  return reason.includes("暂存已失效");
}

function residentOk(
  workspacePath: string,
  att: Pick<
    ResidentAttachmentInput,
    "name" | "binary" | "text" | "truncated" | "fileBlob"
  >,
  over: Partial<Extract<ResidentAttachment, { ok: true }>> = {},
): Extract<ResidentAttachment, { ok: true }> {
  const { fileBlob: overBlob, ...restOver } = over;
  const out: Extract<ResidentAttachment, { ok: true }> = {
    ok: true,
    workspacePath,
    name: att.name,
    binary: !!att.binary,
    text: att.text,
    truncated: att.truncated,
    ...restOver,
  };
  const blob = overBlob ?? att.fileBlob;
  if (blob) out.fileBlob = blob;
  return out;
}

/** 本机 dest 已解析：把 File 再暂存进当前会话 ``attachments/``。 */
async function restageFileBlobLocal(
  conversationId: string,
  file: File,
  att: ResidentAttachmentInput,
): Promise<ResidentAttachment> {
  const staged = await stageDroppedFileAttachment(conversationId, file);
  if (!staged.ok) return staged;
  const workspacePath = staged.workspacePath;
  if (typeof workspacePath !== "string") {
    return { ok: false, reason: "附件落盘未返回工作区路径" };
  }
  return residentOk(workspacePath, att, {
    name: staged.name,
    binary: staged.binary,
    text: staged.text,
    truncated: staged.truncated,
    fileBlob: file,
  });
}

/**
 * 兜底驻留：把还没落地的附件写入本地工作区或上传到云端工作区。
 * 已有 ``workspacePath`` **且没有可再拷的字节** 才跳过。区内引用（``citedRootId``
 * 仍落在当前 dest 树内）不 ``finalize`` 进 ``attachments/``。失败返回 reason。
 *
 * 正常路径上附件在附加时就已驻留完（{@link residentAttachmentForFile}），这里只兜
 * 三种情形：重启后从草稿恢复的暂存件、附加时上传失败后的发送重试、以及历史纯文本引用。
 *
 * 开跑前拒绝会拆掉刚建的会话：芯片上的 ``workspacePath`` 仍指向已删工作区，不能拿来
 * 跳过；``stagingId`` 往往已被 consume / finalize 吃掉，要从 ``fileBlob`` 或再暂存恢复。
 *
 * 分支顺序有讲究：本机工作区的 finalize 必须排在内存 File 直传之前——桌面草稿两者都
 * 有，若先看 File 就会把本该落进本机项目的附件误传到云端。
 */
export async function ensureAttachmentResident(
  conversationId: string,
  att: ResidentAttachmentInput,
): Promise<ResidentAttachment> {
  if (att.workspacePath && !att.fileBlob && !att.stagingId) {
    return residentOk(att.workspacePath, att);
  }

  if (att.citedRootId && att.citedRelPath) {
    const dest = await resolveAttachDest(conversationId);
    if (dest) {
      const rel = workspaceRelFromCite(dest, {
        rootId: att.citedRootId,
        relPath: att.citedRelPath,
      });
      if (rel) return residentOk(rel, att);
    }
  }

  if (att.stagingId && window.fsApi?.finalizeStagedAttachment) {
    const dest = await resolveAttachDest(conversationId);
    if (dest) {
      const res = await window.fsApi.finalizeStagedAttachment(
        att.stagingId,
        dest,
      );
      if (res.ok) {
        const workspacePath = res.data.workspacePath;
        if (typeof workspacePath !== "string") {
          return { ok: false, reason: "附件落盘未返回工作区路径" };
        }
        return residentOk(workspacePath, att, {
          name: res.data.name,
          binary: res.data.binary,
          text: res.data.text,
          truncated: res.data.truncated,
        });
      }
      if (!isStagingExpired(res.reason) || !att.fileBlob) {
        return { ok: false, reason: res.reason };
      }
      return restageFileBlobLocal(conversationId, att.fileBlob, att);
    }
  }

  // 本地模式但本机根不可用：勿误走云端 PUT（会 409）。
  try {
    const binding = await getWorkspaceBinding(conversationId);
    if (binding.mode === "local") {
      return { ok: false, reason: LOCAL_ROOT_UNAVAILABLE };
    }
  } catch {
    /* binding unknown — try cloud upload */
  }

  // 云端：内存里还握着 File 就直接 PUT，别再去主进程读回字节。
  if (att.fileBlob) {
    const name = safeBrowserFileName(att.name);
    const res = await putFileToCloudWorkspace(conversationId, att.fileBlob, {
      name,
      text: att.text,
      truncated: att.truncated,
      binary: !!att.binary,
    });
    if (!res.ok) return res;
    return residentOk(res.workspacePath ?? `attachments/${name}`, att, {
      name,
      fileBlob: att.fileBlob,
    });
  }

  if (!att.stagingId) {
    if (att.binary) {
      return { ok: false, reason: "附件数据已失效，请重新附加" };
    }
    // 纯文本旧路径（对话引用等）：无驻留字节。
    return {
      ok: true,
      workspacePath: "",
      name: att.name,
      binary: false,
      text: att.text,
      truncated: att.truncated,
    };
  }

  // 云端工作区、且字节只在主进程暂存里（重启后恢复的草稿）：取出字节 PUT。
  if (!window.fsApi?.consumeStagedBytes) {
    return { ok: false, reason: "无法将附件上传到云端工作区" };
  }
  const consumed = await window.fsApi.consumeStagedBytes(att.stagingId);
  if (!consumed.ok) return { ok: false, reason: consumed.reason };
  const workspacePath = `attachments/${consumed.data.name}`;
  const recovered = new File(
    [new Uint8Array(consumed.data.data)],
    consumed.data.name,
  );
  try {
    await uploadWorkspaceFile(conversationId, workspacePath, recovered);
  } catch (e) {
    return {
      ok: false,
      reason: e instanceof Error ? e.message : "上传附件到云端工作区失败",
      cause: e,
    };
  }
  return residentOk(workspacePath, att, {
    name: consumed.data.name,
    binary: consumed.data.binary,
    fileBlob: recovered,
  });
}
