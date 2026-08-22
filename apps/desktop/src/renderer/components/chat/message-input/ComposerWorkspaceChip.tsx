import { CreateFolderCascadePanel } from "@/components/folders/CreateFolderMenu";
import { Button, SearchField } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  WorkspaceModeMenu,
  WorkspaceModeTrigger,
  useWorkspaceModeState,
} from "@/components/workspace/WorkspaceModeControl";
import { useGroupedConversations } from "@/hooks/useConversations";
import {
  notifyLocalPickerFailure,
  pickLocalFolderRoot,
} from "@/lib/bindLocalFolder";
import { isBorrowActive } from "@/lib/borrowOriginalPreference";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  getComposerChannelPreference,
  setComposerChannelPreference,
} from "@/lib/composerChannelPreference";
import { visibleDraftFolders } from "@/lib/draftWorkspaceFolders";
import { folderAncestorNames } from "@/lib/folderTree";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { openLocalFolderFromRoot } from "@/lib/openLocalFolder";
import { formatWorkspaceChipTitle } from "@/lib/workspaceEffectiveMode";
import {
  type FolderMeta,
  dedupeFoldersByLocalBinding,
} from "@/services/folders";
import { type DraftWorkspaceIntent, useFoldersStore } from "@/stores/folders";
import type { FsRoot } from "@shared/ipc-contract";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  Cloud,
  CloudUpload,
  FolderOpen,
  GitBranch,
  HardDrive,
  Loader2,
  Plus,
  Upload,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ComposerPlusBackHeader,
  useComposerPlusRow,
} from "./ComposerPlusMenu";
import { WorkspaceChannelGuideDialog } from "./WorkspaceChannelGuideDialog";

/**
 * Always-on「在哪工作」chip for the TurnComposer 底栏左簇（工作区首位）。
 * Draft first screen = pick a place (快速对话 + folders); join / local-use
 * nest 新建·Git·本机三选. Bound conversation: read-only status.
 */
export function ComposerWorkspaceChip({
  conversationId,
}: {
  conversationId: string | null;
}) {
  if (conversationId) {
    return <BoundChip conversationId={conversationId} />;
  }
  return <DraftChip />;
}

function BoundChip({ conversationId }: { conversationId: string }) {
  const plus = useComposerPlusRow("workspace");
  const state = useWorkspaceModeState(conversationId);
  const [pop, setPop] = useState(false);
  const grouped = useGroupedConversations().data;
  const folderId =
    grouped?.conversations
      ?.find((c) => c.id === conversationId)
      ?.folderId?.trim() ?? "";
  const borrowActive = folderId ? isBorrowActive(folderId) : false;

  if (plus.mode === "hidden") return null;

  if (!state) {
    return (
      <span className="inline-flex h-7 items-center gap-1 px-1.5 text-xs text-muted-foreground">
        <Loader2 size={12} className="animate-spin" />…
      </span>
    );
  }

  const boundTitle = formatWorkspaceChipTitle(state.effective);
  const title = borrowActive ? `${boundTitle} · 原件尚未改动` : boundTitle;

  const trigger = (
    <button
      type="button"
      aria-label={title}
      title={title}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className="inline-flex h-8 max-w-[280px] items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
      data-testid="composer-workspace-chip"
    >
      <WorkspaceModeTrigger effective={state.effective} className="text-xs" />
      {borrowActive ? (
        <span className="shrink-0 rounded-lg bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
          原件尚未改动
        </span>
      ) : null}
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="w-64">
        <ComposerPlusBackHeader title={title} onBack={plus.back} />
        <WorkspaceModeMenu
          state={state}
          conversationId={conversationId}
          onActionDone={plus.close}
        />
      </div>
    );
  }

  if (plus.mode === "row") {
    return trigger;
  }

  return (
    <div className="relative shrink-0">
      <Popover open={pop} onOpenChange={setPop}>
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          avoidCollisions={false}
          className="w-64 p-0"
        >
          <WorkspaceModeMenu
            state={state}
            conversationId={conversationId}
            onActionDone={() => setPop(false)}
          />
        </PopoverContent>
      </Popover>
    </div>
  );
}

function draftLabel(
  intent: DraftWorkspaceIntent,
  folders: FolderMeta[],
): { icon: "local" | "cloud" | "folder"; text: string } {
  if (intent.kind === "quick_cloud") {
    return { icon: "cloud", text: "快速对话" };
  }
  // Legacy intent（入口已砍；发送时改导云，不再造本机草稿）
  if (intent.kind === "quick_local") {
    return { icon: "local", text: "本机草稿" };
  }
  const folder = folders.find((f) => f.id === intent.folderId);
  if (!folder) return { icon: "folder", text: "文件夹" };
  return {
    icon: folder.mode === "local" ? "local" : "cloud",
    text: folder.name,
  };
}

/** Channel lives on the row icon; hint is ancestor path only, or nothing. */
function folderLocationHint(f: FolderMeta): string | undefined {
  if (f.mode === "cloud") {
    const ancestors = folderAncestorNames(f);
    return ancestors.length > 0 ? ancestors.join(" / ") : undefined;
  }
  // Same rule as the cloud branch: the hint says what sits *above* the folder.
  // Keeping the trailing segment when it repeats the folder's own name
  // （白板 bound at …/白板）just renders「白板」under「白板」.
  const above = (f.localSubpath ?? "").split("/").filter(Boolean);
  if (above.at(-1) === f.name) above.pop();
  return above.length > 0 ? above.join("/") : undefined;
}

function FolderChannelIcon({ mode }: { mode: FolderMeta["mode"] }) {
  const title = mode === "local" ? "本机文件夹" : "我的文件";
  return (
    <span title={title} aria-hidden>
      {mode === "local" ? <HardDrive size={14} /> : <Cloud size={14} />}
    </span>
  );
}

type DraftView = "pick" | "join" | "create" | "local-use";
type LocalPicked = {
  root: FsRoot;
  owns: boolean;
  /** List item already in 本机文件夹 — 直接改只改草稿意图，不新开会话。 */
  existingFolderId?: string;
  backView: "pick" | "join";
};

function DraftChip() {
  const plus = useComposerPlusRow("workspace");
  const navigate = useNavigate();
  const [pop, setPop] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [foldersExpanded, setFoldersExpanded] = useState(false);
  /** Same popover handoff — avoid close→open race that swallows CreateFolderMenu. */
  const [view, setView] = useState<DraftView>("pick");
  const [pickingLocal, setPickingLocal] = useState(false);
  const [localPicked, setLocalPicked] = useState<LocalPicked | null>(null);
  const localPickedRef = useRef<LocalPicked | null>(null);
  localPickedRef.current = localPicked;
  const intent = useFoldersStore((s) => s.draftWorkspaceIntent);
  const setIntent = useFoldersStore((s) => s.setDraftWorkspaceIntent);
  const isDesktop = hasLocalFiles();
  const { isNarrow } = useNarrowLayoutState();
  const lastChannel = getComposerChannelPreference();
  const lastWasLocal = lastChannel === "local_traditional";

  const grouped = useGroupedConversations().data;
  const folders = useMemo(() => {
    const list = dedupeFoldersByLocalBinding(grouped?.folders ?? []);
    return isDesktop ? list : list.filter((f) => f.mode === "cloud");
  }, [grouped?.folders, isDesktop]);

  const selectedFolderId = intent.kind === "folder" ? intent.folderId : null;

  const {
    visible: folderRows,
    matchCount,
    canExpand,
    hiddenCount,
  } = useMemo(
    () =>
      visibleDraftFolders({
        folders,
        conversations: grouped?.conversations ?? [],
        query,
        expanded: foldersExpanded,
        selectedFolderId,
      }),
    [folders, grouped?.conversations, query, foldersExpanded, selectedFolderId],
  );

  const { icon, text } = draftLabel(intent, folders);
  const borrowActiveDraft =
    intent.kind === "folder" && isBorrowActive(intent.folderId);
  const draftTitle = borrowActiveDraft ? `${text} · 原件尚未改动` : text;

  const dropOwnedLocal = () => {
    const picked = localPickedRef.current;
    if (picked?.owns) {
      void window.fsApi?.removeRoot?.(picked.root.id);
    }
    localPickedRef.current = null;
    setLocalPicked(null);
  };

  const resetPickChrome = () => {
    setQuery("");
    setFoldersExpanded(false);
    setView("pick");
    dropOwnedLocal();
  };

  const closePick = () => {
    setPop(false);
    resetPickChrome();
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
  };

  const handoffLocalPicked = (): LocalPicked | null => {
    const picked = localPickedRef.current;
    if (!picked) return null;
    const released = { ...picked, owns: false };
    localPickedRef.current = released;
    setLocalPicked(released);
    return picked;
  };

  const pickQuickCloud = () => {
    setComposerChannelPreference("cloud");
    setIntent({ kind: "quick_cloud" });
    closePick();
  };

  const pickFolder = (folder: FolderMeta) => {
    if (folder.mode === "local" && folder.localRootId) {
      const next: LocalPicked = {
        root: { id: folder.localRootId, name: folder.name },
        owns: false,
        existingFolderId: folder.id,
        backView: "pick",
      };
      localPickedRef.current = next;
      setLocalPicked(next);
      setView("local-use");
      return;
    }
    setComposerChannelPreference("cloud");
    setIntent({ kind: "folder", folderId: folder.id });
    closePick();
  };

  const connectGit = () => {
    setComposerChannelPreference("cloud");
    closePick();
    useFoldersStore.getState().openConnectGit();
  };

  const joinFromLocal = async () => {
    setPickingLocal(true);
    try {
      const picked = await pickLocalFolderRoot();
      if (!picked.ok) {
        if (picked.reason !== "cancelled") {
          notifyLocalPickerFailure(picked.reason, picked.message);
        }
        return;
      }
      const next: LocalPicked = {
        root: picked.root,
        owns: true,
        backView: "join",
      };
      localPickedRef.current = next;
      setLocalPicked(next);
      setView("local-use");
    } finally {
      setPickingLocal(false);
    }
  };

  const useLocalDirect = () => {
    const picked = handoffLocalPicked();
    if (!picked) return;
    setComposerChannelPreference("local_traditional");
    if (picked.existingFolderId) {
      setIntent({ kind: "folder", folderId: picked.existingFolderId });
      closePick();
      return;
    }
    closePick();
    void openLocalFolderFromRoot(picked.root, navigate);
  };

  const useLocalImport = () => {
    const picked = handoffLocalPicked();
    if (!picked) return;
    setComposerChannelPreference("cloud");
    closePick();
    useFoldersStore.getState().openImportToCloud({
      rootId: picked.root.id,
      folderName: picked.root.name,
      ownsRoot: picked.owns,
    });
  };

  const useLocalBorrow = () => {
    const picked = handoffLocalPicked();
    if (!picked) return;
    setComposerChannelPreference("cloud");
    closePick();
    useFoldersStore.getState().openBorrowToCloud({
      rootId: picked.root.id,
      folderName: picked.root.name,
      ownsRoot: picked.owns,
    });
  };

  const openCreateCloud = () => {
    setComposerChannelPreference("cloud");
    setView("create");
  };

  const openGuide = () => {
    setPop(false);
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    setGuideOpen(true);
  };

  const holdOpen =
    plus.mode === "panel" || plus.mode === "row" ? plus.setHoldOpen : null;
  useEffect(() => {
    if (!holdOpen) return;
    holdOpen(pickingLocal);
    return () => holdOpen(false);
  }, [pickingLocal, holdOpen]);

  const preventDismissWhilePicking = (e: Event) => {
    if (pickingLocal) e.preventDefault();
  };

  const folderList = (
    <>
      <div className="mx-2.5 mb-1 pt-1">
        <p className="mb-1 text-xs text-muted-foreground">文件夹</p>
        <SearchField
          value={query}
          onValueChange={setQuery}
          placeholder="筛选…"
          aria-label="筛选文件夹"
          className="w-full"
          inputClassName="text-xs"
        />
      </div>
      {folderRows.map((f) => (
        <DraftRow
          key={f.id}
          icon={<FolderChannelIcon mode={f.mode} />}
          label={f.name}
          hint={folderLocationHint(f)}
          selected={intent.kind === "folder" && intent.folderId === f.id}
          onClick={() => pickFolder(f)}
        />
      ))}
      {matchCount === 0 && (
        <p className="px-2.5 py-2 text-xs text-muted-foreground">
          {query.trim() ? "没有匹配的文件夹" : "还没有文件夹"}
        </p>
      )}
      {canExpand && !foldersExpanded && hiddenCount > 0 ? (
        <DraftRow
          icon={<ChevronDown size={14} />}
          label={`查看全部（${matchCount}）`}
          onClick={() => setFoldersExpanded(true)}
        />
      ) : null}
      {canExpand && foldersExpanded ? (
        <DraftRow
          icon={<ChevronUp size={14} />}
          label="收起"
          onClick={() => setFoldersExpanded(false)}
        />
      ) : null}
    </>
  );

  const guide = (
    <WorkspaceChannelGuideDialog
      open={guideOpen}
      onOpenChange={setGuideOpen}
      showLocalTraditional={isDesktop}
    />
  );

  const trigger = (
    <button
      type="button"
      aria-label="在哪工作"
      title={draftTitle}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className="inline-flex h-8 max-w-[280px] items-center gap-1 rounded-lg px-2 text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground"
    >
      {icon === "cloud" ? (
        <Cloud size={13} className="shrink-0" />
      ) : icon === "local" ? (
        <HardDrive size={13} className="shrink-0" />
      ) : (
        <FolderOpen size={13} className="shrink-0" />
      )}
      <span className="min-w-0 truncate">{text}</span>
      {borrowActiveDraft ? (
        <span className="shrink-0 rounded-lg bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
          原件尚未改动
        </span>
      ) : null}
    </button>
  );

  const body =
    view === "create" ? (
      <div>
        <NestedHeader
          title="新建文件夹"
          onBack={() => setView(isDesktop ? "join" : "pick")}
        />
        <CreateFolderCascadePanel onClose={closePick} hideTitle />
      </div>
    ) : view === "join" ? (
      <div>
        <NestedHeader title="新建或加入" onBack={() => setView("pick")} />
        <div className="p-1.5">
          <DraftRow
            icon={<Plus size={14} />}
            label="新建文件夹"
            onClick={openCreateCloud}
          />
          <DraftRow
            icon={<GitBranch size={14} />}
            label="从 Git 克隆"
            onClick={connectGit}
          />
          <DraftRow
            icon={
              pickingLocal ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <HardDrive size={14} />
              )
            }
            label="从本机加入"
            onClick={() => void joinFromLocal()}
          />
        </div>
      </div>
    ) : view === "local-use" ? (
      <div>
        <NestedHeader
          title={localPicked?.root.name ?? "本机文件夹"}
          onBack={() => {
            const back = localPickedRef.current?.backView ?? "join";
            dropOwnedLocal();
            setView(back);
          }}
        />
        <div className="p-1.5">
          <DraftRow
            icon={<HardDrive size={14} />}
            label="直接改这个文件夹"
            hint="立刻改电脑上的原件"
            badge={lastWasLocal ? "上次" : undefined}
            onClick={useLocalDirect}
          />
          <DraftRow
            icon={<Upload size={14} />}
            label="复制到云上当新家"
            hint="之后改云上这份，原件不再跟着变"
            onClick={useLocalImport}
          />
          <DraftRow
            icon={<CloudUpload size={14} />}
            label="先在云上做，原件先不动"
            hint="做完再决定写不写回"
            onClick={useLocalBorrow}
          />
        </div>
      </div>
    ) : (
      <div className="max-h-[360px] overflow-y-auto p-1.5">
        <DraftRow
          icon={<Cloud size={14} />}
          label="快速对话"
          selected={intent.kind === "quick_cloud"}
          onClick={pickQuickCloud}
        />
        {folderList}
        <div className="my-1 border-t border-border" />
        {isDesktop ? (
          <DraftRow
            icon={<Plus size={14} />}
            label="新建或加入…"
            onClick={() => setView("join")}
          />
        ) : !isNarrow ? (
          <DraftRow
            icon={<Plus size={14} />}
            label="新建文件夹"
            onClick={openCreateCloud}
          />
        ) : null}
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-accent/40 hover:text-foreground"
          onClick={openGuide}
        >
          <span className="w-4 shrink-0" aria-hidden />
          了解区别
        </button>
      </div>
    );

  if (plus.mode === "hidden") return guide;

  if (plus.mode === "panel") {
    return (
      <div className="w-64">
        {guide}
        {view === "pick" ? (
          <ComposerPlusBackHeader
            title="在哪工作"
            onBack={() => {
              resetPickChrome();
              plus.back();
            }}
          />
        ) : null}
        {body}
      </div>
    );
  }

  if (plus.mode === "row") {
    return (
      <>
        {guide}
        {trigger}
      </>
    );
  }

  return (
    <div className="relative shrink-0">
      {guide}
      <Popover
        open={pop}
        onOpenChange={(o) => {
          if (!o && pickingLocal) return;
          setPop(o);
          if (!o) resetPickChrome();
        }}
      >
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          // Keep side when switching pick→create (taller cascade); flip feels like a jump.
          avoidCollisions={false}
          className="w-64 p-0"
          onCloseAutoFocus={(e) => e.preventDefault()}
          onPointerDownOutside={preventDismissWhilePicking}
          onFocusOutside={preventDismissWhilePicking}
          onInteractOutside={preventDismissWhilePicking}
        >
          {body}
        </PopoverContent>
      </Popover>
    </div>
  );
}

function NestedHeader({
  title,
  onBack,
}: {
  title: string;
  onBack: () => void;
}) {
  return (
    <div className="border-b border-border">
      <Button
        variant="ghost"
        aria-label="返回"
        className="h-auto w-full justify-start gap-2 rounded-none px-4 py-1.5 text-left text-xs font-medium text-muted-foreground"
        icon={
          <span className="flex w-4 shrink-0 justify-center">
            <ChevronLeft size={14} />
          </span>
        }
        onClick={onBack}
      >
        <span className="min-w-0 truncate text-foreground">{title}</span>
      </Button>
    </div>
  );
}

/**
 * Pick-view rows. First screen keeps a single separator (文件夹列表 ↔ 新建或加入).
 */
function DraftRow({
  icon,
  label,
  hint,
  badge,
  selected,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  hint?: string;
  badge?: string;
  selected?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className="h-auto w-full justify-start gap-2 px-2.5 py-1.5 text-left text-xs font-medium"
      icon={
        <span className="flex w-4 shrink-0 justify-center text-muted-foreground">
          {icon}
        </span>
      }
    >
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate">{label}</span>
          {badge ? (
            <span className="shrink-0 rounded-lg bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
              {badge}
            </span>
          ) : null}
        </span>
        {hint && (
          <span className="block truncate text-xs font-normal text-muted-foreground">
            {hint}
          </span>
        )}
      </span>
      {selected && <Check size={14} className="shrink-0 text-primary" />}
    </Button>
  );
}
