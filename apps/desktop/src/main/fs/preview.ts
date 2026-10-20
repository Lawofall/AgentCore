import { promises as fs } from "node:fs";
import { dirname, extname } from "node:path";
import type {
  FilePreview,
  FsEncoding,
  FsEol,
  FsResult,
  FsTextFile,
  FsWriteInput,
  FsWriteResult,
} from "@shared/ipc-contract";
import {
  EDIT_READ_MAX,
  IMAGE_MIME,
  IMAGE_PREVIEW_CAP,
  PDF_PREVIEW_CAP,
  TEXT_PREVIEW_CAP,
} from "./constants";
import {
  fromErrno,
  fsErr,
  locate,
  realFail,
  realInside,
  toReason,
} from "./pathGuard";
import { ensureReady, getRoot } from "./roots";
import { decodeStoredText, encodeStoredText } from "./textCodec";
import { atomicWrite, resolveWritable } from "./workspace/write";

// --- 文档编辑（CodeMirror 源码编辑器）读写：完整正文 + 写前 CAS ---
//
// 与预览路径分工：预览 readFile 截断 256KB 且判别图片/二进制；编辑必须拿到完整正文，
// 截断后保存会丢尾，故走独立通道。编解码与 AI read/replace 同族：BOM/UTF-8/GB18030、
// 按 NUL 字节判二进制、回写按原文 eol 与编码还原换行。

/** 前 8000 字节含 NUL 即判二进制（与服务端 / 参考实现一致）。 */
export function sniffBinary(buf: Buffer): boolean {
  const n = Math.min(buf.length, 8000);
  for (let i = 0; i < n; i++) {
    if (buf[i] === 0) return true;
  }
  return false;
}

/** 解码：BOM → utf-8-bom；合法 UTF-8 → utf-8；否则 GB18030（IPC 名 `gbk`）。 */
export function decodeText(buf: Buffer): {
  encoding: FsEncoding;
  text: string;
} {
  const decoded = decodeStoredText(buf);
  if (decoded) return decoded;
  return { encoding: "gbk", text: new TextDecoder("gb18030").decode(buf) };
}

/** 编码：先规一化为 `\n`，再按 eol 还原；utf-8-bom 补 BOM；gbk 按 GB18030 写回。 */
export function encodeText(
  content: string,
  encoding: FsEncoding,
  eol: FsEol,
): Buffer {
  const normalized = content.replace(/\r\n/g, "\n");
  const withEol =
    eol === "crlf" ? normalized.replace(/\n/g, "\r\n") : normalized;
  return encodeStoredText(withEol, encoding);
}

export async function readFile(
  rootId: string,
  relPath: string,
): Promise<FsResult<FilePreview>> {
  await ensureReady();
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) return realFail(real);
  try {
    const st = await fs.stat(real.path);
    if (!st.isFile()) return { ok: false, reason: "不是文件", code: "invalid" };

    const ext = extname(real.path).toLowerCase();
    const imgMime = IMAGE_MIME[ext];
    if (imgMime) {
      if (st.size > IMAGE_PREVIEW_CAP) {
        return {
          ok: true,
          data: {
            kind: "binary",
            mime: imgMime,
            size: st.size,
            reason: "图片过大（超过 10MB）",
          },
        };
      }
      const buf = await fs.readFile(real.path);
      const dataUrl = `data:${imgMime};base64,${buf.toString("base64")}`;
      return {
        ok: true,
        data: { kind: "image", dataUrl, mime: imgMime, size: st.size },
      };
    }

    if (ext === ".pdf") {
      const pdfMime = "application/pdf";
      if (st.size > PDF_PREVIEW_CAP) {
        return {
          ok: true,
          data: {
            kind: "binary",
            mime: pdfMime,
            size: st.size,
            reason: "PDF 过大（超过 15MB）",
          },
        };
      }
      const buf = await fs.readFile(real.path);
      const dataUrl = `data:${pdfMime};base64,${buf.toString("base64")}`;
      return {
        ok: true,
        data: { kind: "pdf", dataUrl, mime: pdfMime, size: st.size },
      };
    }

    // 文本/二进制：仅读取前 256KB+1 字节用于判别与展示，避免大文件全量读入。
    const fh = await fs.open(real.path, "r");
    try {
      const buf = Buffer.alloc(TEXT_PREVIEW_CAP + 1);
      const { bytesRead } = await fh.read(buf, 0, TEXT_PREVIEW_CAP + 1, 0);
      const data = buf.subarray(0, bytesRead);
      if (data.includes(0)) {
        return {
          ok: true,
          data: {
            kind: "binary",
            mime: "application/octet-stream",
            size: st.size,
            reason: "无法在面板内预览",
          },
        };
      }
      const truncated = st.size > TEXT_PREVIEW_CAP;
      const slice = data.subarray(0, Math.min(bytesRead, TEXT_PREVIEW_CAP));
      const { text } = decodeText(slice);
      return { ok: true, data: { kind: "text", content: text, truncated } };
    } finally {
      await fh.close();
    }
  } catch (e) {
    return fromErrno(e);
  }
}

export async function readTextFile(
  rootId: string,
  relPath: string,
): Promise<FsResult<FsTextFile>> {
  await ensureReady();
  const loc = locate(rootId, relPath);
  if ("error" in loc) return loc.error;
  const real = await realInside(loc.root, loc.abs);
  if (!real.ok) return realFail(real);
  try {
    const st = await fs.stat(real.path);
    if (!st.isFile()) return fsErr("invalid", "不是文件");
    if (st.size > EDIT_READ_MAX) {
      return fsErr("invalid", "文件过大，暂不支持在面板内编辑");
    }
    const buf = await fs.readFile(real.path);
    if (sniffBinary(buf)) return fsErr("invalid", "二进制文件，无法编辑");
    const { encoding, text } = decodeText(buf);
    const eol: FsEol = text.includes("\r\n") ? "crlf" : "lf";
    return {
      ok: true,
      data: {
        content: text.replace(/\r\n/g, "\n"),
        mtimeMs: st.mtimeMs,
        encoding,
        eol,
      },
    };
  } catch (e) {
    return fromErrno(e);
  }
}

export async function writeTextFile(
  rootId: string,
  relPath: string,
  input: FsWriteInput,
): Promise<FsWriteResult> {
  await ensureReady();
  const root = getRoot(rootId);
  if (!root)
    return { ok: false, reason: "denied", message: "目录未授权或已移除" };
  const target = await resolveWritable(root, relPath);
  if (!target)
    return { ok: false, reason: "denied", message: "路径越界，已拒绝" };
  if (target === root.absPath) {
    return { ok: false, reason: "error", message: "目标是目录" };
  }

  // 写前 CAS：现存文件比对 mtime（四舍五入避毫秒抖动）；不存在则按基线区分
  // 「新建」（baseline 0）与「读过的文件已被删/移」（baseline>0 → 冲突，迫使重读）。
  let cur: import("node:fs").Stats | null = null;
  try {
    cur = await fs.stat(target);
  } catch {
    cur = null;
  }
  if (cur) {
    if (Math.round(cur.mtimeMs) !== Math.round(input.baselineMtimeMs)) {
      return { ok: false, reason: "conflict", diskMtimeMs: cur.mtimeMs };
    }
  } else if (input.baselineMtimeMs !== 0) {
    return { ok: false, reason: "conflict", diskMtimeMs: 0 };
  }

  const buf = encodeText(input.content, input.encoding, input.eol);
  try {
    await fs.mkdir(dirname(target), { recursive: true });
    await atomicWrite(target, buf);
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "EBUSY" || code === "EPERM" || code === "EACCES") {
      return { ok: false, reason: "locked", message: toReason(e) };
    }
    return { ok: false, reason: "error", message: toReason(e) };
  }
  try {
    const st = await fs.stat(target);
    return { ok: true, mtimeMs: st.mtimeMs };
  } catch (e) {
    return { ok: false, reason: "error", message: toReason(e) };
  }
}
