import {
  type FileEditDoc,
  type FileNode,
  type FilePreviewResult,
  type FileSource,
  type WriteTextResult,
  baseName,
  isMarkdownPath,
  parentDir,
} from "@/lib/fileSource";
import { isAgentCoreMemoryDirPath } from "@/lib/stageDirs";
import { convertMdToDocx, convertMdToPdf } from "@/services/workspaces";
import type {
  FsErrorCode,
  FilePreview as LocalPreview,
} from "@shared/ipc-contract";

/** Map the local IPC preview shape into the unified result. */
function adaptPreview(p: LocalPreview): FilePreviewResult {
  if (p.kind === "text") {
    return { kind: "text", text: p.content, truncated: p.truncated };
  }
  if (p.kind === "image") {
    return { kind: "image", dataUrl: p.dataUrl, mime: p.mime, size: p.size };
  }
  if (p.kind === "pdf") {
    return { kind: "pdf", dataUrl: p.dataUrl, mime: p.mime, size: p.size };
  }
  return { kind: "binary", mime: p.mime, size: p.size, reason: p.reason };
}

/** Thrown from local IPC failures so callers can branch on {@link FsErrorCode}. */
export class LocalFsError extends Error {
  readonly code: FsErrorCode;
  constructor(reason: string, code: FsErrorCode) {
    super(reason);
    this.name = "LocalFsError";
    this.code = code;
  }
}

function throwFs(reason: string, code: FsErrorCode): never {
  throw new LocalFsError(reason, code);
}

/**
 * A {@link FileSource} over an authorized local OS root (desktop IPC,
 * `window.fsApi`).
 *
 * Native lazy per-directory listing + live watch; no byte transfer (files are
 * already on disk) and no snapshots (a workspace concern). The
 * local IPC splits "rename in place" (a bare new name) from "move into a
 * directory", so the unified full-destination `move` is translated back to
 * whichever the IPC expresses; a simultaneous move+rename isn't expressible in
 * one call today and is rejected (the tree never issues that pair).
 *
 * `subpath` (工作区对称化 D1a): when this source is a per-conversation workspace
 * lazily promoted under a shared container root, every workspace-relative path is
 * prefixed with the subpath on the way to the IPC and stripped on the way back —
 * so the tree, editor, and watcher only ever see paths relative to *this*
 * workspace, never the container root. `""` = the root itself (an explicitly-added
 * local project), a pure pass-through.
 *
 * Lazy materialization: a non-empty `subpath` may not exist on disk yet (zero
 * files). Reads treat `not_found` as empty; writes mkdir-recursive in main.
 */
export function createLocalRootSource(
  rootId: string,
  label: string,
  subpath = "",
): FileSource {
  const base = subpath.replace(/^\/+|\/+$/g, "");
  // Workspace-relative → container-relative (prefix). "" / root → the base itself.
  const inPath = (p: string): string => {
    if (!base) return p;
    const rel = p.replace(/^\/+/, "");
    return rel ? `${base}/${rel}` : base;
  };
  // Container-relative → workspace-relative (strip). Inverse of inPath.
  const outPath = (p: string): string => {
    if (!base) return p;
    if (p === base) return "";
    const prefix = `${base}/`;
    return p.startsWith(prefix) ? p.slice(prefix.length) : p;
  };
  return {
    // Distinct id per subpath workspace so the hub treats two workspaces under the
    // same container root as separate sources (caching / active selection by id).
    id: base ? `local:${rootId}:${base}` : `local:${rootId}`,
    label,
    caps: {
      watch: true,
      transfer: false,
      edit: true,
      snapshots: false,
    },
    async listDir(dir) {
      const res = await window.fsApi.listDir(rootId, inPath(dir));
      if (!res.ok) {
        // Lazy workspace: missing base (or unrealized nested path) ≡ empty.
        if (base && res.code === "not_found") return [];
        throwFs(res.reason, res.code);
      }
      return res.data
        .map(
          (e): FileNode => ({
            path: outPath(e.relPath),
            name: e.name,
            isDir: e.kind === "dir",
            sizeBytes: e.size,
            mtimeMs: e.modifiedMs,
          }),
        )
        .filter((n) => !isAgentCoreMemoryDirPath(n.path));
    },
    async listFileIndex() {
      // Flat file list for the @ index (文件中枢统一 F4); the IPC already prunes
      // node_modules/.git… and caps the count, matching the cloud /file-index.
      // `order: "recent"` 带 mtime，空态按最近修改排。A subpath workspace indexes
      // the whole container root then filters to its subtree — wasteful but
      // self-contained (no IPC surface), and rarely hit: the /files rail browses
      // via listDir, and @ mention sources are built at root level.
      //
      // Lazy workspace: probe base first — missing ≡ empty index (no full scan).
      if (base) {
        const probe = await window.fsApi.listDir(rootId, base);
        if (!probe.ok) {
          if (probe.code === "not_found") {
            return { files: [], truncated: false };
          }
          throwFs(probe.reason, probe.code);
        }
      }
      const res = await window.fsApi.listFiles(rootId, { order: "recent" });
      if (!res.ok) throwFs(res.reason, res.code);
      const { files, truncated } = res.data;
      if (!base) {
        return {
          files: files
            .filter((f) => !isAgentCoreMemoryDirPath(f.relPath))
            .map((f) => ({
              relPath: f.relPath,
              mtimeMs: f.mtimeMs,
            })),
          truncated,
        };
      }
      const prefix = `${base}/`;
      return {
        files: files
          .filter((f) => f.relPath.startsWith(prefix))
          .map((f) => ({
            relPath: f.relPath.slice(prefix.length),
            mtimeMs: f.mtimeMs,
          }))
          .filter((f) => !isAgentCoreMemoryDirPath(f.relPath)),
        truncated,
      };
    },
    async read(path): Promise<FilePreviewResult> {
      const resolvedPath = inPath(path);
      const res = await window.fsApi.readFile(rootId, resolvedPath);
      if (!res.ok) {
        // not_found is a calm answer for preview; other codes stay error-logged.
        if (res.code !== "not_found") {
          console.error(
            `[FilePreview] localRootSource.read failed ${JSON.stringify({
              rootId,
              path,
              resolvedPath,
              reason: res.reason,
              code: res.code,
            })}`,
          );
        }
        throwFs(res.reason, res.code);
      }
      return adaptPreview(res.data);
    },
    async readForEdit(path): Promise<FileEditDoc> {
      const res = await window.fsApi.readTextFile(rootId, inPath(path));
      if (!res.ok) throwFs(res.reason, res.code);
      const { content, mtimeMs, encoding, eol } = res.data;
      return { text: content, version: { mtimeMs }, encoding, eol };
    },
    async writeText(path, input): Promise<WriteTextResult> {
      // GBK 只读：宿主已以只读打开，这里再兜一道，绝不静默改编码。
      if (input.encoding === "gbk") {
        return {
          ok: false,
          reason: "unsupported",
          message: "GBK 文件回写暂未启用",
        };
      }
      const res = await window.fsApi.writeFile(rootId, inPath(path), {
        content: input.content,
        encoding: input.encoding,
        eol: input.eol,
        baselineMtimeMs: input.baseline?.mtimeMs ?? 0,
      });
      if (res.ok) return { ok: true, version: { mtimeMs: res.mtimeMs } };
      if (res.reason === "conflict") {
        return {
          ok: false,
          reason: "conflict",
          version: { mtimeMs: res.diskMtimeMs },
        };
      }
      return { ok: false, reason: res.reason, message: res.message };
    },
    async createFile(path) {
      const res = await window.fsApi.create(rootId, inPath(path), "file");
      if (!res.ok) throwFs(res.reason, res.code);
    },
    async mkdir(path) {
      const res = await window.fsApi.create(rootId, inPath(path), "dir");
      if (!res.ok) throwFs(res.reason, res.code);
    },
    async move(src, dst) {
      // sameParent/sameName are computed on the workspace-relative paths (a prefix
      // shared by both sides cancels out), then translated to whichever IPC the
      // local source expresses, with the subpath prefixed onto the actual targets.
      const sameParent = parentDir(src) === parentDir(dst);
      const sameName = baseName(src) === baseName(dst);
      if (sameParent && sameName) return;
      if (sameParent) {
        const res = await window.fsApi.rename(
          rootId,
          inPath(src),
          baseName(dst),
        );
        if (!res.ok) throwFs(res.reason, res.code);
        return;
      }
      if (sameName) {
        const res = await window.fsApi.move(
          rootId,
          inPath(src),
          inPath(parentDir(dst)),
        );
        if (!res.ok) throwFs(res.reason, res.code);
        return;
      }
      throw new Error("暂不支持同时移动并改名");
    },
    async copy(src, dst) {
      // copy 收完整目标路径（与 move 译成 rename/move-into-dir 不同）：主进程经 fs.cp 递归
      // 复制到该精确路径，故能在同目录内另存为新名（去重粘贴）。subpath 前缀两端同样抵消。
      const res = await window.fsApi.copy(rootId, inPath(src), inPath(dst));
      if (!res.ok) throwFs(res.reason, res.code);
    },
    async delete(path) {
      // 文件树删除走系统回收站（`shell.trashItem`）。失败如实上抛，禁止回退
      // `fsApi.delete` / `fs.rm`——那会让确认框「可从回收站还原」变成假承诺。
      const res = await window.fsApi.trashPath(rootId, inPath(path));
      if (!res.ok) throwFs(res.reason, res.code);
    },
    watch(dir, onChange) {
      // Missing dirs are tolerated (fs.watch throws → main ignores). FileTree
      // re-attaches after a silent root patch goes empty→has — do not swap source.
      const watched = inPath(dir);
      void window.fsApi.watch(rootId, watched);
      const off = window.fsApi.onChanged((e) => {
        if (e.rootId === rootId && e.relPath === watched) onChange(dir);
      });
      return () => {
        void window.fsApi.unwatch(rootId, watched);
        off();
      };
    },
    // 系统集成：主进程把 inPath(path) 解析为绝对路径并校验在根内，再交给系统。绝对路径
    // 不回到 renderer。失败以异常上抛，调用方 toast（与本源其他方法的 `!ok` 抛错一致）。
    async revealInOsFileManager(path) {
      const res = await window.fsApi.reveal(rootId, inPath(path));
      if (!res.ok) throwFs(res.reason, res.code);
    },
    async openWithOsDefaultApp(path) {
      const res = await window.fsApi.openPath(rootId, inPath(path));
      if (!res.ok) throwFs(res.reason, res.code);
    },
    // 「在浏览器打开」本地 HTML：文件已在磁盘，直接用系统默认程序打开（= 系统浏览器）。
    async openInBrowser(path) {
      const res = await window.fsApi.openPath(rootId, inPath(path));
      if (!res.ok) throwFs(res.reason, res.code);
    },
    async openShellAtPath(path) {
      const api = window.terminalApi;
      if (!api?.openShellAtRoot) {
        throw new Error("终端不可用（非桌面环境）");
      }
      const normalized =
        path === "" || path === "." ? "." : path.replace(/^\/+|\/+$/g, "");
      const containerSub =
        normalized === "." ? base || "." : inPath(normalized);
      const result = await api.openShellAtRoot(rootId, containerSub);
      if (!result.ok) throw new Error(result.reason);
    },
    async exportMarkdown(path, format, layout = "standard") {
      if (!isMarkdownPath(path)) {
        throw new Error("仅支持导出 Markdown（.md / .markdown）");
      }
      const doc = await (async () => {
        const res = await window.fsApi.readTextFile(rootId, inPath(path));
        if (!res.ok) throwFs(res.reason, res.code);
        return res.data.content;
      })();

      if (format === "pdf") {
        const converted = await convertMdToPdf({
          markdown: doc,
          sourceName: baseName(path),
          layout,
        });
        const outName = converted.suggestedFilename;
        const outPath = parentDir(path)
          ? `${parentDir(path)}/${outName}`
          : outName;
        const wb = await window.fsApi.workspaceOp(rootId, "write_bytes", {
          path: inPath(outPath),
          data: converted.pdfBase64,
        });
        if (!wb.ok) {
          throw new Error(wb.error.detail || "写入 PDF 失败");
        }
        return { path: outPath, warnings: converted.warnings };
      }

      // Collect relative image srcs (same heuristic as server collect_image_srcs).
      const imgRe = /!\[[^\]]*]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
      const images: Record<string, string | null> = {};
      let m = imgRe.exec(doc);
      while (m !== null) {
        const src = m[1]?.trim();
        if (!src || src in images) {
          m = imgRe.exec(doc);
          continue;
        }
        if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(src) || src.startsWith("data:")) {
          m = imgRe.exec(doc);
          continue;
        }
        let cleaned = src.replace(/\\/g, "/");
        while (cleaned.startsWith("./")) cleaned = cleaned.slice(2);
        const dir = parentDir(path);
        const joined = dir ? `${dir}/${cleaned}` : cleaned;
        const parts = joined.split("/");
        const stack: string[] = [];
        for (const p of parts) {
          if (!p || p === ".") continue;
          if (p === "..") {
            if (stack.length === 0) {
              images[src] = null;
              break;
            }
            stack.pop();
            continue;
          }
          stack.push(p);
        }
        if (!(src in images)) {
          const wsImg = stack.join("/");
          const rb = await window.fsApi.workspaceOp(rootId, "read_bytes", {
            path: inPath(wsImg),
          });
          if (rb.ok && typeof rb.value === "string") {
            images[src] = rb.value;
          } else {
            images[src] = null;
          }
        }
        m = imgRe.exec(doc);
      }

      const converted = await convertMdToDocx({
        markdown: doc,
        images,
        sourceName: baseName(path),
        layout,
      });
      const outName = converted.suggestedFilename;
      const outPath = parentDir(path)
        ? `${parentDir(path)}/${outName}`
        : outName;
      const wb = await window.fsApi.workspaceOp(rootId, "write_bytes", {
        path: inPath(outPath),
        data: converted.docxBase64,
      });
      if (!wb.ok) {
        throw new Error(wb.error.detail || "写入 Word 失败");
      }
      return { path: outPath, warnings: converted.warnings };
    },
  };
}
