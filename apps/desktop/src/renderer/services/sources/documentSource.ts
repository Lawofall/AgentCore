import type { FileNode, FilePreviewResult, FileSource } from "@/lib/fileSource";
import { notifyWarning } from "@/lib/toast";
import { getDocument, writeDocument } from "@/services/documents";

/**
 * A {@link FileSource} over cloud **document entries**, so the AgentCore rail can reuse the
 * shared markdown editor host ({@link MarkdownFileEditor}) — full-text edit + preview + AI
 * 改写 + CAS conflict handling (Agent记忆与知识系统 · 统一 md 条目基座).
 *
 * `/v1/documents` addresses nodes by **id**, so a tab PATH simply IS its document id. The
 * source is path-aware (one instance serves every entry). tree / CRUD reject — the rail lists
 * via `services/documents`. Successful writes may carry `quota_warning` (user edited an
 * existing always entry past the pool) or `frontmatter_error` (unclosed fence; derived
 * on_demand); both are toasted, not treated as failure.
 */

const unsupported = (): Promise<never> =>
  Promise.reject(new Error("条目文档不支持该操作"));

function notifyQuotaWarning(warning: string | null | undefined): void {
  const text = warning?.trim();
  if (!text) return;
  notifyWarning("AI 暂时记不下新东西", {
    description: text,
    action: {
      label: "去整理",
      onClick: () => {
        window.location.hash = "/files";
      },
    },
  });
}

function notifyFrontmatterError(error: string | null | undefined): void {
  const text = error?.trim();
  if (!text) return;
  notifyWarning("规则格式有问题，这条暂时不按「每次生效」注入", {
    description: text,
  });
}

export function createDocumentSource(): FileSource {
  return {
    id: "documents",
    label: "条目",
    caps: { watch: false, transfer: false, edit: true, snapshots: false },
    listDir: (): Promise<FileNode[]> => Promise.resolve([]),
    read: async (path): Promise<FilePreviewResult> => {
      const doc = await getDocument(path);
      return { kind: "text", text: doc.content, truncated: false };
    },
    createFile: unsupported,
    mkdir: unsupported,
    move: unsupported,
    delete: unsupported,
    readForEdit: async (path) => {
      const doc = await getDocument(path);
      return {
        text: doc.content,
        version: { etag: doc.version },
        encoding: "utf-8",
        eol: "lf",
      };
    },
    writeText: async (path, input) => {
      const r = await writeDocument(
        path,
        input.content,
        input.baseline?.etag ?? null,
      );
      if (r.ok) {
        notifyQuotaWarning(r.quotaWarning);
        notifyFrontmatterError(r.frontmatterError);
        return { ok: true as const, version: { etag: r.version } };
      }
      return {
        ok: false as const,
        reason: "conflict" as const,
        version: { etag: r.version },
      };
    },
  };
}
