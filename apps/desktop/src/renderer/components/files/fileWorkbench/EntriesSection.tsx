import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { isFeatureUnavailable } from "@/lib/errors";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  type DocumentNode,
  deleteDocument,
  listScopeEntries,
  renameDocument,
} from "@/services/documents";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileText, Loader2, Pencil, Trash2 } from "lucide-react";
import { type HTMLAttributes, type ReactNode, forwardRef } from "react";

/** Which layer a section renders: GLOBAL entries, or one project's. */
export type EntryScope =
  | { kind: "global" }
  | { kind: "folder"; folderId: string };

const ENTRIES_QUERY_KEY = ["scope-entries"] as const;

/** Ensure an entry name is markdown so it opens in the shared editor. */
function ensureMdName(name: string): string {
  return /\.(md|markdown)$/i.test(name) ? name : `${name}.md`;
}

/**
 * Where to open an entry in the detail pane.
 * User-owned entries open via the documents source (path = document id).
 */
export type EntryOpenTarget = {
  channel: "document";
  path: string;
  name: string;
};

/** Map a listed document onto the workbench open channel. */
export function entryOpenTarget(doc: DocumentNode): EntryOpenTarget {
  return { channel: "document", path: doc.id, name: doc.name };
}

/**
 * Flat entry list for one AgentCore scope (目标形态 · 文件页形态).
 * No 记忆/规则/文档 folders — partition is scope only; each row shows
 * description + frontmatter errors. Create lives on the section / `.agentcore`
 * header so it still works while this list is unmounted (collapsed).
 */
export function EntriesSection({
  scope,
  documentActivePath,
  onOpen,
  onDeleted,
  onRenamed,
  indent = 0,
}: {
  scope: EntryScope;
  documentActivePath: string | null;
  onOpen: (target: EntryOpenTarget) => void;
  onDeleted: (target: EntryOpenTarget) => void;
  onRenamed: (target: EntryOpenTarget, name: string) => void;
  indent?: number;
}) {
  const queryClient = useQueryClient();
  const folderId = scope.kind === "folder" ? scope.folderId : null;

  const entries = useQuery({
    queryKey: [...ENTRIES_QUERY_KEY, folderId ?? "global"],
    queryFn: () => listScopeEntries(folderId),
    staleTime: 30_000,
    retry: (failureCount, error) =>
      !isFeatureUnavailable(error) && failureCount < 3,
  });

  const rows = [...(entries.data ?? [])].sort((a, b) =>
    a.name.localeCompare(b.name, "zh"),
  );
  const leafPad = indent + 8;

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ENTRIES_QUERY_KEY });
  };

  const renameEntry = async (doc: DocumentNode) => {
    if (doc.aiMaintained) return;
    const input = window.prompt("条目名称", doc.name);
    if (input === null) return;
    const name = ensureMdName(input.trim());
    if (name === ".md" || name === doc.name) return;
    try {
      await renameDocument(doc.id, name);
      await refresh();
      onRenamed(entryOpenTarget({ ...doc, name }), name);
    } catch (e) {
      notifyError(e, "重命名失败");
    }
  };

  const removeEntry = async (doc: DocumentNode) => {
    if (!window.confirm(`确定删除「${doc.name}」？此操作不可撤销。`)) return;
    try {
      const target = entryOpenTarget(doc);
      await deleteDocument(doc.id);
      onDeleted(target);
      await refresh();
    } catch (e) {
      notifyError(e, "删除失败");
    }
  };

  const isActive = (target: EntryOpenTarget) =>
    documentActivePath === target.path;

  const renderDocRow = (doc: DocumentNode) => {
    const target = entryOpenTarget(doc);
    return (
      <ContextMenu key={doc.id}>
        <ContextMenuTrigger asChild>
          <EntryLeafRow
            paddingLeft={leafPad}
            icon={
              <FileText size={14} className="shrink-0 text-muted-foreground" />
            }
            label={doc.name}
            description={doc.description}
            frontmatterError={doc.frontmatterError}
            active={isActive(target)}
            onOpen={() => onOpen(target)}
          />
        </ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem
            disabled={doc.aiMaintained}
            onSelect={() => void renameEntry(doc)}
          >
            <Pencil size={14} className="shrink-0" />
            <span className="flex-1 truncate">重命名</span>
          </ContextMenuItem>
          <ContextMenuItem
            variant="danger"
            onSelect={() => void removeEntry(doc)}
          >
            <Trash2 size={14} className="shrink-0" />
            <span className="flex-1 truncate">删除</span>
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    );
  };

  return (
    <div>
      {entries.isLoading ? (
        <div
          className="flex h-7 items-center gap-1.5 text-xs text-muted-foreground"
          style={{ paddingLeft: leafPad }}
        >
          <Loader2 size={12} className="animate-spin" />
          加载中…
        </div>
      ) : entries.isError ? (
        isFeatureUnavailable(entries.error) ? (
          <div
            title="服务端升级后自动恢复"
            className="flex min-h-7 items-center py-1 text-xs text-muted-foreground/60"
            style={{ paddingLeft: leafPad }}
          >
            条目功能暂不可用（服务端待升级）
          </div>
        ) : (
          <button
            type="button"
            onClick={() => void entries.refetch()}
            style={{ paddingLeft: leafPad }}
            className="flex h-7 w-full items-center gap-1 text-left text-xs text-muted-foreground hover:underline"
          >
            加载失败，点此重试
          </button>
        )
      ) : rows.length === 0 ? (
        <div
          className="flex flex-col gap-1 py-1"
          style={{ paddingLeft: leafPad }}
        >
          <p className="text-xs text-muted-foreground/60">
            {scope.kind === "global" ? "还没有全局条目" : "本文件夹还没有条目"}
          </p>
        </div>
      ) : (
        rows.map((doc) => renderDocRow(doc))
      )}
    </div>
  );
}

const EntryLeafRow = forwardRef<
  HTMLDivElement,
  {
    paddingLeft: number;
    icon: ReactNode;
    label: string;
    description: string;
    frontmatterError: string | null;
    active: boolean;
    onOpen: () => void;
    dimmed?: boolean;
  } & Omit<HTMLAttributes<HTMLDivElement>, "onClick">
>(function EntryLeafRow(
  {
    paddingLeft,
    icon,
    label,
    description,
    frontmatterError,
    active,
    onOpen,
    dimmed = false,
    className,
    style,
    ...rest
  },
  ref,
) {
  const hasMeta = Boolean(description || frontmatterError);
  return (
    <div
      ref={ref}
      {...rest}
      style={{ paddingLeft, ...style }}
      className={cn(
        "flex w-full items-start gap-1.5 rounded-lg py-1 pr-1 text-sm transition-colors",
        hasMeta ? "min-h-7" : "h-7 items-center",
        dimmed && !active && "text-muted-foreground opacity-60",
        active
          ? "bg-accent text-foreground"
          : "text-foreground hover:bg-accent/60",
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-start gap-1.5 rounded-lg text-left"
      >
        <span className={cn("shrink-0", hasMeta ? "mt-0.5" : "")}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1">
            <span className="min-w-0 truncate">{label}</span>
            {frontmatterError ? (
              <span
                title={`frontmatter 无效，该条不生效：${frontmatterError}`}
                className="inline-flex shrink-0 items-center gap-0.5 text-destructive"
              >
                <AlertTriangle size={12} aria-hidden />
                <span className="text-xs">不生效</span>
              </span>
            ) : null}
          </span>
          {frontmatterError ? (
            <span className="mt-0.5 block truncate text-xs text-destructive/80">
              {frontmatterError}
            </span>
          ) : description ? (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {description}
            </span>
          ) : null}
        </span>
      </button>
    </div>
  );
});
EntryLeafRow.displayName = "EntryLeafRow";

export { EntryLeafRow };
