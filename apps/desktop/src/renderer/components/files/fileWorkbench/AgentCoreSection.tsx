import {
  EntriesSection,
  type EntryOpenTarget,
} from "@/components/files/fileWorkbench/EntriesSection";
import { createAndOpenScopeEntry } from "@/components/files/fileWorkbench/createScopeEntry";
import {
  loadAgentCoreExpanded,
  saveAgentCoreExpanded,
} from "@/components/files/fileWorkbench/storage";
import { IconButton } from "@/components/files/parts";
import { AGENTCORE_ROOT_LABEL } from "@/lib/stageDirs";
import {
  ChevronDown,
  ChevronRight,
  FilePlus,
  Folder,
  FolderOpen,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** Folder-layer convention tree. Account prompts live in the toolbox, not here. */
export type AgentCoreScope = { kind: "folder"; folderId: string };

/**
 * Folder-scope entries live inside the file tree's ``.agentcore`` row.
 */
export const ENTRIES_SECTION_NAME_FOLDER = AGENTCORE_ROOT_LABEL;

type AgentCoreSectionProps = {
  scope: AgentCoreScope;
  memoryActivePath: string | null;
  documentActivePath: string | null;
  onOpenEntry: (target: EntryOpenTarget) => void;
  onEntryDeleted: (target: EntryOpenTarget) => void;
  onEntryRenamed: (target: EntryOpenTarget, name: string) => void;
  indent?: number;
  /** Deep-link: expand AgentCore once. */
  forceOpen?: boolean;
  onRevealApplied?: () => void;
};

/**
 * Folder-layer entry rail — flat entries (目标形态 · 文件页形态).
 * No 记忆/规则/文档 subfolders; location is the 加载档 (root vs 主题夹).
 * 「新建条目」sits on the folder header so it stays available while the list is collapsed.
 *
 * Presentation only — folder entries still live under the `AgentCore` document root.
 * The files page has no account-prompt hub; this section is folder-scope only.
 */
export function AgentCoreSection({
  scope,
  memoryActivePath,
  documentActivePath,
  onOpenEntry,
  onEntryDeleted,
  onEntryRenamed,
  indent = 0,
  forceOpen = false,
  onRevealApplied,
}: AgentCoreSectionProps) {
  const foldKey = scope.folderId;
  const [sectionOpen, setSectionOpen] = useState(() =>
    loadAgentCoreExpanded().has(foldKey),
  );
  const revealAppliedRef = useRef(false);

  const persistOpen = (open: boolean) => {
    const set = loadAgentCoreExpanded();
    if (open) set.add(foldKey);
    else set.delete(foldKey);
    saveAgentCoreExpanded(set);
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
      const set = loadAgentCoreExpanded();
      set.add(foldKey);
      saveAgentCoreExpanded(set);
      return true;
    });
    onRevealApplied?.();
  }, [forceOpen, foldKey, onRevealApplied]);

  const toggleSection = () =>
    setSectionOpen((open) => {
      const next = !open;
      persistOpen(next);
      return next;
    });

  const entryScope = { kind: "folder" as const, folderId: scope.folderId };

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
          className="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-lg pr-2 text-left text-sm text-foreground transition-colors hover:bg-accent/60"
        >
          {sectionOpen ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )}
          {sectionOpen ? (
            <FolderOpen size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <Folder size={14} className="shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate">
            {ENTRIES_SECTION_NAME_FOLDER}
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
          indent={childIndent}
        />
      )}
    </div>
  );
}
