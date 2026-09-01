import { Badge, ConfirmDialog } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { isFeatureUnavailable } from "@/lib/errors";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  type DocumentApplyMode,
  type DocumentNode,
  deleteDocument,
  listScopeEntries,
  renameDocument,
  setDocumentDisputed,
  updateDocumentApplyMode,
} from "@/services/documents";
import { type MemoryKind, writeMemoryFile } from "@/services/memory";
import {
  GLOBAL_PREFERENCES_PATH,
  GLOBAL_PROFILE_PATH,
  MEMORY_UPDATES_PATH,
  memoryProjectNavigationPath,
  memoryProjectProfilePath,
  memoryTopicPath,
} from "@/services/sources/memorySource";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Eraser,
  FileText,
  History,
  Loader2,
  Pencil,
  ThumbsDown,
  Trash2,
  Undo2,
} from "lucide-react";
import { type ReactNode, forwardRef, useState } from "react";
import { DisputeEntryDialog } from "./DisputeEntryDialog";

/** Which layer a section renders: GLOBAL entries, or one project's. */
export type EntryScope =
  | { kind: "global" }
  | { kind: "folder"; folderId: string };

const ENTRIES_QUERY_KEY = ["scope-entries"] as const;

const APPLY_LABEL: Record<DocumentApplyMode, string> = {
  always: "常驻",
  on_demand: "按需",
};

const APPLY_HINT: Record<DocumentApplyMode, string> = {
  always: "每次对话都会带上",
  on_demand: "需要时再查阅",
};

/**
 * Rows under this print no size at all. A row's char count exists to answer
 * 「池子紧张时该删谁」; against a 24k pool a sub-千字 entry answers it with nothing,
 * and it is the common case — repeated down the whole list the number stops
 * reading as a signal and just eats the width the filename needs.
 */
const ROW_CHARS_FLOOR = 1000;

/** Fixed AI core leaf names (aligned with server ``memory.store`` / write_guards). */
const AI_CORE_NAMES = new Set(["偏好.md", "画像.md", "导航.md"]);

/** AI-maintained 画像 / 偏好 / 导航 — named slots; clear via empty PUT, not DELETE. */
export function isAiCoreMemoryLeaf(
  doc: Pick<DocumentNode, "name" | "aiMaintained">,
): boolean {
  return doc.aiMaintained && AI_CORE_NAMES.has(doc.name);
}

/** Map a core leaf onto the per-file memory write surface; ``null`` = not a core. */
export function coreMemoryLeafKind(
  doc: Pick<DocumentNode, "name" | "aiMaintained" | "folderId">,
): MemoryKind | null {
  if (!isAiCoreMemoryLeaf(doc)) return null;
  if (doc.name === "偏好.md") return "preferences";
  if (doc.name === "画像.md") return "profile";
  if (doc.name === "导航.md") return "navigation";
  return null;
}

/** Ensure an entry name is markdown so it opens in the shared editor. */
function ensureMdName(name: string): string {
  return /\.(md|markdown)$/i.test(name) ? name : `${name}.md`;
}

/** Cold-start placeholders so core leaves stay visible before any document row exists. */
type CorePlaceholder = {
  name: string;
  path: string;
  applyMode: DocumentApplyMode;
};

function corePlaceholders(scope: EntryScope): CorePlaceholder[] {
  if (scope.kind === "global") {
    return [
      {
        name: "偏好.md",
        path: GLOBAL_PREFERENCES_PATH,
        applyMode: "always",
      },
      {
        name: "画像.md",
        path: GLOBAL_PROFILE_PATH,
        applyMode: "always",
      },
    ];
  }
  return [
    {
      name: "画像.md",
      path: memoryProjectProfilePath(scope.folderId),
      applyMode: "always",
    },
    {
      name: "导航.md",
      path: memoryProjectNavigationPath(scope.folderId),
      applyMode: "always",
    },
  ];
}

type DisplayRow =
  | { kind: "doc"; doc: DocumentNode }
  | { kind: "placeholder"; leaf: CorePlaceholder };

function mergeDisplayRows(
  scope: EntryScope,
  docs: DocumentNode[],
): DisplayRow[] {
  const present = new Set(docs.map((d) => d.name));
  const rows: DisplayRow[] = [
    ...docs.map((doc): DisplayRow => ({ kind: "doc", doc })),
    ...corePlaceholders(scope)
      .filter((leaf) => !present.has(leaf.name))
      .map((leaf): DisplayRow => ({ kind: "placeholder", leaf })),
  ];
  return rows.sort((a, b) => {
    const an = a.kind === "doc" ? a.doc.name : a.leaf.name;
    const bn = b.kind === "doc" ? b.doc.name : b.leaf.name;
    return an.localeCompare(bn, "zh");
  });
}

/**
 * Where to open an entry in the detail pane.
 * AI-maintained notes keep memory synthetic paths (editor + 双栏画像); user-owned
 * entries open via the documents source (path = document id).
 */
export type EntryOpenTarget =
  | { channel: "memory"; path: string; name: string }
  | { channel: "document"; path: string; name: string };

/** Map a listed document onto the workbench open channel. */
export function entryOpenTarget(doc: DocumentNode): EntryOpenTarget {
  if (doc.aiMaintained) {
    const memoryPath = memoryPathForDocument(doc);
    if (memoryPath) {
      return { channel: "memory", path: memoryPath, name: doc.name };
    }
  }
  return { channel: "document", path: doc.id, name: doc.name };
}

function memoryPathForDocument(doc: DocumentNode): string | null {
  const { name, folderId } = doc;
  if (name === "偏好.md" && folderId == null) return GLOBAL_PREFERENCES_PATH;
  if (name === "画像.md") {
    return folderId ? memoryProjectProfilePath(folderId) : GLOBAL_PROFILE_PATH;
  }
  if (name === "导航.md" && folderId != null) {
    return memoryProjectNavigationPath(folderId);
  }
  const topic = /^主题\/(.+?)(?:\.md)?$/i.exec(name);
  if (topic) return memoryTopicPath(folderId, topic[1]);
  return null;
}

/**
 * Coarsen char counts for humans: 千字 / 万字 buckets, never exact ones.
 * 0 and「不足千」are distinct — empty is not "almost a thousand".
 * Exported for unit tests.
 */
export function formatRoughChars(n: number): string {
  const chars = Math.max(0, Math.round(n));
  if (chars === 0) return "0 字";
  if (chars < 1000) return "不足千字";
  if (chars < 9500) return `约 ${Math.max(1, Math.round(chars / 1000))} 千字`;
  const wan = Math.round(chars / 1000) / 10;
  const label = Number.isInteger(wan) ? String(wan) : wan.toFixed(1);
  return `约 ${label} 万字`;
}

/** Per-entry always size (same coarsening as the meter). */
export function formatAlwaysChars(n: number): string {
  return formatRoughChars(n);
}

/**
 * Flat entry list for one AgentCore scope (目标形态 · 文件页形态).
 * No 记忆/规则/文档 folders — partition is scope only; each row shows 常驻/按需 +
 * description + frontmatter errors. Create lives on the section / `.agentcore`
 * header so it still works while this list is unmounted (collapsed).
 */
export function EntriesSection({
  scope,
  memoryActivePath,
  documentActivePath,
  onOpen,
  onDeleted,
  onRenamed,
  onOpenUpdates,
  indent = 0,
}: {
  scope: EntryScope;
  memoryActivePath: string | null;
  documentActivePath: string | null;
  onOpen: (target: EntryOpenTarget) => void;
  onDeleted: (target: EntryOpenTarget) => void;
  onRenamed: (target: EntryOpenTarget, name: string) => void;
  /** GLOBAL-only「最近更新」feed opener. */
  onOpenUpdates?: () => void;
  indent?: number;
}) {
  const queryClient = useQueryClient();
  const folderId = scope.kind === "folder" ? scope.folderId : null;
  const [disputing, setDisputing] = useState<DocumentNode | null>(null);
  const [disputeBusy, setDisputeBusy] = useState(false);
  const [clearing, setClearing] = useState<DocumentNode | null>(null);
  const [clearBusy, setClearBusy] = useState(false);

  const entries = useQuery({
    queryKey: [...ENTRIES_QUERY_KEY, folderId ?? "global"],
    queryFn: () => listScopeEntries(folderId),
    staleTime: 30_000,
    retry: (failureCount, error) =>
      !isFeatureUnavailable(error) && failureCount < 3,
  });

  const rows = entries.data ?? [];
  const displayRows = mergeDisplayRows(scope, rows);
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
    if (isAiCoreMemoryLeaf(doc)) return;
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

  const confirmClearCoreLeaf = async () => {
    const doc = clearing;
    if (!doc || clearBusy) return;
    const kind = coreMemoryLeafKind(doc);
    if (!kind) return;
    setClearBusy(true);
    try {
      const result = await writeMemoryFile(kind, "", null, doc.folderId);
      if (!result.ok) {
        notifyError("这篇设定刚被改过，请刷新后再试。", "清空失败");
        return;
      }
      onDeleted(entryOpenTarget(doc));
      setClearing(null);
      await refresh();
    } catch (e) {
      notifyError(e, "清空失败");
    } finally {
      setClearBusy(false);
    }
  };

  const setApplyMode = async (doc: DocumentNode, mode: DocumentApplyMode) => {
    if (doc.aiMaintained || doc.applyMode === mode) return;
    try {
      await updateDocumentApplyMode(doc.id, mode);
      await refresh();
    } catch (e) {
      notifyError(e, "切换失败");
    }
  };

  // 纠错通道: only the user can say「这条不对」, and saying it stops the entry from being
  // used without deleting it — the text stays here to read, re-check and undo.
  const setDisputed = async (doc: DocumentNode, disputed: boolean) => {
    try {
      await setDocumentDisputed(doc.id, disputed);
      await refresh();
      return true;
    } catch (e) {
      notifyError(e, disputed ? "标记失败" : "撤销标记失败");
      return false;
    }
  };

  // Marking goes through {@link DisputeEntryDialog} first: the mark is entry-level while
  // the user usually means one sentence, so the other lines it silences get named before
  // the click lands. Undo needs no such warning — it only gives usage back.
  const confirmDispute = async () => {
    const doc = disputing;
    if (!doc || disputeBusy) return;
    setDisputeBusy(true);
    try {
      if (await setDisputed(doc, true)) setDisputing(null);
    } finally {
      setDisputeBusy(false);
    }
  };

  const isActive = (target: EntryOpenTarget) =>
    target.channel === "memory"
      ? memoryActivePath === target.path
      : documentActivePath === target.path;

  const renderDocRow = (doc: DocumentNode) => {
    const mode = doc.applyMode;
    const other: DocumentApplyMode = mode === "always" ? "on_demand" : "always";
    const canToggleApply = !doc.aiMaintained && !doc.frontmatterError;
    const disputed = doc.disputedAt != null;
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
            disputed={disputed}
            active={isActive(target)}
            onOpen={() => onOpen(target)}
            applyMode={mode}
            alwaysChars={doc.alwaysChars}
            onToggleApplyMode={
              canToggleApply ? () => void setApplyMode(doc, other) : undefined
            }
          />
        </ContextMenuTrigger>
        <ContextMenuContent className="min-w-36">
          <ContextMenuItem
            disabled={!canToggleApply || mode === "always"}
            title={APPLY_HINT.always}
            onSelect={() => void setApplyMode(doc, "always")}
          >
            <span className="flex-1 truncate">设为常驻</span>
          </ContextMenuItem>
          <ContextMenuItem
            disabled={!canToggleApply || mode === "on_demand"}
            title={APPLY_HINT.on_demand}
            onSelect={() => void setApplyMode(doc, "on_demand")}
          >
            <span className="flex-1 truncate">设为按需</span>
          </ContextMenuItem>
          <ContextMenuSeparator />
          {disputed ? (
            <ContextMenuItem
              title="恢复后 AI 会重新使用这条"
              onSelect={() => void setDisputed(doc, false)}
            >
              <Undo2 size={14} className="shrink-0" />
              <span className="flex-1 truncate">恢复使用</span>
            </ContextMenuItem>
          ) : (
            <ContextMenuItem
              title="停用整个条目：AI 不再使用，内容保留，可随时恢复"
              onSelect={() => setDisputing(doc)}
            >
              <ThumbsDown size={14} className="shrink-0" />
              <span className="flex-1 truncate">这条不对…</span>
            </ContextMenuItem>
          )}
          <ContextMenuItem
            disabled={doc.aiMaintained}
            onSelect={() => void renameEntry(doc)}
          >
            <Pencil size={14} className="shrink-0" />
            <span className="flex-1 truncate">重命名</span>
          </ContextMenuItem>
          {isAiCoreMemoryLeaf(doc) ? (
            <ContextMenuItem variant="danger" onSelect={() => setClearing(doc)}>
              <Eraser size={14} className="shrink-0" />
              <span className="flex-1 truncate">清空</span>
            </ContextMenuItem>
          ) : (
            <ContextMenuItem
              variant="danger"
              onSelect={() => void removeEntry(doc)}
            >
              <Trash2 size={14} className="shrink-0" />
              <span className="flex-1 truncate">删除</span>
            </ContextMenuItem>
          )}
        </ContextMenuContent>
      </ContextMenu>
    );
  };

  const renderPlaceholderRow = (leaf: CorePlaceholder) => {
    const target: EntryOpenTarget = {
      channel: "memory",
      path: leaf.path,
      name: leaf.name,
    };
    return (
      <EntryLeafRow
        key={`placeholder:${leaf.path}`}
        paddingLeft={leafPad}
        icon={<FileText size={14} className="shrink-0 text-muted-foreground" />}
        label={leaf.name}
        description=""
        frontmatterError={null}
        disputed={false}
        active={isActive(target)}
        onOpen={() => onOpen(target)}
        applyMode={leaf.applyMode}
      />
    );
  };

  return (
    <div>
      {scope.kind === "global" && onOpenUpdates && (
        <EntryLeafRow
          paddingLeft={leafPad}
          icon={
            <History size={14} className="shrink-0 text-muted-foreground" />
          }
          label="最近更新"
          description=""
          frontmatterError={null}
          disputed={false}
          active={memoryActivePath === MEMORY_UPDATES_PATH}
          onOpen={onOpenUpdates}
        />
      )}

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
      ) : displayRows.length === 0 ? (
        <div
          className="flex flex-col gap-1 py-1"
          style={{ paddingLeft: leafPad }}
        >
          <p className="text-xs text-muted-foreground/60">
            {scope.kind === "global" ? "还没有全局条目" : "本文件夹还没有条目"}
          </p>
          <p className="text-xs text-muted-foreground/50">
            短硬约束用常驻，厚知识用按需
          </p>
        </div>
      ) : (
        displayRows.map((row) =>
          row.kind === "doc"
            ? renderDocRow(row.doc)
            : renderPlaceholderRow(row.leaf),
        )
      )}

      <DisputeEntryDialog
        doc={disputing}
        busy={disputeBusy}
        onOpenChange={(open) => {
          if (!open) setDisputing(null);
        }}
        onConfirm={() => void confirmDispute()}
      />
      <ConfirmDialog
        open={clearing != null}
        onOpenChange={(open) => {
          if (!open) setClearing(null);
        }}
        title={clearing ? `清空「${clearing.name}」？` : "清空这篇设定？"}
        description="下一句对话 AI 不再使用这篇。列表里还会留下这个名字，方便以后再写。项目文件不会被删。"
        confirmLabel="清空"
        tone="danger"
        busy={clearBusy}
        onConfirm={() => void confirmClearCoreLeaf()}
      />
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
    /** User marked this entry wrong: AI stops using it, the text stays (纠错通道). */
    disputed?: boolean;
    active: boolean;
    onOpen: () => void;
    applyMode?: DocumentApplyMode;
    /** Always-pool chars for this row; only shown when always + non-null. */
    alwaysChars?: number | null;
    onToggleApplyMode?: () => void;
  }
>(function EntryLeafRow(
  {
    paddingLeft,
    icon,
    label,
    description,
    frontmatterError,
    disputed = false,
    active,
    onOpen,
    applyMode,
    alwaysChars,
    onToggleApplyMode,
    ...rest
  },
  ref,
) {
  const hasMeta = Boolean(description || frontmatterError);
  // A disputed entry no longer rides the prompt, so its always size is not being spent.
  // Empty rows stay silent too, which is also what keeps a cold-start placeholder and a
  // written-but-empty entry looking the same.
  const showAlwaysChars =
    applyMode === "always" &&
    !disputed &&
    typeof alwaysChars === "number" &&
    Number.isFinite(alwaysChars) &&
    alwaysChars >= ROW_CHARS_FLOOR;
  return (
    <div
      ref={ref}
      {...rest}
      style={{ paddingLeft }}
      className={cn(
        "flex w-full items-start gap-1.5 rounded-lg py-1 pr-1 text-sm transition-colors",
        hasMeta ? "min-h-7" : "h-7 items-center",
        active
          ? "bg-accent text-foreground"
          : "text-foreground hover:bg-accent/60",
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
            <span
              className={cn(
                "min-w-0 truncate",
                disputed && "text-muted-foreground line-through",
              )}
            >
              {label}
            </span>
            {disputed ? (
              <span
                title="你标了「这条不对」：AI 不再使用，内容仍保留（右键可恢复）"
                className="inline-flex shrink-0 items-center gap-0.5 text-muted-foreground"
              >
                <ThumbsDown size={12} aria-hidden />
                <span className="text-xs">已停用</span>
              </span>
            ) : null}
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
      {showAlwaysChars ? (
        <span
          title="每次对话都会带上"
          className={cn(
            "shrink-0 text-xs text-muted-foreground",
            hasMeta ? "mt-0.5" : "",
          )}
        >
          {formatAlwaysChars(alwaysChars)}
        </span>
      ) : null}
      {applyMode && onToggleApplyMode ? (
        <button
          type="button"
          title={
            disputed
              ? `${APPLY_LABEL[applyMode]} · 已停用，AI 不会用（点击切换生效方式）`
              : `${APPLY_LABEL[applyMode]} · ${APPLY_HINT[applyMode]}（点击切换）`
          }
          aria-label={`生效方式：${APPLY_LABEL[applyMode]}，点击切换`}
          onClick={onToggleApplyMode}
          className={cn("shrink-0 rounded-full", hasMeta ? "mt-0.5" : "")}
        >
          <Badge tone="muted" pill className="pointer-events-none font-normal">
            {APPLY_LABEL[applyMode]}
          </Badge>
        </button>
      ) : applyMode ? (
        <span className={cn("shrink-0", hasMeta ? "mt-0.5" : "")}>
          <Badge tone="muted" pill className="font-normal">
            {APPLY_LABEL[applyMode]}
          </Badge>
        </span>
      ) : null}
    </div>
  );
});
EntryLeafRow.displayName = "EntryLeafRow";
