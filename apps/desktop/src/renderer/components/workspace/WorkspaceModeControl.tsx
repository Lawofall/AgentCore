import { Button } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { WORKSPACE_BINDING_CHANGED } from "@/lib/bindLocalFolder";
import { hasLocalFiles } from "@/lib/capabilities";
import { notifyActionError } from "@/lib/toast";
import {
  type EffectiveWorkspace,
  formatWorkspaceChipLabel,
  resolveEffectiveWorkspace,
} from "@/lib/workspaceEffectiveMode";
import {
  exportCloudDeskToPickedFolder,
  exportCloudDeskZip,
  mergeArtifactsOnlyToLanding,
  mergeBackToLanding,
  peekMergeLanding,
  registerMergeLanding,
} from "@/services/cloudDeskExit";
import {
  type WorkspaceBinding,
  getWorkspaceBinding,
} from "@/services/workspaceBinding";
import {
  useBackgroundTasksStore,
  useHandoffArmed,
} from "@/stores/backgroundTasks";
import { useFoldersStore } from "@/stores/folders";
import type { FsRoot } from "@shared/ipc-contract";
import {
  AlertTriangle,
  ChevronDown,
  Cloud,
  CloudUpload,
  Download,
  FolderDown,
  FolderInput,
  GitBranch,
  HardDrive,
  Loader2,
  MapPin,
  Package,
  Upload,
} from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Shared workspace mode control — status for established chats (project inherit /
 * bare scratch). 出生定终身：不改当前会话 folder。§五：云会话不再主推打开本地 /
 * 绑定本机；遗留本机会话保留备份。云桌出口（ZIP / 合回落点）见 §7.6。
 */

export interface WorkspaceModeState {
  binding: WorkspaceBinding;
  roots: FsRoot[];
  effective: EffectiveWorkspace;
  refresh: () => Promise<void>;
}

export function useWorkspaceModeState(
  conversationId: string | null,
): WorkspaceModeState | null {
  const [binding, setBinding] = useState<WorkspaceBinding | null>(null);
  const [roots, setRoots] = useState<FsRoot[]>([]);
  const [containerRootId, setContainerRootId] = useState<string | null>(null);
  const [folderName, setFolderName] = useState<string | null>(null);
  // Track which conversation the in-memory binding belongs to. When the id
  // changes, clear synchronously during render so consumers never see the prior
  // session's effective.rootId (composer Git chip flash) before refresh resolves.
  const [boundConversationId, setBoundConversationId] =
    useState(conversationId);
  if (conversationId !== boundConversationId) {
    setBoundConversationId(conversationId);
    setBinding(null);
    setContainerRootId(null);
    setFolderName(null);
    setRoots([]);
  }

  // Guard in-flight refresh: a slow getWorkspaceBinding for conv A must not
  // write back after the user has already switched to conv B, and must not
  // setState after unmount (jsdom teardown then throws `window is not defined`).
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const fsApi = typeof window !== "undefined" ? window.fsApi : undefined;

  const loadRoots = useCallback(
    (): Promise<FsRoot[]> => fsApi?.listRoots() ?? Promise.resolve([]),
    [fsApi],
  );

  const refresh = useCallback(async () => {
    if (!conversationId || !mountedRef.current) return;
    const forId = conversationId;
    const conv = getConversations().find((c) => c.id === forId) ?? null;
    if (!mountedRef.current || conversationIdRef.current !== forId) return;
    setContainerRootId(conv?.localContainerRootId ?? null);
    const folder = conv?.folderId
      ? (getFolders().find((f) => f.id === conv.folderId) ?? null)
      : null;
    setFolderName(folder?.name ?? null);
    try {
      const [b, r] = await Promise.all([
        getWorkspaceBinding(forId, { fresh: true }),
        loadRoots(),
      ]);
      if (!mountedRef.current || conversationIdRef.current !== forId) return;
      setBinding(b);
      setRoots(r);
    } catch {
      if (!mountedRef.current || conversationIdRef.current !== forId) return;
      setBinding(null);
    }
  }, [conversationId, loadRoots]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!conversationId) return;
    const onChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ conversationId?: string }>).detail;
      if (detail?.conversationId === conversationId) void refresh();
    };
    window.addEventListener(WORKSPACE_BINDING_CHANGED, onChanged);
    return () =>
      window.removeEventListener(WORKSPACE_BINDING_CHANGED, onChanged);
  }, [conversationId, refresh]);

  const effective = useMemo(
    () =>
      resolveEffectiveWorkspace({
        binding,
        localContainerRootId: containerRootId,
        roots,
        folderName,
      }),
    [binding, containerRootId, roots, folderName],
  );

  if (!conversationId || !binding) return null;

  return {
    binding,
    roots,
    effective,
    refresh,
  };
}

/** Compact trigger used by the dock bar and composer chip. */
export function WorkspaceModeTrigger({
  effective,
  className = "",
  chevron = true,
}: {
  effective: EffectiveWorkspace;
  className?: string;
  chevron?: boolean;
}) {
  const { isLocal, rootMissing } = effective;
  const label = formatWorkspaceChipLabel(effective);
  return (
    <span
      className={`inline-flex min-w-0 items-center gap-1.5 overflow-hidden ${className}`}
    >
      {isLocal && rootMissing ? (
        <AlertTriangle size={13} className="shrink-0 text-muted-foreground" />
      ) : isLocal ? (
        <HardDrive size={13} className="shrink-0 text-primary" />
      ) : (
        <Cloud size={13} className="shrink-0 text-muted-foreground" />
      )}
      <span className="min-w-0 truncate">{label}</span>
      {chevron && (
        <ChevronDown size={12} className="shrink-0 text-muted-foreground" />
      )}
    </span>
  );
}

/** Status + local legacy handoff arm; cloud sessions: ZIP/合回出口 + import/Git（无 mode=local create）。 */
export function WorkspaceModeMenu({
  state,
  conversationId,
  onActionDone,
}: {
  state: WorkspaceModeState;
  conversationId?: string;
  onActionDone?: () => void;
}) {
  const { effective, roots, refresh } = state;
  const { isLocal, rootMissing, rootName, viaFolder, folderName } = effective;
  const desktop = hasLocalFiles();
  const [exitBusy, setExitBusy] = useState(false);
  const handoffArmed = useHandoffArmed(conversationId ?? null);
  const setHandoffArmed = useBackgroundTasksStore((s) => s.setHandoffArmed);

  const title = viaFolder
    ? folderName
      ? `文件夹 · ${folderName}`
      : "文件夹工作区"
    : isLocal
      ? "本机草稿"
      : "云端草稿";

  const subtitle = isLocal
    ? rootMissing
      ? "目录在本机不可用"
      : rootName
        ? viaFolder
          ? `本机路径 · ${rootName}`
          : `默认容器 · ${rootName}`
        : "本机草稿"
    : viaFolder
      ? "云端共享空间"
      : "云端对话";

  const landing =
    !isLocal && conversationId ? peekMergeLanding(conversationId, roots) : null;
  const landingHint =
    landing && !landing.missing
      ? `当前 · ${landing.rootName ?? "已登记目录"}`
      : landing?.missing
        ? "原目录已失效，请重新登记"
        : "首次合回时也会询问";

  const runExit = async (fn: () => Promise<unknown>) => {
    if (exitBusy) return;
    setExitBusy(true);
    try {
      await fn();
      await refresh();
    } finally {
      setExitBusy(false);
    }
  };

  const onRegisterLanding = () => {
    if (!conversationId) return;
    void runExit(async () => {
      const result = await registerMergeLanding(conversationId);
      if (!result.ok && result.reason === "error") {
        notifyActionError(
          "登记合回落点失败",
          new Error(result.message ?? "登记失败"),
        );
      }
    });
  };

  const onMergeBack = () => {
    if (!conversationId) return;
    onActionDone?.();
    void runExit(async () => {
      await mergeBackToLanding(conversationId, roots);
    });
  };

  const onMergeArtifactsOnly = () => {
    if (!conversationId) return;
    onActionDone?.();
    void runExit(async () => {
      await mergeArtifactsOnlyToLanding(conversationId, roots);
    });
  };

  const onExportZip = () => {
    if (!conversationId) return;
    onActionDone?.();
    void runExit(async () => {
      await exportCloudDeskZip(conversationId);
    });
  };

  const onExportToFolder = () => {
    if (!conversationId) return;
    onActionDone?.();
    void runExit(async () => {
      await exportCloudDeskToPickedFolder(conversationId);
    });
  };

  const importToCloud = () => {
    onActionDone?.();
    const prefillRootId = effective.rootId;
    useFoldersStore.getState().openImportToCloud(
      prefillRootId
        ? {
            rootId: prefillRootId,
            folderName: folderName ?? rootName,
          }
        : null,
    );
  };

  /** 云会话 → 当前 desk；遗留本机 → 新建云文件夹再 clone（不改绑本会话）。 */
  const connectGit = () => {
    let wsId: string | null = null;
    if (!isLocal && conversationId) {
      const conv = getConversations().find((c) => c.id === conversationId);
      wsId = conv?.folderId
        ? `folder:${conv.folderId}`
        : `conv:${conversationId}`;
    }
    onActionDone?.();
    useFoldersStore.getState().openConnectGit(wsId);
  };

  const anyBusy = exitBusy;

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <span
          className={`flex size-7 shrink-0 items-center justify-center rounded-lg ${
            isLocal
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {isLocal ? <HardDrive size={15} /> : <Cloud size={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-foreground">
            {title}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {subtitle}
          </div>
        </div>
      </div>

      <div className="p-1.5">
        {isLocal && !rootMissing ? (
          <>
            <ModeAction
              icon={<Upload size={14} />}
              label="导入到「我的文件」"
              hint="可选：新建云文件夹并导入"
              onClick={importToCloud}
              disabled={anyBusy}
            />
            {conversationId ? (
              <ModeAction
                icon={<CloudUpload size={14} />}
                label="遗留：先改云拷贝再合回"
                hint={
                  handoffArmed
                    ? "已开：下一条发送走云拷贝，完成后需点一下合回本机"
                    : "高级：发送后在云拷贝上改，不会直写本机；再点关闭"
                }
                onClick={() => {
                  setHandoffArmed(conversationId, !handoffArmed);
                }}
                disabled={anyBusy}
                pressed={handoffArmed}
                ariaLabel={
                  handoffArmed
                    ? "关闭遗留：先改云拷贝再合回"
                    : "开启遗留：先改云拷贝再合回"
                }
              />
            ) : null}
          </>
        ) : isLocal && rootMissing ? (
          <>
            <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
              目录在本机不可用。请导入到「我的文件」或重新绑定本机路径后再继续。
            </p>
            <ModeAction
              icon={<Upload size={14} />}
              label="导入到「我的文件」"
              hint="本机文件夹快照 → 新建云文件夹"
              onClick={importToCloud}
              disabled={anyBusy}
            />
            <ModeAction
              icon={<GitBranch size={14} />}
              label="从 Git 克隆"
              hint="新建云文件夹并浅克隆"
              onClick={connectGit}
              disabled={anyBusy}
            />
          </>
        ) : desktop ? (
          <>
            {/* §7.6 云桌→本机标准出口（常驻；不绑「后台云端」job） */}
            {conversationId ? (
              <>
                <ModeAction
                  icon={<Download size={14} />}
                  label="导出 ZIP"
                  hint="下载云端快照拷贝"
                  onClick={onExportZip}
                  disabled={anyBusy}
                />
                <ModeAction
                  icon={<FolderDown size={14} />}
                  label="导出到本机文件夹"
                  hint="每次可选目录；不必先登记落点"
                  onClick={onExportToFolder}
                  disabled={anyBusy}
                />
                <ModeAction
                  icon={<MapPin size={14} />}
                  label={
                    landing && !landing.missing
                      ? "更换合回落点"
                      : "登记合回落点"
                  }
                  hint={landingHint}
                  onClick={onRegisterLanding}
                  disabled={anyBusy}
                />
                <ModeAction
                  icon={<FolderInput size={14} />}
                  label="合回到本机"
                  hint="Diff 勾选写入落点；冲突默认保留本机"
                  onClick={onMergeBack}
                  disabled={anyBusy}
                />
                <ModeAction
                  icon={<Package size={14} />}
                  label="只合回产物"
                  hint="仅写入本回合交付产物；无则提示"
                  onClick={onMergeArtifactsOnly}
                  disabled={anyBusy}
                />
                <div className="my-1 border-t border-border" />
              </>
            ) : null}
            <ModeAction
              icon={<Upload size={14} />}
              label="导入到「我的文件」"
              hint="本机文件夹快照"
              onClick={importToCloud}
              disabled={anyBusy}
            />
            <ModeAction
              icon={<GitBranch size={14} />}
              label="从 Git 克隆"
              hint="云端浅克隆到本工作区"
              onClick={connectGit}
              disabled={anyBusy}
            />
            <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
              本会话工作区在创建时已确定，不可改绑。
            </p>
          </>
        ) : (
          <p className="px-2.5 py-1.5 text-xs text-muted-foreground">
            工作区在创建时已确定，会话期间不可改绑。
          </p>
        )}

        {exitBusy ? (
          <div className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            处理中…
          </div>
        ) : null}
      </div>
    </>
  );
}

function ModeAction({
  icon,
  label,
  hint,
  onClick,
  disabled,
  pressed,
  ariaLabel,
}: {
  icon: ReactNode;
  label: string;
  hint?: string;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
  ariaLabel?: string;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-pressed={pressed}
      className={`h-auto w-full justify-start gap-2 px-2.5 py-1.5 text-left text-xs font-medium ${
        pressed
          ? "bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary"
          : ""
      }`}
      icon={
        <span
          className={`shrink-0 ${pressed ? "text-primary" : "text-muted-foreground"}`}
        >
          {icon}
        </span>
      }
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate">{label}</span>
        {hint ? (
          <span
            className={`block truncate text-xs font-normal ${
              pressed ? "text-primary/80" : "text-muted-foreground"
            }`}
          >
            {hint}
          </span>
        ) : null}
      </span>
    </Button>
  );
}

/** Full control: trigger + shared popover (dock mode bar). */
export function WorkspaceModeControl({
  conversationId,
  triggerClassName,
}: {
  conversationId: string;
  triggerClassName?: string;
}) {
  const state = useWorkspaceModeState(conversationId);
  const [pop, setPop] = useState(false);

  if (!state) return null;

  return (
    <Popover open={pop} onOpenChange={setPop}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          className={
            triggerClassName ??
            `h-auto min-w-0 shrink gap-1.5 overflow-hidden px-2 py-1 text-xs font-medium ${
              state.effective.isLocal && state.effective.rootMissing
                ? "text-muted-foreground"
                : "text-foreground"
            }`
          }
        >
          <WorkspaceModeTrigger effective={state.effective} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        <WorkspaceModeMenu
          state={state}
          conversationId={conversationId}
          onActionDone={() => setPop(false)}
        />
      </PopoverContent>
    </Popover>
  );
}
