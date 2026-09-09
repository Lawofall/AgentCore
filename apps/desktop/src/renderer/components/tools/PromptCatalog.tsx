import { MemoryUpdatesView } from "@/components/files/MemoryUpdatesView";
import { EntryLeafRow } from "@/components/files/fileWorkbench/EntriesSection";
import { RailSectionHeader } from "@/components/files/fileWorkbench/RailHeaders";
import { IconButton } from "@/components/files/parts";
import { PromptWorkbench } from "@/components/prompt/PromptWorkbench";
import { RoleIdentityBlock } from "@/components/tools/RoleIdentityBlock";
import { Badge, Button } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  type AccountScopeEntry,
  DEFAULT_PROMPT_CATALOG_ID,
  OTHER_FOLDER_NAME,
  type PromptCatalogItem,
  type PromptCatalogLocationState,
  type PromptRailFolder,
  buildMineCatalogRows,
  buildPromptRail,
  catalogIdForMemoryTarget,
  flattenPromptRail,
  mineCatalogId,
  onDemandDropFolder,
} from "@/lib/promptCatalog";
import {
  PROMPT_DRAG_MIME,
  isPromptDrag,
  parsePromptDragPayload,
  promptDragPayload,
} from "@/lib/promptCatalogDrag";
import { cn } from "@/lib/utils";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import type { Capabilities } from "@/services/capabilities";
import {
  createRuleDocument,
  createRuleFolder,
  deleteDocument,
  getDocument,
  listAccountPromptTree,
  listScopeEntries,
  renameDocument,
  reparentDocument,
  setDocumentDisputed,
  writeDocument,
} from "@/services/documents";
import { getMemoryFile, writeMemoryFile } from "@/services/memory";
import {
  EMPTY_SKILL_CATALOG,
  type SkillCatalog,
  composeOnDemandSkillContent,
  composeSkillContent,
  getSkillCatalog,
  skillBodyFromContent,
  skillFileName,
} from "@/services/skillCatalog";
import {
  type SkillStoreListing,
  listInstalledSkills,
  listMySkillListings,
  publishSkill,
  publishSkillVersion,
  unpublishSkill,
} from "@/services/skillStore";
import {
  filesMemoryLeafNavState,
  isAccountMemoryTarget,
} from "@/services/sources/memorySource";
import {
  ChevronDown,
  ChevronRight,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  History,
  Pencil,
  Trash2,
  Undo2,
} from "lucide-react";
import {
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

function overlayErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.serverMessage?.trim()) return err.serverMessage;
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        return parsed.detail;
      }
    } catch {
      /* keep falling through */
    }
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return "没保存成功";
}

type CatalogDropDest =
  | { kind: "root" }
  | { kind: "folder"; folder: PromptRailFolder };

function hasPromptDrag(event: DragEvent): boolean {
  return isPromptDrag(Array.from(event.dataTransfer.types));
}

function promptDragTypes(event: DragEvent): string[] {
  return Array.from(event.dataTransfer.types);
}

function readPromptDrag(event: DragEvent) {
  return parsePromptDragPayload(event.dataTransfer.getData(PROMPT_DRAG_MIME));
}

function toScopeEntry(
  doc: Awaited<ReturnType<typeof listScopeEntries>>[number],
): AccountScopeEntry {
  return {
    id: doc.id,
    name: doc.name,
    description: doc.description,
    applyMode: doc.applyMode,
    aiMaintained: doc.aiMaintained,
    disputedAt: doc.disputedAt,
    parentId: doc.parentId,
  };
}

/** Left TOC + right reader for the 工具箱「提示词」page (account layer only). */
export function PromptCatalog({ data }: { data: Capabilities }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const updatesOpen = searchParams.get("updates") === "1";
  const [overlay, setOverlay] = useState<SkillCatalog>(EMPTY_SKILL_CATALOG);
  const [accountEntries, setAccountEntries] = useState<AccountScopeEntry[]>([]);
  const [listings, setListings] = useState<SkillStoreListing[]>([]);
  const [installedCopyIds, setInstalledCopyIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accountReady, setAccountReady] = useState(false);
  const [pendingLeaf, setPendingLeaf] = useState<string | null>(null);
  const [rulesDirId, setRulesDirId] = useState<string | null>(null);
  const [promptFolders, setPromptFolders] = useState<
    { id: string; name: string }[]
  >([]);
  const [createFolderId, setCreateFolderId] = useState<string | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const [dropDest, setDropDest] = useState<CatalogDropDest | null>(null);

  const setUpdatesOpen = useCallback(
    (open: boolean) => {
      const params = new URLSearchParams(searchParams);
      if (open) params.set("updates", "1");
      else params.delete("updates");
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const mineRows = useMemo(
    () => buildMineCatalogRows(overlay.mine, accountEntries),
    [overlay.mine, accountEntries],
  );
  const rail = useMemo(
    () => buildPromptRail(data, mineRows, promptFolders, rulesDirId),
    [data, mineRows, promptFolders, rulesDirId],
  );
  const items = useMemo(() => flattenPromptRail(rail), [rail]);
  const otherDrop = useMemo(() => onDemandDropFolder(rail), [rail]);
  const fallbackId =
    items.find((item) => item.id === DEFAULT_PROMPT_CATALOG_ID)?.id ??
    items[0]?.id ??
    null;
  const [selectedId, setSelectedId] = useState<string | null>(fallbackId);
  const selected =
    items.find((item) => item.id === selectedId) ??
    items.find((item) => item.id === fallbackId) ??
    items[0];

  const loadAccountLayer = useCallback(async (): Promise<SkillCatalog> => {
    const [catalog, entries, tree] = await Promise.all([
      getSkillCatalog(null),
      listScopeEntries(null).catch(() => []),
      listAccountPromptTree().catch(() => ({
        rulesDirId: null,
        folders: [] as { id: string; name: string }[],
        documents: [],
      })),
    ]);
    setAccountEntries(entries.map(toScopeEntry));
    setRulesDirId(tree.rulesDirId);
    setPromptFolders(
      tree.folders.map((folder) => ({ id: folder.id, name: folder.name })),
    );
    return catalog;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadAccountLayer()
      .then((catalog) => {
        if (!cancelled) setOverlay(catalog);
      })
      .catch(() => {
        if (!cancelled) {
          setOverlay(EMPTY_SKILL_CATALOG);
          setAccountEntries([]);
        }
      })
      .finally(() => {
        if (!cancelled) setAccountReady(true);
      });
    void Promise.all([listMySkillListings(), listInstalledSkills()])
      .then(([mine, installed]) => {
        if (cancelled) return;
        setListings(mine);
        setInstalledCopyIds(
          new Set(
            installed
              .map((row) => row.installDocumentId)
              .filter((id): id is string => Boolean(id)),
          ),
        );
      })
      .catch(() => {
        if (cancelled) return;
        setListings([]);
        setInstalledCopyIds(new Set());
      });
    return () => {
      cancelled = true;
    };
  }, [loadAccountLayer]);

  useEffect(() => {
    const leaf = (location.state as PromptCatalogLocationState | null)
      ?.openMineLeaf;
    if (!leaf) return;
    setPendingLeaf(leaf);
    navigate(
      { pathname: location.pathname, search: location.search },
      { replace: true, state: {} },
    );
  }, [location.state, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (!pendingLeaf || !accountReady) return;
    const id = catalogIdForMemoryTarget(pendingLeaf, items);
    if (id) setSelectedId(id);
    setPendingLeaf(null);
    if (updatesOpen) setUpdatesOpen(false);
  }, [pendingLeaf, accountReady, items, updatesOpen, setUpdatesOpen]);

  async function persist(
    action: () => Promise<SkillCatalog | undefined>,
    opts?: { lock?: boolean },
  ): Promise<boolean> {
    const lock = opts?.lock !== false;
    if (lock) setBusy(true);
    setError(null);
    try {
      const next = await action();
      if (next) {
        setOverlay(next);
        const [entries, tree] = await Promise.all([
          listScopeEntries(null).catch(() => []),
          listAccountPromptTree().catch(() => ({
            rulesDirId: null,
            folders: [] as { id: string; name: string }[],
            documents: [],
          })),
        ]);
        setAccountEntries(entries.map(toScopeEntry));
        setRulesDirId(tree.rulesDirId);
        setPromptFolders(
          tree.folders.map((folder) => ({ id: folder.id, name: folder.name })),
        );
      } else {
        setOverlay(await loadAccountLayer());
      }
      return true;
    } catch (err) {
      setError(overlayErrorMessage(err));
      return false;
    } finally {
      if (lock) setBusy(false);
    }
  }

  async function ensureNamedFolder(name: string): Promise<string> {
    const existing = promptFolders.find((folder) => folder.name === name);
    if (existing) return existing.id;
    const created = await createRuleFolder(name);
    return created.id;
  }

  async function onCreateMine() {
    await persist(async () => {
      const parentId =
        createFolderId ?? (await ensureNamedFolder(OTHER_FOLDER_NAME));
      const created = await createRuleDocument(
        skillFileName("未命名提示词"),
        null,
        composeOnDemandSkillContent("", ""),
        "on_demand",
        parentId,
      );
      const catalog = await loadAccountLayer();
      setSelectedId(mineCatalogId(created.id));
      setUpdatesOpen(false);
      return catalog;
    });
  }

  async function onCreateFolder() {
    const name = window.prompt("夹名称")?.trim();
    if (!name) return;
    await persist(async () => {
      const created = await createRuleFolder(name);
      setCreateFolderId(created.id);
      setOpenFolders((prev) => new Set(prev).add(`folder:${created.id}`));
      return undefined;
    });
  }

  async function moveMineItem(
    item: PromptCatalogItem,
    dest: PromptRailFolder | "root",
  ) {
    if (item.kind !== "mine" || !item.mineId || item.memoryKind) return;
    if (dest === "root") {
      if (item.applyMode === "always") return;
    } else if (dest.documentId && item.parentId === dest.documentId) {
      return;
    }
    await persist(async () => {
      if (dest === "root") {
        let parent = rulesDirId;
        if (!parent) {
          await createRuleFolder(OTHER_FOLDER_NAME);
          parent = (await listAccountPromptTree()).rulesDirId;
        }
        if (!parent) return undefined;
        await reparentDocument(item.mineId, parent, "always");
        return undefined;
      }
      let folderId = dest.documentId;
      if (!folderId) folderId = await ensureNamedFolder(dest.name);
      await reparentDocument(item.mineId, folderId, "on_demand");
      return undefined;
    });
  }

  function acceptPromptDrag(event: DragEvent, dest: CatalogDropDest) {
    const types = promptDragTypes(event);
    if (!isPromptDrag(types)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    setDropDest(dest);
  }

  function dropPromptFile(event: DragEvent, dest: PromptRailFolder | "root") {
    event.preventDefault();
    event.stopPropagation();
    setDropDest(null);
    const payload = readPromptDrag(event);
    if (!payload || payload.kind === "skill") return;
    const item = items.find(
      (row) => row.kind === "mine" && row.mineId === payload.mineId,
    );
    if (!item) return;
    void moveMineItem(item, dest);
  }

  async function renameMineItem(item: PromptCatalogItem) {
    if (item.kind !== "mine" || !item.mineId || item.aiMaintained) return;
    const input = window.prompt("条目名称", item.label);
    if (input == null) return;
    const name = skillFileName(input.trim());
    if (name === ".md" || name === skillFileName(item.label)) return;
    await persist(async () => {
      await renameDocument(item.mineId, name);
      return undefined;
    });
  }

  async function restoreMineItem(item: PromptCatalogItem) {
    if (item.kind !== "mine" || !item.mineId) return;
    await persist(async () => {
      await setDocumentDisputed(item.mineId, false);
      return undefined;
    });
  }

  async function deleteMineItem(item: PromptCatalogItem) {
    if (item.kind !== "mine" || !item.mineId || item.memoryKind) return;
    if (!window.confirm(`确定删除「${item.label}」？此操作不可撤销。`)) return;
    await persist(async () => {
      await deleteDocument(item.mineId);
      setSelectedId(fallbackId);
      return undefined;
    });
  }

  if (selected == null) return null;

  const railFile = (
    item: PromptCatalogItem,
    paddingLeft: number,
    folderId: string | null,
  ) => (
    <PromptRailFile
      key={item.id}
      item={item}
      selectedId={selected.id}
      updatesOpen={updatesOpen}
      paddingLeft={paddingLeft}
      onOpen={() => {
        setSelectedId(item.id);
        setCreateFolderId(folderId);
        if (updatesOpen) setUpdatesOpen(false);
      }}
      onDragEnd={() => setDropDest(null)}
      onRename={() => void renameMineItem(item)}
      onRestore={() => void restoreMineItem(item)}
      onDelete={() => void deleteMineItem(item)}
    />
  );

  return (
    <div
      className="flex min-h-0 flex-1 overflow-hidden"
      data-testid="prompt-catalog"
    >
      <nav
        aria-label="提示词目录"
        className="w-56 shrink-0 overflow-y-auto border-r border-border bg-muted/30 px-1 py-2"
        onDragOver={(event) => {
          if (!hasPromptDrag(event)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "none";
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDropDest(null);
        }}
      >
        {dropDest ? (
          <p
            className="pointer-events-none sticky top-0 z-10 mb-1 rounded-lg bg-accent px-2 py-1 text-xs text-foreground"
            aria-live="polite"
          >
            {dropDest.kind === "root"
              ? "将放到常驻"
              : `将放入${dropDest.folder.name}`}
          </p>
        ) : null}
        <div
          onDragOver={(event) => {
            if (!hasPromptDrag(event)) return;
            event.stopPropagation();
            event.dataTransfer.dropEffect = "none";
            setDropDest(null);
          }}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setDropDest(null);
          }}
        >
          <EntryLeafRow
            paddingLeft={8}
            icon={
              <History size={14} className="shrink-0 text-muted-foreground" />
            }
            label="最近更新"
            description=""
            frontmatterError={null}
            active={updatesOpen}
            onOpen={() => setUpdatesOpen(!updatesOpen)}
          />
        </div>
        {/* biome-ignore lint/a11y/useSemanticElements: 自定义分组轨；fieldset 默认边框会破坏拖放目标。 */}
        <div
          role="group"
          aria-label="常驻"
          data-testid="prompt-rail-always"
          data-prompt-drop="root"
          onDragOver={(event) => acceptPromptDrag(event, { kind: "root" })}
          onDrop={(event) => dropPromptFile(event, "root")}
          className={cn(
            dropDest?.kind === "root" &&
              "rounded-lg ring-1 ring-inset ring-primary",
          )}
        >
          <RailSectionHeader label="常驻" />
          {rail.constitution.map((item) => railFile(item, 8, null))}
          <div data-testid="prompt-rail-memory">
            {rail.memory.map((item) => railFile(item, 8, null))}
          </div>
          {rail.alwaysMine.map((item) => railFile(item, 8, null))}
        </div>
        {/* biome-ignore lint/a11y/useSemanticElements: 自定义分组轨；fieldset 默认边框会破坏拖放目标。 */}
        <div
          role="group"
          aria-label="按需"
          data-testid="prompt-rail-on-demand"
          data-prompt-drop="folder"
          onDragOver={(event) =>
            acceptPromptDrag(event, { kind: "folder", folder: otherDrop })
          }
          onDrop={(event) => dropPromptFile(event, otherDrop)}
        >
          <RailSectionHeader
            label="按需"
            action={
              <div className="flex items-center gap-0.5">
                <IconButton
                  title="新建条目"
                  disabled={busy}
                  onClick={() => void onCreateMine()}
                >
                  <FilePlus size={14} />
                </IconButton>
                <IconButton
                  title="新建夹"
                  disabled={busy}
                  onClick={() => void onCreateFolder()}
                >
                  <FolderPlus size={14} />
                </IconButton>
              </div>
            }
          />
          {rail.folders.map((folder) => {
            const open = openFolders.size === 0 || openFolders.has(folder.id);
            const highlighted =
              dropDest?.kind === "folder" && dropDest.folder.id === folder.id;
            return (
              <div
                key={folder.id}
                data-prompt-drop="folder"
                data-testid={
                  folder.items.some((row) => row.kind === "mine")
                    ? "my-skills"
                    : undefined
                }
                onDragOver={(event) =>
                  acceptPromptDrag(event, { kind: "folder", folder })
                }
                onDrop={(event) => dropPromptFile(event, folder)}
                className={cn(
                  highlighted && "rounded-lg ring-1 ring-inset ring-primary",
                )}
              >
                <button
                  type="button"
                  aria-expanded={open}
                  aria-label={highlighted ? `将放入${folder.name}` : undefined}
                  onClick={() => {
                    setCreateFolderId(folder.documentId);
                    setOpenFolders((prev) => {
                      const next = new Set(prev);
                      if (prev.size === 0) {
                        for (const row of rail.folders) next.add(row.id);
                      }
                      if (next.has(folder.id)) next.delete(folder.id);
                      else next.add(folder.id);
                      return next;
                    });
                  }}
                  style={{ paddingLeft: 8 }}
                  className="flex h-7 w-full items-center gap-1.5 rounded-lg pr-1 text-left text-sm text-foreground hover:bg-accent/60"
                >
                  {open ? (
                    <ChevronDown
                      size={14}
                      className="shrink-0 text-muted-foreground"
                    />
                  ) : (
                    <ChevronRight
                      size={14}
                      className="shrink-0 text-muted-foreground"
                    />
                  )}
                  {open ? (
                    <FolderOpen
                      size={14}
                      className="shrink-0 text-muted-foreground"
                    />
                  ) : (
                    <Folder
                      size={14}
                      className="shrink-0 text-muted-foreground"
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                </button>
                {open
                  ? folder.items.map((item) =>
                      railFile(item, 20, folder.documentId),
                    )
                  : null}
              </div>
            );
          })}
          <div
            onDragOver={(event) => {
              if (!hasPromptDrag(event)) return;
              event.stopPropagation();
              event.dataTransfer.dropEffect = "none";
              setDropDest(null);
            }}
            onDrop={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setDropDest(null);
            }}
          >
            {rail.official.map((item) => railFile(item, 8, null))}
          </div>
        </div>
      </nav>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {error ? (
          <p
            className="shrink-0 border-b border-border px-3 py-1.5 text-destructive text-xs"
            role="alert"
          >
            {error}
          </p>
        ) : null}
        {updatesOpen ? (
          <div className="min-h-0 flex-1">
            <MemoryUpdatesView
              embedded
              onOpenLeaf={(path, _name, projectId) => {
                if (isAccountMemoryTarget(path, projectId)) {
                  setPendingLeaf(path);
                  setUpdatesOpen(false);
                  return;
                }
                navigate(APP_PATHS.files, {
                  state: filesMemoryLeafNavState(path, projectId),
                });
              }}
            />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <CatalogDetail
              item={selected}
              overlay={overlay}
              listings={listings}
              installedCopyIds={installedCopyIds}
              busy={busy}
              onSaveMine={(item, draft) =>
                persist(
                  async () => {
                    const fileName = skillFileName(draft.name);
                    if (fileName !== skillFileName(item.label)) {
                      await renameDocument(item.mineId, fileName);
                    }
                    const written = await writeDocument(
                      item.mineId,
                      composeSkillContent(
                        item.applyMode,
                        draft.description,
                        draft.body,
                      ),
                      item.version,
                    );
                    if (written.conflict) {
                      throw new Error("刚有更新，刷新后再保存");
                    }
                    if (!written.ok) {
                      throw new Error("没保存成功");
                    }
                    return undefined;
                  },
                  { lock: false },
                )
              }
              onSaveAccount={(item, draft) =>
                persist(
                  async () => {
                    if (item.memoryKind) {
                      const written = await writeMemoryFile(
                        item.memoryKind,
                        draft.body,
                        draft.version,
                      );
                      if (written.conflict) {
                        throw new Error("刚有更新，刷新后再保存");
                      }
                      if (!written.ok) {
                        throw new Error("没保存成功");
                      }
                      return undefined;
                    }
                    const fileName = skillFileName(draft.name);
                    if (item.mineId && fileName !== skillFileName(item.label)) {
                      await renameDocument(item.mineId, fileName);
                    }
                    const written = await writeDocument(
                      item.mineId,
                      draft.body,
                      draft.version,
                    );
                    if (written.conflict) {
                      throw new Error("刚有更新，刷新后再保存");
                    }
                    if (!written.ok) {
                      throw new Error("没保存成功");
                    }
                    return undefined;
                  },
                  { lock: false },
                )
              }
              onPublishMine={(item) =>
                persist(async () => {
                  const existing = listings.find(
                    (row) => row.documentId === item.mineId,
                  );
                  if (existing?.status === "taken_down") return undefined;
                  if (existing?.status === "published") {
                    await publishSkillVersion(existing.id, item.mineId);
                  } else {
                    await publishSkill(item.mineId);
                  }
                  setListings(await listMySkillListings());
                  return undefined;
                })
              }
              onUnpublishMine={(item) =>
                persist(async () => {
                  const existing = listings.find(
                    (row) => row.documentId === item.mineId,
                  );
                  if (!existing) return undefined;
                  await unpublishSkill(existing.id);
                  setListings(await listMySkillListings());
                  return undefined;
                })
              }
            />
          </div>
        )}
      </div>
    </div>
  );
}

function CatalogDetail({
  item,
  overlay,
  listings,
  installedCopyIds,
  busy,
  onSaveMine,
  onSaveAccount,
  onPublishMine,
  onUnpublishMine,
}: {
  item: PromptCatalogItem;
  overlay: SkillCatalog;
  listings: SkillStoreListing[];
  installedCopyIds: Set<string>;
  busy: boolean;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => Promise<boolean>;
  onSaveAccount: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; body: string; version: string },
  ) => Promise<boolean>;
  onPublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onUnpublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  if (item.kind === "identity") {
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
          <h2 className="font-medium text-foreground text-sm">{item.label}</h2>
          <Badge tone="muted">官方</Badge>
          <Badge tone="muted">三选一</Badge>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden px-3 py-3">
          <RoleIdentityBlock
            ceoIdentity={item.ceoIdentity}
            nestedIdentity={item.nestedIdentity}
            leafIdentity={item.leafIdentity}
          />
        </div>
      </div>
    );
  }

  if (item.kind === "shared") {
    return (
      <PromptWorkbench
        title={item.label}
        badges={<Badge tone="muted">官方</Badge>}
        initialBody={item.text}
        readOnly
        hideHeading={item.label}
      />
    );
  }

  if (item.kind === "skill") {
    return (
      <PromptWorkbench
        testId="factory-skill-editor"
        title={item.label}
        badges={<Badge tone="muted">官方</Badge>}
        initialBody={item.skill.body}
        readOnly
        hideHeading={item.label}
      />
    );
  }

  if (item.kind === "mine" && item.memoryKind) {
    return (
      <AccountEntryEditor key={item.id} item={item} onSave={onSaveAccount} />
    );
  }

  if (item.kind === "mine") {
    const fromMarket = Boolean(
      item.mineId && installedCopyIds.has(item.mineId),
    );
    return (
      <MineSkillEditor
        key={item.id}
        item={item}
        listing={listings.find((row) => row.documentId === item.mineId) ?? null}
        fromMarket={fromMarket}
        writable={overlay.writable}
        busy={busy}
        onSave={onSaveMine}
        onPublish={onPublishMine}
        onUnpublish={onUnpublishMine}
      />
    );
  }

  return null;
}

function MineSkillEditor({
  item,
  listing,
  fromMarket,
  writable,
  busy,
  onSave,
  onPublish,
  onUnpublish,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  listing: SkillStoreListing | null;
  fromMarket: boolean;
  writable: boolean;
  busy: boolean;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => Promise<boolean>;
  onPublish: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onUnpublish: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  const [body, setBody] = useState(() => skillBodyFromContent(item.content));
  const [version, setVersion] = useState(item.version);
  const [loading, setLoading] = useState(Boolean(item.mineId) && !item.content);
  const canPublish =
    writable &&
    !fromMarket &&
    item.applyMode === "on_demand" &&
    listing?.status !== "taken_down";
  const canUnpublish =
    writable && !fromMarket && listing?.status === "published";

  useEffect(() => {
    if (!item.mineId || item.content) {
      setBody(skillBodyFromContent(item.content));
      setVersion(item.version);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void getDocument(item.mineId)
      .then((doc) => {
        if (cancelled) return;
        setBody(skillBodyFromContent(doc.content));
        setVersion(doc.version);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item.mineId, item.content, item.version]);

  return (
    <PromptWorkbench
      key={loading ? `${item.id}-loading` : item.id}
      testId="mine-skill-editor"
      title={item.label}
      titleEditable
      badges={
        <>
          <Badge tone="muted">{fromMarket ? "市场" : "我的"}</Badge>
          {item.aiMaintained ? <Badge tone="muted">AI 可能改</Badge> : null}
          {listing?.status === "published" ? (
            <Badge tone="muted">已上架</Badge>
          ) : null}
          {listing?.status === "taken_down" ? (
            <Badge tone="muted">平台已下架</Badge>
          ) : null}
        </>
      }
      initialTrigger={item.description}
      triggerEnabled={item.applyMode === "on_demand"}
      initialBody={body}
      bodyLoading={loading}
      readOnly={!writable}
      extraActions={
        <>
          {canPublish ? (
            <Button
              type="button"
              variant="ghost"
              disabled={busy || loading}
              onClick={() => onPublish(item)}
            >
              上架
            </Button>
          ) : null}
          {canUnpublish ? (
            <Button
              type="button"
              variant="ghost"
              disabled={busy || loading}
              onClick={() => onUnpublish(item)}
            >
              下架
            </Button>
          ) : null}
        </>
      }
      onSave={
        writable
          ? (draft) =>
              onSave(
                { ...item, version },
                {
                  name: draft.title,
                  description: draft.trigger,
                  body: draft.body,
                },
              )
          : undefined
      }
    />
  );
}

function AccountEntryEditor({
  item,
  onSave,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; body: string; version: string },
  ) => Promise<boolean>;
}) {
  const [body, setBody] = useState(item.content);
  const [version, setVersion] = useState(item.version);
  const [loading, setLoading] = useState(!item.content);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        if (item.memoryKind) {
          const file = await getMemoryFile(item.memoryKind);
          if (cancelled) return;
          setBody(file.content);
          setVersion(file.version);
          setLoading(false);
          return;
        }
        if (!item.mineId) {
          setLoading(false);
          return;
        }
        const doc = await getDocument(item.mineId);
        if (cancelled) return;
        setBody(doc.content);
        setVersion(doc.version);
        setLoading(false);
      } catch {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [item.memoryKind, item.mineId]);

  const renameable = !item.memoryKind && Boolean(item.mineId);

  return (
    <PromptWorkbench
      key={loading ? `${item.id}-loading` : item.id}
      testId="account-entry-editor"
      title={item.label}
      titleEditable={renameable}
      badges={
        <>
          <Badge tone="muted">我的</Badge>
          {item.aiMaintained ? <Badge tone="muted">AI 可能改</Badge> : null}
        </>
      }
      hint={
        item.aiMaintained
          ? "AI 可能改这份。对话里学到的内容会写进来，不能当商店货上架。"
          : undefined
      }
      initialBody={body}
      bodyLoading={loading}
      onSave={(draft) =>
        onSave(item, { name: draft.title, body: draft.body, version })
      }
    />
  );
}

function PromptRailFile({
  item,
  selectedId,
  updatesOpen,
  paddingLeft,
  onOpen,
  onDragEnd,
  onRename,
  onRestore,
  onDelete,
}: {
  item: PromptCatalogItem;
  selectedId: string;
  updatesOpen: boolean;
  paddingLeft: number;
  onOpen: () => void;
  onDragEnd: () => void;
  onRename: () => void;
  onRestore: () => void;
  onDelete: () => void;
}) {
  const active = !updatesOpen && item.id === selectedId;
  const dimmed = item.kind === "mine" && item.disputed;
  const description =
    item.kind === "mine" && item.description.trim() !== item.label
      ? item.description
      : "";
  const canMove =
    item.kind === "mine" && Boolean(item.mineId) && !item.memoryKind;
  const hasMenu =
    item.kind === "mine" && Boolean(item.mineId) && !item.memoryKind;
  const row = (
    <EntryLeafRow
      paddingLeft={paddingLeft}
      icon={<FileText size={14} className="shrink-0 text-muted-foreground" />}
      label={item.label}
      description={description}
      frontmatterError={null}
      disputed={item.kind === "mine" && item.disputed}
      active={active}
      onOpen={onOpen}
      dimmed={dimmed}
      draggable={canMove}
      onDragStart={
        canMove
          ? (event) => {
              if (item.kind === "mine") {
                event.dataTransfer.setData(
                  PROMPT_DRAG_MIME,
                  promptDragPayload({ kind: "mine", mineId: item.mineId }),
                );
              }
              event.dataTransfer.effectAllowed = "move";
            }
          : undefined
      }
      onDragEnd={onDragEnd}
    />
  );
  if (!hasMenu) return row;
  const disputed = item.kind === "mine" && item.disputed;
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{row}</ContextMenuTrigger>
      <ContextMenuContent className="min-w-36">
        {disputed ? (
          <ContextMenuItem onSelect={onRestore}>
            <Undo2 size={14} className="shrink-0" />
            恢复使用
          </ContextMenuItem>
        ) : null}
        <ContextMenuItem onSelect={onRename}>
          <Pencil size={14} className="shrink-0" />
          重命名
        </ContextMenuItem>
        <ContextMenuItem variant="danger" onSelect={onDelete}>
          <Trash2 size={14} className="shrink-0" />
          删除
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
