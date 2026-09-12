import { InlineInput } from "@/components/files/FileTreeInline";
import {
  type PromptDropDest,
  PromptOverview,
} from "@/components/tools/PromptOverview";
import {
  type ConnectorPick,
  PromptReadDialog,
} from "@/components/tools/PromptReadDialog";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import {
  TOOLS_GATE_HINT,
  TOOL_CALLING_TOOL_NAMES,
  needsToolsGateHint,
} from "@/lib/llmToolsGate";
import {
  type AccountScopeEntry,
  OTHER_FOLDER_NAME,
  OVERVIEW_CATALOG_ID,
  type PromptCatalogItem,
  type PromptCatalogLocationState,
  type PromptRailFolder,
  buildMineCatalogRows,
  buildPromptRail,
  catalogIdForMemoryTarget,
  flattenPromptRail,
  mineCatalogId,
  onDemandDropFolder,
  toolCatalogId,
} from "@/lib/promptCatalog";
import {
  PROMPT_DRAG_MIME,
  isPromptDrag,
  parsePromptDragPayload,
  promptDragPayload,
} from "@/lib/promptCatalogDrag";
import {
  ConnectorStatusBadge,
  NEW_CONNECTOR_ID,
  connectorCatalogId,
  useMcpConnectors,
} from "@/pages/toolbox/ConnectorsPage";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import type { Capabilities } from "@/services/capabilities";
import {
  createRuleDocument,
  createRuleFolder,
  deleteDocument,
  listAccountPromptTree,
  listScopeEntries,
  renameDocument,
  reparentDocument,
  setDocumentDisputed,
  writeDocument,
} from "@/services/documents";
import { defaultChatSupportsTools } from "@/services/llmProviders";
import { writeMemoryFile } from "@/services/memory";
import {
  EMPTY_SKILL_CATALOG,
  type SkillCatalog,
  composeOnDemandSkillContent,
  composeSkillContent,
  getSkillCatalog,
  skillFileName,
} from "@/services/skillCatalog";
import {
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
import { Pencil, Trash2, Undo2 } from "lucide-react";
import {
  type DragEvent,
  type ReactNode,
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

function hasPromptDrag(event: DragEvent): boolean {
  return isPromptDrag(Array.from(event.dataTransfer.types));
}

function promptDragTypes(event: DragEvent): string[] {
  return Array.from(event.dataTransfer.types);
}

function readPromptDrag(event: DragEvent) {
  return parsePromptDragPayload(event.dataTransfer.getData(PROMPT_DRAG_MIME));
}

function connectorTileDescription(server: {
  command: string;
  args: string[];
  runtimeError?: string | null;
}): string {
  if (server.runtimeError?.trim()) return server.runtimeError;
  return `${server.command} ${server.args.join(" ")}`.trim();
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
    alwaysChars: doc.alwaysChars,
    parentId: doc.parentId,
  };
}

/** Portrait overview + centered read dialog for the 工具箱「提示词」page. */
export function PromptCatalog({ data }: { data: Capabilities }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const updatesOpen = searchParams.get("updates") === "1";
  const [overlay, setOverlay] = useState<SkillCatalog>(EMPTY_SKILL_CATALOG);
  const [accountEntries, setAccountEntries] = useState<AccountScopeEntry[]>([]);
  const [listings, setListings] = useState<
    Awaited<ReturnType<typeof listMySkillListings>>
  >([]);
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
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingMineId, setRenamingMineId] = useState<string | null>(null);
  const [dropDest, setDropDest] = useState<PromptDropDest | null>(null);
  const [mcpBusyId, setMcpBusyId] = useState<string | null>(null);
  const mcp = useMcpConnectors();
  const { data: llmProviders } = useLlmProviders();
  const { data: modelCatalog } = useModels();
  const showToolsHint = needsToolsGateHint(
    defaultChatSupportsTools(llmProviders, modelCatalog?.current?.provider_id),
  );

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
  const connectorPicks = useMemo<ConnectorPick[]>(
    () =>
      mcp.servers.map((server) => ({
        kind: "connector" as const,
        id: connectorCatalogId(server.id),
        label: server.name,
        server,
      })),
    [mcp.servers],
  );

  const [selectedId, setSelectedId] = useState<string>(() => {
    const tool = searchParams.get("tool");
    if (tool) return toolCatalogId(tool);
    if (searchParams.get("connectors") === "1") return NEW_CONNECTOR_ID;
    return OVERVIEW_CATALOG_ID;
  });

  const selectedConnector: ConnectorPick | null =
    selectedId === NEW_CONNECTOR_ID
      ? {
          kind: "connector",
          id: NEW_CONNECTOR_ID,
          label: "添加连接器",
          server: null,
        }
      : (connectorPicks.find((row) => row.id === selectedId) ?? null);
  const selectedItem =
    items.find((item) => item.id === selectedId) ?? selectedConnector ?? null;
  const dialogOpen = updatesOpen || selectedItem != null;

  const closeDialog = useCallback(() => {
    setSelectedId(OVERVIEW_CATALOG_ID);
    setCreateFolderId(null);
    setRenamingMineId(null);
    if (updatesOpen) setUpdatesOpen(false);
  }, [updatesOpen, setUpdatesOpen]);

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
    if (id) {
      setSelectedId(id);
    }
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
    setCreatingFolder(false);
    setRenamingMineId(null);
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

  function startCreateFolder() {
    setRenamingMineId(null);
    setCreatingFolder(true);
  }

  async function submitCreateFolder(raw: string) {
    setCreatingFolder(false);
    const name = raw.trim().replace(/^\/+|\/+$/g, "");
    if (!name || name.includes("/")) return;
    await persist(async () => {
      const created = await createRuleFolder(name);
      setCreateFolderId(created.id);
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

  function acceptPromptDrag(event: DragEvent, dest: PromptDropDest) {
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

  function rejectPromptDrag(event: DragEvent) {
    if (!hasPromptDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "none";
    setDropDest(null);
  }

  function startRenameMine(item: PromptCatalogItem) {
    if (item.kind !== "mine" || !item.mineId || item.aiMaintained) return;
    setCreatingFolder(false);
    setRenamingMineId(item.mineId);
  }

  async function submitRenameMine(item: PromptCatalogItem, raw: string) {
    if (item.kind !== "mine" || !item.mineId || item.aiMaintained) return;
    setRenamingMineId(null);
    const name = skillFileName(raw.trim());
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
      setSelectedId(OVERVIEW_CATALOG_ID);
      return undefined;
    });
  }

  function wrapMineTile({
    item,
    children,
  }: {
    item: PromptCatalogItem;
    children: ReactNode;
  }) {
    if (item.kind !== "mine" || !item.mineId || item.memoryKind) {
      return children;
    }
    if (item.mineId === renamingMineId) {
      return (
        <div className="flex min-h-[7.5rem] items-center rounded-xl border border-border px-4">
          <InlineInput
            initial={item.label}
            ariaLabel="条目名称"
            onSubmit={(value) => void submitRenameMine(item, value)}
            onCancel={() => setRenamingMineId(null)}
          />
        </div>
      );
    }
    const disputed = item.disputed;
    const inner = (
      <div
        className="h-full min-w-0"
        draggable
        onDragStart={(event) => {
          event.dataTransfer.setData(
            PROMPT_DRAG_MIME,
            promptDragPayload({ kind: "mine", mineId: item.mineId }),
          );
          event.dataTransfer.effectAllowed = "move";
        }}
        onDragEnd={() => setDropDest(null)}
      >
        {children}
      </div>
    );
    return (
      <ContextMenu>
        <ContextMenuTrigger asChild>{inner}</ContextMenuTrigger>
        <ContextMenuContent className="min-w-36">
          {disputed ? (
            <ContextMenuItem onSelect={() => void restoreMineItem(item)}>
              <Undo2 size={14} className="shrink-0" />
              恢复使用
            </ContextMenuItem>
          ) : null}
          <ContextMenuItem onSelect={() => startRenameMine(item)}>
            <Pencil size={14} className="shrink-0" />
            重命名
          </ContextMenuItem>
          <ContextMenuItem
            variant="danger"
            onSelect={() => void deleteMineItem(item)}
          >
            <Trash2 size={14} className="shrink-0" />
            删除
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
    );
  }

  return (
    <div className="w-full" data-testid="prompt-catalog">
      {error ? (
        <p className="mb-3 text-destructive text-xs" role="alert">
          {error}
        </p>
      ) : null}
      <div
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
        <PromptOverview
          rail={rail}
          selectedId={selectedId === OVERVIEW_CATALOG_ID ? null : selectedId}
          dropDest={dropDest}
          otherFolder={otherDrop}
          connectors={connectorPicks.map((row) => ({
            id: row.id,
            label: row.label,
            description: row.server ? connectorTileDescription(row.server) : "",
            accessory: row.server ? (
              <ConnectorStatusBadge server={row.server} />
            ) : undefined,
          }))}
          connectorError={mcp.error}
          showConnectors={Boolean(mcp.api)}
          creatingFolder={creatingFolder}
          busy={busy}
          installedCopyIds={installedCopyIds}
          onOpenItem={(id) => {
            setSelectedId(id);
            if (updatesOpen) setUpdatesOpen(false);
          }}
          onOpenUpdates={() => {
            setSelectedId(OVERVIEW_CATALOG_ID);
            setUpdatesOpen(true);
          }}
          onCreateMine={() => void onCreateMine()}
          onStartCreateFolder={startCreateFolder}
          onSubmitCreateFolder={(name) => void submitCreateFolder(name)}
          onCancelCreateFolder={() => setCreatingFolder(false)}
          onAddConnector={
            mcp.api
              ? () => {
                  setSelectedId(NEW_CONNECTOR_ID);
                  if (updatesOpen) setUpdatesOpen(false);
                }
              : null
          }
          onAcceptAlwaysDrag={(event) =>
            acceptPromptDrag(event, { kind: "root" })
          }
          onDropAlways={(event) => dropPromptFile(event, "root")}
          onAcceptFolderDrag={(event, folder) =>
            acceptPromptDrag(event, { kind: "folder", folder })
          }
          onDropFolder={(event, folder) => dropPromptFile(event, folder)}
          onRejectDrag={rejectPromptDrag}
          renderMineTile={wrapMineTile}
        />
      </div>
      <PromptReadDialog
        open={dialogOpen}
        updatesOpen={updatesOpen}
        item={selectedItem}
        overlay={overlay}
        listings={listings}
        installedCopyIds={installedCopyIds}
        busy={busy}
        showToolsHint={showToolsHint}
        toolsHint={TOOLS_GATE_HINT}
        toolCallingNames={TOOL_CALLING_TOOL_NAMES}
        mcpApi={mcp.api}
        mcpBusyId={mcpBusyId}
        onOpenChange={(open) => {
          if (!open) closeDialog();
        }}
        onOpenUpdatesLeaf={(path, _name, projectId) => {
          if (isAccountMemoryTarget(path, projectId)) {
            setPendingLeaf(path);
            setUpdatesOpen(false);
            return;
          }
          navigate(APP_PATHS.files, {
            state: filesMemoryLeafNavState(path, projectId),
          });
        }}
        onMcpBusy={setMcpBusyId}
        onMcpSaved={mcp.reload}
        onCloseNewConnector={() => {
          setSelectedId(OVERVIEW_CATALOG_ID);
        }}
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
        onPublishMine={(item, group) =>
          void persist(async () => {
            const existing = listings.find(
              (row) => row.documentId === item.mineId,
            );
            if (existing?.status === "taken_down") return undefined;
            if (existing?.status === "published") {
              await publishSkillVersion(existing.id, item.mineId, group);
            } else {
              await publishSkill(item.mineId, group);
            }
            setListings(await listMySkillListings());
            return undefined;
          })
        }
        onUnpublishMine={(item) =>
          void persist(async () => {
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
  );
}
