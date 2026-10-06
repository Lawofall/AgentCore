import { InlineInput } from "@/components/files/FileTreeInline";
import {
  UNTITLED_PROMPT_FOLDER_NAME,
  isUntitledPromptFolderName,
  uniqueNumberedName,
} from "@/components/files/dedupeName";
import {
  type MineBatchConfirmState,
  type MineBatchFailure,
  type MineBatchFailureState,
  PromptCatalogBatchDialogs,
} from "@/components/tools/PromptCatalogBatchDialogs";
import { PromptCatalogSelectionBar } from "@/components/tools/PromptCatalogSelectionBar";
import {
  type PromptDropDest,
  PromptOverview,
} from "@/components/tools/PromptOverview";
import { PromptReadDialog } from "@/components/tools/PromptReadDialog";
import { Button, ConfirmDialog, SearchField } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { patchConversationCache } from "@/hooks/useConversations";
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
  type PromptRailFolder,
  buildMineCatalogRows,
  buildPromptRail,
  flattenPromptRail,
  mineCatalogId,
  onDemandDropFolder,
  skillCatalogId,
  toolCatalogId,
} from "@/lib/promptCatalog";
import {
  PROMPT_DRAG_MIME,
  isPromptDrag,
  parsePromptDragPayload,
  promptDragPayload,
} from "@/lib/promptCatalogDrag";
import {
  EMPTY_MINE_SELECTION,
  type MineSelectedItem,
  clickIntent,
  dropFromSelection,
  flattenVisibleMineItems,
  isSelectionOnlyClick,
  mineItemOf,
  selectRow,
  selectionCatalogIds,
  selectionForContextMenu,
  selectionHas,
} from "@/lib/promptCatalogSelection";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import { useEditingAssembly } from "@/pages/toolbox/useEditingAssembly";
import { ApiError } from "@/services/api";
import type { Capabilities } from "@/services/capabilities";
import { setConversationModelProfile } from "@/services/conversations";
import {
  createRuleDocument,
  createRuleFolder,
  deleteDocument,
  listAccountPromptTree,
  listScopeEntries,
  renameDocument,
  reparentDocument,
  writeDocument,
} from "@/services/documents";
import {
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import { defaultChatSupportsTools } from "@/services/llmProviders";
import {
  EMPTY_SKILL_CATALOG,
  type SkillCatalog,
  composeOnDemandSkillContent,
  composeSkillContent,
  getSkillCatalog,
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
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
import {
  type DragEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";

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

/** A name the person actually chose. The generated placeholder does not count. */
function chosenFolderName(raw: string, generated: string): string | null {
  const name = raw.trim().replace(/^\/+|\/+$/g, "");
  if (!name || name.includes("/") || name === generated) return null;
  return name;
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

/** Portrait overview + centered read dialog for the toolbox 交代 section. */
export function PromptCatalog({
  data,
  query: queryProp,
  suppressMiss = false,
  onMissChange,
}: {
  data: Capabilities;
  /** When set, the page owns the search box and this section only filters. */
  query?: string;
  suppressMiss?: boolean;
  onMissChange?: (miss: boolean) => void;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [overlay, setOverlay] = useState<SkillCatalog>(EMPTY_SKILL_CATALOG);
  const [accountEntries, setAccountEntries] = useState<AccountScopeEntry[]>([]);
  const [listings, setListings] = useState<SkillStoreListing[]>([]);
  const [installedListings, setInstalledListings] = useState<
    SkillStoreListing[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rulesDirId, setRulesDirId] = useState<string | null>(null);
  const [promptFolders, setPromptFolders] = useState<
    { id: string; name: string }[]
  >([]);
  const createFolderIdRef = useRef<string | null>(null);
  const pendingUntitledRef = useRef<{ id: string; name: string } | null>(null);
  const folderSettleRef = useRef<Promise<void> | null>(null);
  /** Skip the empty-placeholder sweep while a new folder is still being created. */
  const suppressSweepRef = useRef(false);
  /** Folder ids already handed to delete, so the sweep does not delete them twice. */
  const releasedFolderIdsRef = useRef(new Set<string>());
  /** Placeholder still on screen while its chosen name is being saved. */
  const renameHoldRef = useRef<string | null>(null);
  const promptFoldersRef = useRef(promptFolders);
  promptFoldersRef.current = promptFolders;
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renamingMineId, setRenamingMineId] = useState<string | null>(null);
  const [openFolderId, setOpenFolderId] = useState<string | null>(null);
  const [promptDragging, setPromptDragging] = useState(false);
  const [dropDest, setDropDest] = useState<PromptDropDest | null>(null);
  const queryClient = useQueryClient();
  const {
    profile,
    pending: assemblyPending,
    conversationId,
  } = useEditingAssembly();
  const [factoryPending, setFactoryPending] = useState(false);
  const { data: llmProviders } = useLlmProviders();
  const { data: modelCatalog } = useModels();
  const showToolsHint = needsToolsGateHint(
    defaultChatSupportsTools(llmProviders, modelCatalog?.current?.provider_id),
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
  const [ownQuery, setOwnQuery] = useState("");
  const query = queryProp ?? ownQuery;
  const searchOwned = queryProp !== undefined;
  const [selectedId, setSelectedId] = useState<string>(() => {
    const tool = searchParams.get("tool");
    if (tool) return toolCatalogId(tool);
    const skill = searchParams.get("skill");
    if (skill) return skillCatalogId(skill);
    return OVERVIEW_CATALOG_ID;
  });
  const [selection, setSelection] = useState(EMPTY_MINE_SELECTION);
  const [deleteConfirm, setDeleteConfirm] =
    useState<MineBatchConfirmState | null>(null);
  const [folderDissolve, setFolderDissolve] = useState<{
    documentId: string;
    name: string;
    childIds: readonly string[];
    busy: boolean;
  } | null>(null);
  const [batchFailure, setBatchFailure] =
    useState<MineBatchFailureState | null>(null);

  const visibleMine = useMemo(
    () => flattenVisibleMineItems(rail, query, openFolderId),
    [openFolderId, rail, query],
  );
  const pickedIds = useMemo(() => selectionCatalogIds(selection), [selection]);

  const selectedItem = items.find((item) => item.id === selectedId) ?? null;
  const dialogOpen = selectedItem != null;

  useEffect(() => {
    const live = new Set(
      items.filter((row) => row.kind === "mine").map((row) => row.id),
    );
    setSelection((sel) =>
      dropFromSelection(
        sel,
        sel.items
          .filter((row) => !live.has(row.catalogId))
          .map((row) => row.catalogId),
      ),
    );
  }, [items]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (deleteConfirm || folderDissolve || batchFailure) return;
      if (dialogOpen) return;
      if (selection.items.length === 0) return;
      setSelection(EMPTY_MINE_SELECTION);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    batchFailure,
    deleteConfirm,
    dialogOpen,
    folderDissolve,
    selection.items.length,
  ]);

  useEffect(() => {
    const tool = searchParams.get("tool");
    const skill = searchParams.get("skill");
    if (tool) setSelectedId(toolCatalogId(tool));
    else if (skill) setSelectedId(skillCatalogId(skill));
  }, [searchParams]);

  const closeDialog = useCallback(() => {
    setSelectedId(OVERVIEW_CATALOG_ID);
    createFolderIdRef.current = null;
    setRenamingMineId(null);
    setRenamingFolderId(null);
    if (searchParams.has("tool") || searchParams.has("skill")) {
      const next = new URLSearchParams(searchParams);
      next.delete("tool");
      next.delete("skill");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

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
    const folders = tree.folders.map((folder) => ({
      id: folder.id,
      name: folder.name,
    }));
    promptFoldersRef.current = folders;
    setPromptFolders(folders);
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
      });
    void Promise.all([listMySkillListings(), listInstalledSkills()])
      .then(([mine, installed]) => {
        if (cancelled) return;
        setListings(mine);
        setInstalledListings(installed);
      })
      .catch(() => {
        if (cancelled) return;
        setListings([]);
        setInstalledListings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAccountLayer]);

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
        const folders = tree.folders.map((folder) => ({
          id: folder.id,
          name: folder.name,
        }));
        promptFoldersRef.current = folders;
        setPromptFolders(folders);
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

  function rememberCreateFolder(id: string | null) {
    createFolderIdRef.current = id;
  }

  function folderHasItems(id: string): boolean {
    return mineRows.some((row) => row.parentId === id);
  }

  function folderChildIds(documentId: string): string[] {
    return accountEntries
      .filter((row) => row.parentId === documentId)
      .map((row) => row.id);
  }

  function closeFolderDocument(documentId: string) {
    setOpenFolderId((current) =>
      current === `folder:${documentId}` ? null : current,
    );
    setRenamingFolderId((current) => (current === documentId ? null : current));
    if (createFolderIdRef.current === documentId) rememberCreateFolder(null);
    if (pendingUntitledRef.current?.id === documentId) {
      pendingUntitledRef.current = null;
    }
  }

  function trackSettle(work: Promise<void>): Promise<void> {
    let current: Promise<void> | null = null;
    const settled = work.finally(() => {
      if (current && folderSettleRef.current === current) {
        folderSettleRef.current = null;
      }
    });
    current = settled;
    folderSettleRef.current = settled;
    return settled;
  }

  function beginReleaseUntitled(id: string): Promise<void> {
    const pending = pendingUntitledRef.current;
    if (!pending || pending.id !== id) return Promise.resolve();
    pendingUntitledRef.current = null;
    if (folderHasItems(id)) return Promise.resolve();
    releasedFolderIdsRef.current.add(id);
    if (createFolderIdRef.current === id) rememberCreateFolder(null);
    return trackSettle(
      persist(
        async () => {
          await deleteDocument(id);
          return undefined;
        },
        { lock: false },
      ).then(() => undefined),
    );
  }

  async function ensureNamedFolder(name: string): Promise<string> {
    const existing = promptFolders.find((folder) => folder.name === name);
    if (existing) return existing.id;
    const created = await createRuleFolder(name);
    return created.id;
  }

  async function onCreateMine() {
    setRenamingMineId(null);
    // 夹名输入先失焦收场，再决定这条新建进哪只夹。
    if (folderSettleRef.current) await folderSettleRef.current;
    setRenamingFolderId(null);
    const openFolder = rail.folders.find(
      (folder) => folder.id === openFolderId,
    );
    const openDoc =
      openFolder?.source === "user" ? openFolder.documentId : null;
    const openStillThere =
      Boolean(openDoc) &&
      promptFoldersRef.current.some((folder) => folder.id === openDoc);
    await persist(async () => {
      const parentId =
        (openStillThere ? openDoc : null) ??
        createFolderIdRef.current ??
        (await ensureNamedFolder(OTHER_FOLDER_NAME));
      const created = await createRuleDocument(
        skillFileName("未命名提示词"),
        null,
        composeOnDemandSkillContent("", ""),
        "on_demand",
        parentId,
      );
      const catalog = await loadAccountLayer();
      setSelectedId(mineCatalogId(created.id));
      return catalog;
    });
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: accountEntries is the catalog key; helpers are recreated each render
  useEffect(() => {
    if (suppressSweepRef.current) return;
    const pendingId = pendingUntitledRef.current?.id ?? null;
    const targets = promptFolders.filter(
      (folder) =>
        isUntitledPromptFolderName(folder.name) &&
        folderChildIds(folder.id).length === 0 &&
        folder.id !== pendingId &&
        folder.id !== renamingFolderId &&
        folder.id !== renameHoldRef.current &&
        !releasedFolderIdsRef.current.has(folder.id),
    );
    if (targets.length === 0) return;
    for (const folder of targets) releasedFolderIdsRef.current.add(folder.id);
    void persist(
      async () => {
        for (const folder of targets) {
          closeFolderDocument(folder.id);
          await deleteDocument(folder.id);
        }
        return undefined;
      },
      { lock: false },
    );
  }, [accountEntries, promptFolders, renamingFolderId]);

  async function createUntitledPromptFolder() {
    suppressSweepRef.current = true;
    setRenamingMineId(null);
    try {
      if (folderSettleRef.current) await folderSettleRef.current;
      let createdId: string | null = null;
      let createdName = "";
      const ok = await persist(async () => {
        createdName = uniqueNumberedName(
          UNTITLED_PROMPT_FOLDER_NAME,
          promptFoldersRef.current.map((folder) => folder.name),
        );
        const created = await createRuleFolder(createdName);
        createdId = created.id;
        return undefined;
      });
      if (ok && createdId) {
        pendingUntitledRef.current = { id: createdId, name: createdName };
        rememberCreateFolder(createdId);
        setRenamingFolderId(createdId);
        setOpenFolderId(`folder:${createdId}`);
      }
    } finally {
      suppressSweepRef.current = false;
    }
  }

  async function submitRenameFolder(id: string, raw: string) {
    const pending = pendingUntitledRef.current;
    if (pending && pending.id === id) {
      const name = chosenFolderName(raw, pending.name);
      if (!name) {
        setRenamingFolderId(null);
        await beginReleaseUntitled(id);
        return;
      }
      // Hold the placeholder until the chosen name is on screen, or the sweep
      // deletes this empty folder in the gap.
      renameHoldRef.current = id;
      pendingUntitledRef.current = null;
      setRenamingFolderId(null);
      try {
        await trackSettle(
          persist(
            async () => {
              await renameDocument(id, name);
              return undefined;
            },
            { lock: false },
          ).then(() => undefined),
        );
      } finally {
        renameHoldRef.current = null;
      }
      return;
    }
    setRenamingFolderId(null);
    const name = raw.trim().replace(/^\/+|\/+$/g, "");
    if (!name || name.includes("/")) return;
    const current = promptFolders.find((folder) => folder.id === id);
    if (current && current.name === name) return;
    await trackSettle(
      persist(async () => {
        await renameDocument(id, name);
        return undefined;
      }).then(() => undefined),
    );
  }

  function cancelRenameFolder() {
    const id = renamingFolderId;
    setRenamingFolderId(null);
    if (id) void beginReleaseUntitled(id);
  }

  function startRenameFolder(folder: PromptRailFolder) {
    if (!folder.documentId) return;
    setRenamingMineId(null);
    setRenamingFolderId(folder.documentId);
  }

  async function removeEmptyFolder(documentId: string) {
    releasedFolderIdsRef.current.add(documentId);
    closeFolderDocument(documentId);
    await persist(
      async () => {
        await deleteDocument(documentId);
        return undefined;
      },
      { lock: false },
    );
  }

  function requestDeleteFolder(folder: PromptRailFolder) {
    const id = folder.documentId;
    if (!id || folder.source !== "user") return;
    const childIds = folderChildIds(id);
    if (childIds.length === 0) {
      void removeEmptyFolder(id);
      return;
    }
    setFolderDissolve({
      documentId: id,
      name: folder.name,
      childIds,
      busy: false,
    });
  }

  async function confirmDissolveFolder() {
    const target = folderDissolve;
    if (!target || target.busy) return;
    setFolderDissolve({ ...target, busy: true });
    setError(null);
    try {
      const otherId = await ensureNamedFolder(OTHER_FOLDER_NAME);
      for (const childId of target.childIds) {
        // Server folder delete removes the subtree. Move entries out first.
        await reparentDocument(childId, otherId);
      }
      releasedFolderIdsRef.current.add(target.documentId);
      closeFolderDocument(target.documentId);
      await deleteDocument(target.documentId);
      setOverlay(await loadAccountLayer());
      setFolderDissolve(null);
    } catch (err) {
      setError(overlayErrorMessage(err));
      setFolderDissolve((current) =>
        current ? { ...current, busy: false } : null,
      );
    }
  }

  function alreadyAtDest(
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    dest: PromptRailFolder | "root",
  ): boolean {
    if (dest === "root") return item.applyMode === "always";
    return Boolean(dest.documentId && item.parentId === dest.documentId);
  }

  async function reparentMine(
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    dest: PromptRailFolder | "root",
  ) {
    if (dest === "root") {
      let parent = rulesDirId;
      if (!parent) {
        await createRuleFolder(OTHER_FOLDER_NAME);
        parent = (await listAccountPromptTree()).rulesDirId;
      }
      if (!parent) throw new Error("没找到常驻目录");
      await reparentDocument(item.mineId, parent, "always");
      return;
    }
    let folderId = dest.documentId;
    if (!folderId) folderId = await ensureNamedFolder(dest.name);
    await reparentDocument(item.mineId, folderId, "on_demand");
  }

  async function moveMineItems(
    mineIds: readonly string[],
    dest: PromptRailFolder | "root",
  ) {
    const failures: MineBatchFailure[] = [];
    setBusy(true);
    setError(null);
    try {
      for (const mineId of mineIds) {
        const item = items.find(
          (row) => row.kind === "mine" && row.mineId === mineId,
        );
        if (!item || item.kind !== "mine") continue;
        if (alreadyAtDest(item, dest)) continue;
        try {
          await reparentMine(item, dest);
        } catch (err) {
          failures.push({
            id: item.id,
            name: item.label,
            reason: overlayErrorMessage(err),
          });
        }
      }
      setOverlay(await loadAccountLayer());
      if (failures.length > 0) {
        setBatchFailure({ title: "有些条目没有移过去", failures });
      }
    } catch (err) {
      setError(overlayErrorMessage(err));
    } finally {
      setBusy(false);
    }
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
    void moveMineItems(payload.mineIds, dest);
  }

  function rejectPromptDrag(event: DragEvent) {
    if (!hasPromptDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "none";
    setDropDest(null);
  }

  async function toggleFactoryCatalog(present: boolean) {
    if (
      !profile ||
      (profile.kind !== "user" && profile.kind !== "system") ||
      factoryPending
    ) {
      return;
    }
    setFactoryPending(true);
    setError(null);
    try {
      let id = profile.id;
      if (profile.kind === "system") {
        const materialized = await setDefaultLlmModelProfile(profile.id);
        id = materialized.id;
        if (conversationId) {
          const saved = await setConversationModelProfile(conversationId, id);
          patchConversationCache(conversationId, {
            assemblyId: saved.assemblyId ?? id,
          });
        }
      }
      await updateLlmModelProfile(id, {
        omit_factory_catalog: !present,
      });
      await queryClient.invalidateQueries({
        queryKey: llmModelProfileKeys.list,
      });
    } catch (err) {
      setError(overlayErrorMessage(err));
    } finally {
      setFactoryPending(false);
    }
  }

  function handleActivate(
    id: string,
    event?: Pick<MouseEvent, "ctrlKey" | "metaKey" | "shiftKey">,
  ) {
    const item = items.find((row) => row.id === id);
    const mine = item ? mineItemOf(item) : null;
    const intent = event ? clickIntent(event) : { toggle: false, range: false };
    if (mine) {
      setSelection((sel) => selectRow(sel, mine, intent, visibleMine));
      if (isSelectionOnlyClick(intent)) return;
      setSelectedId(id);
      clearDeepLink();
      return;
    }
    if (isSelectionOnlyClick(intent)) return;
    setSelection(EMPTY_MINE_SELECTION);
    setSelectedId(id);
    const next = new URLSearchParams(searchParams);
    next.delete("tool");
    next.delete("skill");
    if (item?.kind === "tool") next.set("tool", item.tool.name);
    if (item?.kind === "skill") next.set("skill", item.skill.name);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }

  function clearDeepLink() {
    if (!searchParams.has("tool") && !searchParams.has("skill")) return;
    const next = new URLSearchParams(searchParams);
    next.delete("tool");
    next.delete("skill");
    setSearchParams(next, { replace: true });
  }

  function startRenameMine(item: PromptCatalogItem) {
    if (item.kind !== "mine" || !item.mineId || item.aiMaintained) return;
    setRenamingFolderId(null);
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

  function requestDelete(rows: readonly MineSelectedItem[]) {
    if (rows.length === 0) return;
    const hasMarket = rows.some((row) =>
      installedListings.some(
        (listing) => listing.installDocumentId === row.mineId,
      ),
    );
    setDeleteConfirm({ items: rows, hasMarket, busy: false });
  }

  async function confirmDelete() {
    if (!deleteConfirm) return;
    const doomed = deleteConfirm.items;
    setDeleteConfirm({ ...deleteConfirm, busy: true });
    const failures: MineBatchFailure[] = [];
    const deleted: string[] = [];
    setError(null);
    try {
      for (const row of doomed) {
        try {
          await deleteDocument(row.mineId);
          deleted.push(row.catalogId);
        } catch (err) {
          failures.push({
            id: row.catalogId,
            name: row.label,
            reason: overlayErrorMessage(err),
          });
        }
      }
      setOverlay(await loadAccountLayer());
      setSelection((sel) => dropFromSelection(sel, deleted));
      if (doomed.some((row) => row.catalogId === selectedId)) {
        setSelectedId(OVERVIEW_CATALOG_ID);
      }
      setDeleteConfirm(null);
      if (failures.length > 0) {
        setBatchFailure({ title: "有些条目没有删掉", failures });
      }
    } catch (err) {
      setError(overlayErrorMessage(err));
      setDeleteConfirm((current) =>
        current ? { ...current, busy: false } : null,
      );
    }
  }

  function wrapMineTile({
    item,
    children,
  }: {
    item: PromptCatalogItem;
    children: ReactNode;
  }) {
    if (item.kind !== "mine" || !item.mineId) {
      return children;
    }
    if (item.mineId === renamingMineId) {
      return (
        <div className="flex min-h-10 items-center rounded-xl border border-border px-4">
          <InlineInput
            initial={item.label}
            ariaLabel="条目名称"
            onSubmit={(value) => void submitRenameMine(item, value)}
            onCancel={() => setRenamingMineId(null)}
          />
        </div>
      );
    }
    const mine = mineItemOf(item);
    const inBatch =
      Boolean(mine) &&
      selection.items.length >= 2 &&
      selectionHas(selection, item.id);
    const inner = (
      <div
        className="min-h-10 min-w-0"
        draggable
        onContextMenu={() => {
          if (mine) setSelection((sel) => selectionForContextMenu(sel, mine));
        }}
        onDragStart={(event) => {
          const inSelection =
            selectionHas(selection, item.id) && selection.items.length > 0;
          const mineIds = inSelection
            ? selection.items.map((row) => row.mineId)
            : [item.mineId];
          event.dataTransfer.setData(
            PROMPT_DRAG_MIME,
            promptDragPayload({ kind: "mine", mineIds }),
          );
          event.dataTransfer.effectAllowed = "move";
          setPromptDragging(true);
        }}
        onDragEnd={() => {
          setPromptDragging(false);
          setDropDest(null);
        }}
      >
        {children}
      </div>
    );
    return (
      <ContextMenu>
        <ContextMenuTrigger asChild>{inner}</ContextMenuTrigger>
        <ContextMenuContent>
          {inBatch ? (
            <ContextMenuItem
              variant="danger"
              onSelect={() => requestDelete(selection.items)}
            >
              <Trash2 size={14} className="shrink-0" />
              删除 {selection.items.length} 项
            </ContextMenuItem>
          ) : (
            <>
              <ContextMenuItem onSelect={() => startRenameMine(item)}>
                <Pencil size={14} className="shrink-0" />
                重命名
              </ContextMenuItem>
              <ContextMenuItem
                variant="danger"
                onSelect={() => {
                  if (mine) requestDelete([mine]);
                }}
              >
                <Trash2 size={14} className="shrink-0" />
                删除
              </ContextMenuItem>
            </>
          )}
        </ContextMenuContent>
      </ContextMenu>
    );
  }

  const catalogActions = (
    <>
      {searchOwned ? null : (
        <SearchField
          aria-label="搜提示词"
          placeholder="搜提示词"
          value={query}
          onValueChange={setOwnQuery}
          className="w-52"
        />
      )}
      {busy || query.trim() ? null : (
        <button
          type="button"
          className="rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => void createUntitledPromptFolder()}
        >
          新建夹
        </button>
      )}
      <Button size="md" disabled={busy} onClick={() => void onCreateMine()}>
        新建
      </Button>
    </>
  );
  const catalog = (
    <>
      {error ? (
        <p className="mb-3 text-destructive text-xs" role="alert">
          {error}
        </p>
      ) : null}
      {selection.items.length >= 2 ? (
        <PromptCatalogSelectionBar
          count={selection.items.length}
          busy={busy || Boolean(deleteConfirm?.busy)}
          onDelete={() => requestDelete(selection.items)}
          onClear={() => setSelection(EMPTY_MINE_SELECTION)}
        />
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
          pickedIds={pickedIds}
          dropDest={dropDest}
          otherFolder={otherDrop}
          renamingFolderId={renamingFolderId}
          busy={busy}
          listings={listings}
          installedListings={installedListings}
          query={query}
          suppressMiss={suppressMiss}
          onMissChange={onMissChange}
          factoryControl={{
            present: !profile?.omit_factory_catalog,
            canToggle: profile?.kind === "user" || profile?.kind === "system",
            pending: factoryPending || assemblyPending,
            onPresentChange: (present) => void toggleFactoryCatalog(present),
          }}
          dragging={promptDragging}
          openFolderId={openFolderId}
          onOpenFolder={setOpenFolderId}
          onCloseFolder={() => setOpenFolderId(null)}
          onOpenItem={handleActivate}
          onCreateMine={() => void onCreateMine()}
          onSubmitRenameFolder={(id, name) => void submitRenameFolder(id, name)}
          onCancelRenameFolder={cancelRenameFolder}
          onRenameFolder={startRenameFolder}
          onDeleteFolder={requestDeleteFolder}
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
        item={selectedItem}
        overlay={overlay}
        listings={listings}
        installedListings={installedListings}
        busy={busy}
        showToolsHint={showToolsHint}
        toolsHint={TOOLS_GATE_HINT}
        toolCallingNames={TOOL_CALLING_TOOL_NAMES}
        onOpenChange={(open) => {
          if (!open) closeDialog();
        }}
        onSaveMine={(item, draft) =>
          persist(
            async () => {
              const fileName = skillFileName(draft.name);
              if (fileName !== skillFileName(item.label)) {
                await renameDocument(item.mineId, fileName);
              }
              const mode = draft.applyMode ?? item.applyMode;
              if (mode === "paths" && !draft.paths.trim()) {
                throw new Error("碰到文件要写路径，比如 **/*.tsx");
              }
              const written = await writeDocument(
                item.mineId,
                composeSkillContent(
                  mode,
                  draft.description,
                  draft.body,
                  draft.paths,
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
      <PromptCatalogBatchDialogs
        confirm={deleteConfirm}
        onConfirmDelete={() => void confirmDelete()}
        onCancelDelete={() => {
          if (!deleteConfirm?.busy) setDeleteConfirm(null);
        }}
        failure={batchFailure}
        onCloseFailure={() => setBatchFailure(null)}
      />
      <ConfirmDialog
        open={folderDissolve !== null}
        onOpenChange={(open) => {
          if (!open && !folderDissolve?.busy) setFolderDissolve(null);
        }}
        title={`删除「${folderDissolve?.name ?? ""}」？`}
        description={`里面 ${folderDissolve?.childIds.length ?? 0} 条会回到货架，这些条目不删。`}
        confirmLabel="删除"
        tone="danger"
        busy={folderDissolve?.busy ?? false}
        onConfirm={() => void confirmDissolveFolder()}
      />
    </>
  );
  const showFactorySwitch =
    profile?.kind === "user" || profile?.kind === "system";
  return (
    <div className="w-full" data-testid="prompt-catalog">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <h2 className="text-sm font-medium text-foreground">交代</h2>
          {showFactorySwitch ? (
            <div className="flex shrink-0 items-center gap-1.5">
              <span className="text-xs text-muted-foreground">出厂</span>
              <Switch
                checked={!profile?.omit_factory_catalog}
                disabled={factoryPending || assemblyPending}
                label="出厂"
                onCheckedChange={(on) => void toggleFactoryCatalog(on)}
              />
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-3">{catalogActions}</div>
      </div>
      {catalog}
    </div>
  );
}
