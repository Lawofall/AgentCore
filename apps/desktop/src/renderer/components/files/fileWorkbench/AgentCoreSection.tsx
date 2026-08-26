import { BrandMarkIcon } from "@/components/brand/BrandMark";
import {
  EntriesSection,
  type EntryOpenTarget,
} from "@/components/files/fileWorkbench/EntriesSection";
import { createAndOpenScopeEntry } from "@/components/files/fileWorkbench/createScopeEntry";
import {
  loadAgentCoreCollapsed,
  loadAgentCoreExpanded,
  saveAgentCoreCollapsed,
  saveAgentCoreExpanded,
} from "@/components/files/fileWorkbench/storage";
import { IconButton } from "@/components/files/parts";
import { AGENTCORE_ROOT_LABEL } from "@/lib/stageDirs";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  FilePlus,
  Folder,
  FolderOpen,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** Which convention-tree layer: GLOBAL (cloud root) or one folder's. */
export type AgentCoreScope =
  | { kind: "global" }
  | { kind: "folder"; folderId: string };

/**
 * Entry-base rail titles by mount. Folder-scope entries live inside the file
 * tree's ``.agentcore`` row; this section is the global rail (plus leftover
 * folder title if mounted).
 */
export const ENTRIES_SECTION_NAME_GLOBAL = "全局设定";
export const ENTRIES_SECTION_NAME_FOLDER = AGENTCORE_ROOT_LABEL;

export function entriesSectionName(scope: AgentCoreScope): string {
  return scope.kind === "global"
    ? ENTRIES_SECTION_NAME_GLOBAL
    : ENTRIES_SECTION_NAME_FOLDER;
}

/**
 * Entry-base rail section — flat entries by scope (目标形态 · 文件页形态).
 * No 记忆/规则/文档 subfolders; 常驻/按需 badges live in {@link EntriesSection}.
 * 「新建条目」sits on this header so it stays available while the list is collapsed.
 *
 * Presentation only — entries still live under the `AgentCore` document root.
 */
export function AgentCoreSection({
  scope,
  memoryActivePath,
  documentActivePath,
  onOpenEntry,
  onEntryDeleted,
  onEntryRenamed,
  onOpenUpdates,
  indent = 0,
  forceOpen = false,
  onRevealApplied,
}: {
  scope: AgentCoreScope;
  memoryActivePath: string | null;
  documentActivePath: string | null;
  onOpenEntry: (target: EntryOpenTarget) => void;
  onEntryDeleted: (target: EntryOpenTarget) => void;
  onEntryRenamed: (target: EntryOpenTarget, name: string) => void;
  /** GLOBAL-only「最近更新」feed opener. */
  onOpenUpdates?: () => void;
  indent?: number;
  /** Deep-link: expand AgentCore once. */
  forceOpen?: boolean;
  onRevealApplied?: () => void;
}) {
  const foldKey = scope.kind === "global" ? "global" : scope.folderId;
  const [sectionOpen, setSectionOpen] = useState(() =>
    scope.kind === "global"
      ? !loadAgentCoreCollapsed().has(foldKey)
      : loadAgentCoreExpanded().has(foldKey),
  );
  const revealAppliedRef = useRef(false);

  const persistOpen = (open: boolean) => {
    if (scope.kind === "global") {
      const set = loadAgentCoreCollapsed();
      if (open) set.delete(foldKey);
      else set.add(foldKey);
      saveAgentCoreCollapsed(set);
    } else {
      const set = loadAgentCoreExpanded();
      if (open) set.add(foldKey);
      else set.delete(foldKey);
      saveAgentCoreExpanded(set);
    }
  };

  const ensureOpen = () => {
    setSectionOpen((open) => {
      if (open) return open;
      persistOpen(true);
      return true;
    });
  };

  useEffect(() => {
    if (!forceOpen) {
      revealAppliedRef.current = false;
      return;
    }
    if (revealAppliedRef.current) return;
    revealAppliedRef.current = true;

    setSectionOpen((open) => {
      if (open) return open;
      if (scope.kind === "global") {
        const set = loadAgentCoreCollapsed();
        set.delete(foldKey);
        saveAgentCoreCollapsed(set);
      } else {
        const set = loadAgentCoreExpanded();
        set.add(foldKey);
        saveAgentCoreExpanded(set);
      }
      return true;
    });
    onRevealApplied?.();
  }, [forceOpen, scope.kind, foldKey, onRevealApplied]);

  const toggleSection = () =>
    setSectionOpen((open) => {
      const next = !open;
      persistOpen(next);
      return next;
    });

  const entryScope =
    scope.kind === "global"
      ? ({ kind: "global" } as const)
      : ({ kind: "folder", folderId: scope.folderId } as const);

  const createEntry = async () => {
    const ok = await createAndOpenScopeEntry(entryScope, onOpenEntry);
    if (ok) ensureOpen();
  };

  const headerPad = indent + 8;
  const childIndent = indent + 14;

  return (
    <div>
      <div className="flex items-center rounded-lg pr-1">
        <button
          type="button"
          onClick={toggleSection}
          aria-expanded={sectionOpen}
          style={{ paddingLeft: headerPad }}
          className={cn(
            "flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-lg pr-2 text-left text-sm text-foreground transition-colors hover:bg-accent/60",
            scope.kind === "global" && "font-medium",
          )}
        >
          {sectionOpen ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )}
          {scope.kind === "global" ? (
            <BrandMarkIcon size={14} />
          ) : sectionOpen ? (
            <FolderOpen size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <Folder size={14} className="shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate">
            {entriesSectionName(scope)}
          </span>
        </button>
        <IconButton title="新建条目" onClick={() => void createEntry()}>
          <FilePlus size={14} />
        </IconButton>
      </div>

      {sectionOpen && (
        <EntriesSection
          scope={entryScope}
          memoryActivePath={memoryActivePath}
          documentActivePath={documentActivePath}
          onOpen={onOpenEntry}
          onDeleted={onEntryDeleted}
          onRenamed={onEntryRenamed}
          onOpenUpdates={onOpenUpdates}
          indent={childIndent}
        />
      )}
    </div>
  );
}
