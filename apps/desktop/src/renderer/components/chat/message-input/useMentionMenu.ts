import type {
  MentionMenuSection,
  MentionMenuSelectable,
} from "@/components/chat/MentionMenu";
import { getConversations } from "@/hooks/useConversations";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  type IndexedEntry,
  buildDirListing,
  filterEntries,
  loadFileIndex,
  mentionFilterTotal,
} from "@/lib/fileIndex";
import type { FileSource } from "@/lib/fileSource";
import { insertInlineToken } from "@/lib/inlineBody";
import { logEvent } from "@/lib/log";
import { fetchMessageWindow } from "@/services/messages";
import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import {
  type Dispatch,
  type KeyboardEvent,
  type RefObject,
  type SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ComposerBodyHandle } from "./ComposerBodyEditor";
import { startStagedAttachmentUpload } from "./attachmentUploads";
import {
  CONV_MENTION_MSG_LIMIT,
  EMPTY_MENTION_INDEX_LIMIT,
  MAX_AGENT_MENTIONS,
  type MentionSectionId,
  type PendingAgentMention,
  type PendingAttachment,
  buildMentionSources,
  detectMention,
  formatConversationContext,
  parseMentionFilter,
  pickRecentConversations,
} from "./composerAttachments";
import {
  MENTION_CATEGORY_LABEL,
  buildMentionCategoryRows,
  categoryHighlightIndex,
  isMentionSectionId,
  mentionMenuKeyAction,
  showMentionCategoryLevel,
} from "./mentionMenuLevel";
import { recordMentionRecent, stampMentionRecents } from "./mentionRecents";
import {
  pickLocalFileAttachment,
  stageRootFileAttachment,
} from "./resideAttachment";
import {
  type AttachmentFolderHint,
  resolveFolderFromCitedRoot,
  resolveFolderFromIndexedEntry,
} from "./resolveAttachmentFolder";
import type { MenuMode } from "./types";

export type { AttachmentFolderHint };

function isAgentItem(
  item: MentionMenuSelectable,
): item is { kind: "agent"; agentId: string; role: string } {
  return "kind" in item && item.kind === "agent" && "agentId" in item;
}

/** 从当前会话由近及远找最新带 agents 的 execution（诚实降级：无则空）。 */
function pickTeamAgents(
  messages: ReadonlyArray<{ id: string; role: string }>,
  byId: ReturnType<typeof useExecutionStore.getState>["byId"],
): { id: string; role: string }[] {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const agents = byId[m.id]?.plan?.agents;
    if (agents && agents.length > 0) {
      return agents.map((a) => ({ id: a.id, role: a.role }));
    }
  }
  return [];
}

const EMPTY_MESSAGES: { id: string; role: string }[] = [];
/** 钻入后的条数上限；空态仍用 composerAttachments 的 EMPTY_MENTION_INDEX_LIMIT。 */
const MENTION_DRILL_INDEX_LIMIT = 50;

export function useMentionMenu({
  conversationId,
  value,
  setValue,
  attachments,
  setAttachments,
  agentMentions,
  setAgentMentions,
  bodyRef,
  onAttachmentFolderHint,
  onBrowserFilePick,
}: {
  conversationId: string | null;
  value: string;
  setValue: Dispatch<SetStateAction<string>>;
  attachments: PendingAttachment[];
  setAttachments: Dispatch<SetStateAction<PendingAttachment[]>>;
  agentMentions: PendingAgentMention[];
  setAgentMentions: Dispatch<SetStateAction<PendingAgentMention[]>>;
  bodyRef: RefObject<ComposerBodyHandle | null>;
  /** Draft-only: @ / browse attach from a folder → suggest filing into it (B4). */
  onAttachmentFolderHint?: (hint: AttachmentFolderHint) => void;
  /** Web：无本机选择器时点「附件」走 hidden file input。 */
  onBrowserFilePick?: () => void;
}) {
  const [menuMode, setMenuMode] = useState<MenuMode>(null);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [activeCategory, setActiveCategory] = useState<MentionSectionId | null>(
    null,
  );
  const [menuError, setMenuError] = useState<string | null>(null);
  const [fileIndex, setFileIndex] = useState<IndexedEntry[]>([]);
  const [dirIndex, setDirIndex] = useState<IndexedEntry[]>([]);
  const [sourceCount, setSourceCount] = useState(0);
  const [indexTruncated, setIndexTruncated] = useState(false);
  const [indexLoading, setIndexLoading] = useState(false);
  const [convTick, setConvTick] = useState(0);
  const indexLoadedRef = useRef(false);
  const sourcesRef = useRef<Map<string, FileSource>>(new Map());
  const mentionRangeRef = useRef<{ start: number; end: number } | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  /** 手打 @ → 团队；工具栏 @ → 附件。打开后不因索引加载改高亮。 */
  const highlightPrefRef = useRef<"team" | "attach">("team");

  const messages = useConversationStore((s) => {
    if (!conversationId) return EMPTY_MESSAGES;
    return s.byId[conversationId]?.messages ?? EMPTY_MESSAGES;
  });
  const execById = useExecutionStore((s) => s.byId);

  const teamAgents = useMemo(
    () => pickTeamAgents(messages, execById),
    [messages, execById],
  );

  const { section: sectionFilter, filter: filterText } = useMemo(
    () => parseMentionFilter(query),
    [query],
  );
  const showCategoryLevel = showMentionCategoryLevel({
    sectionFilter,
    activeCategory,
    filterText,
  });
  const focusedSection = sectionFilter ?? activeCategory;

  // 缓存列表变动时（发送/新建）刷新对话分区；tick 作轻量失效键。
  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is an intentional re-run key
  useEffect(() => {
    if (!menuMode) return;
    setConvTick((n) => n + 1);
  }, [menuMode, conversationId]);

  const emptyLimit =
    focusedSection !== null
      ? MENTION_DRILL_INDEX_LIMIT
      : EMPTY_MENTION_INDEX_LIMIT;

  const convItems = useMemo(() => {
    void convTick;
    if (
      menuMode === "browse" &&
      !filterText.trim() &&
      !focusedSection &&
      !query.trim()
    ) {
      // browse 空搜不强推对话；有过滤词、类型前缀或已钻入时再出。
      return [];
    }
    return pickRecentConversations(
      getConversations(),
      conversationId,
      filterText,
      emptyLimit,
    );
  }, [
    convTick,
    menuMode,
    filterText,
    focusedSection,
    query,
    conversationId,
    emptyLimit,
  ]);

  const convCount = useMemo(() => {
    void convTick;
    return pickRecentConversations(
      getConversations(),
      conversationId,
      filterText,
      Number.MAX_SAFE_INTEGER,
    ).length;
  }, [convTick, conversationId, filterText]);

  const folderItems = useMemo(() => {
    return filterEntries(stampMentionRecents(dirIndex), filterText, emptyLimit);
  }, [dirIndex, filterText, emptyLimit]);

  const fileItems = useMemo(() => {
    return filterEntries(
      stampMentionRecents(fileIndex),
      filterText,
      emptyLimit,
    );
  }, [fileIndex, filterText, emptyLimit]);

  const agentItems = useMemo((): MentionMenuSelectable[] => {
    const q = filterText.trim().toLowerCase();
    let agents = teamAgents;
    if (q) {
      agents = agents.filter(
        (a) =>
          a.role.toLowerCase().includes(q) || a.id.toLowerCase().includes(q),
      );
    }
    return agents.map((a) => ({
      kind: "agent" as const,
      agentId: a.id,
      role: a.role,
    }));
  }, [teamAgents, filterText]);

  const folderCount = useMemo(
    () => mentionFilterTotal(dirIndex, filterText),
    [dirIndex, filterText],
  );
  const fileCount = useMemo(
    () => mentionFilterTotal(fileIndex, filterText),
    [fileIndex, filterText],
  );
  const folderTruncated =
    folderItems.length > 0 &&
    (folderItems.length < folderCount || indexTruncated);
  const fileTruncated =
    fileItems.length > 0 && (fileItems.length < fileCount || indexTruncated);

  const categories = useMemo(
    () =>
      buildMentionCategoryRows({
        counts: {
          team: agentItems.length,
          conversation: convCount,
          folder: folderCount,
          file: fileCount,
        },
        loadingFiles: indexLoading,
      }),
    [agentItems.length, convCount, folderCount, fileCount, indexLoading],
  );

  const sections = useMemo((): MentionMenuSection[] => {
    const show = (id: MentionSectionId) =>
      focusedSection === null || focusedSection === id;

    const out: MentionMenuSection[] = [];

    // 团队 count=0 不占位；钻入/类型前缀时才允许空态提示。
    if (show("team") && (agentItems.length > 0 || focusedSection === "team")) {
      out.push({
        id: "team",
        label: "团队",
        items: agentItems,
        emptyHint:
          agentItems.length === 0 ? "多 Agent 回合后可点名" : undefined,
      });
    }

    if (
      show("conversation") &&
      (convItems.length > 0 || focusedSection === "conversation")
    ) {
      out.push({
        id: "conversation",
        label: "对话",
        items: convItems,
        truncated: convItems.length > 0 && convItems.length < convCount,
        emptyHint: convItems.length === 0 ? "暂无其他对话" : undefined,
      });
    }

    if (show("folder")) {
      out.push({
        id: "folder",
        label: "文件夹",
        items: folderItems,
        truncated: folderTruncated,
        emptyHint:
          folderItems.length === 0 && focusedSection === "folder"
            ? "没有匹配的文件夹"
            : undefined,
      });
    }

    if (show("file")) {
      out.push({
        id: "file",
        label: "文件",
        items: fileItems,
        truncated: fileTruncated,
        emptyHint:
          fileItems.length === 0 && focusedSection === "file"
            ? "没有匹配的文件"
            : undefined,
      });
    }

    return out;
  }, [
    focusedSection,
    agentItems,
    convItems,
    convCount,
    folderItems,
    fileItems,
    folderTruncated,
    fileTruncated,
  ]);

  const flatItems = useMemo(() => sections.flatMap((s) => s.items), [sections]);

  // 一级：手打高亮团队、工具栏高亮附件；索引加载完成不抢高亮。
  // biome-ignore lint/correctness/useExhaustiveDependencies: categories read on level enter only
  useEffect(() => {
    if (!showCategoryLevel) return;
    setActiveIndex(
      categoryHighlightIndex(categories, highlightPrefRef.current),
    );
  }, [query, menuMode, showCategoryLevel]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: query/menuMode/sections are intentional re-run keys
  useEffect(() => {
    if (showCategoryLevel) return;
    setActiveIndex(0);
  }, [query, menuMode, flatItems.length, showCategoryLevel]);

  const ensureIndex = useCallback(async () => {
    if (indexLoadedRef.current) return;
    setIndexLoading(true);
    try {
      const sources = await buildMentionSources(conversationId);
      sourcesRef.current = new Map(sources.map((s) => [s.id, s]));
      const {
        files,
        dirs,
        sourceCount: count,
        truncated,
      } = await loadFileIndex(sources);
      setFileIndex(files);
      setDirIndex(dirs);
      setSourceCount(count);
      setIndexTruncated(Boolean(truncated));
      indexLoadedRef.current = true;
    } catch {
      setMenuError("读取文件列表失败");
    } finally {
      setIndexLoading(false);
    }
  }, [conversationId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: conversationId is an intentional re-run key
  useEffect(() => {
    indexLoadedRef.current = false;
    setFileIndex([]);
    setDirIndex([]);
    setSourceCount(0);
    setIndexTruncated(false);
    sourcesRef.current = new Map();
  }, [conversationId]);

  const closeMenu = useCallback(() => {
    setMenuMode(null);
    setMenuError(null);
    setActiveCategory(null);
    mentionRangeRef.current = null;
  }, []);

    /**
     * 回形针 / @ 本机文件在云端会话下的上传也前移到附加时——主进程已经把字节暂存好了，
     * 没道理等用户点发送才开始「读回字节 → PUT」。已有 workspacePath（区内引用或
     * 已写入 ``attachments/``）不必再来一趟。
     */
  const startCloudUpload = useCallback(
    (attachment: PendingAttachment) => {
      if (!conversationId) return;
      if (attachment.workspacePath || !attachment.stagingId) return;
      setAttachments((prev) =>
        prev.map((a) =>
          a.id === attachment.id
            ? { ...a, uploadState: "uploading" as const }
            : a,
        ),
      );
      void startStagedAttachmentUpload(conversationId, attachment).then(
        (res) => {
          setAttachments((prev) =>
            prev.map((a) => {
              if (a.id !== attachment.id) return a;
              if (!res.ok) {
                return {
                  ...a,
                  uploadState: "error" as const,
                  uploadError: res.reason,
                };
              }
              return {
                ...a,
                name: res.name,
                path: res.path,
                text: res.text,
                truncated: res.truncated,
                binary: res.binary,
                workspacePath: res.workspacePath,
                // 暂存字节已被取走，id 不再有效——留着只会让重启后的草稿发不出去。
                stagingId: undefined,
                uploadState: undefined,
                uploadError: undefined,
              };
            }),
          );
        },
      );
    },
    [conversationId, setAttachments],
  );

  const focusCaret = useCallback(
    (offset: number) => {
      bodyRef.current?.focus();
      bodyRef.current?.setCaret(offset);
      requestAnimationFrame(() => {
        bodyRef.current?.focus();
        bodyRef.current?.setCaret(offset);
      });
    },
    [bodyRef],
  );

  const consumeMentionQuery = useCallback((): {
    content: string;
    caret: number;
  } => {
    const range = mentionRangeRef.current;
    if (menuMode === "mention" && range) {
      return {
        content: value.slice(0, range.start) + value.slice(range.end),
        caret: range.start,
      };
    }
    return {
      content: value,
      caret: bodyRef.current?.getCaret() ?? value.length,
    };
  }, [menuMode, value, bodyRef]);

  const commitInline = useCallback(
    (kind: "A" | "M", index: number) => {
      const { content, caret } = consumeMentionQuery();
      const ins = insertInlineToken(content, caret, kind, index);
      setValue(ins.value);
      focusCaret(ins.caret);
      closeMenu();
    },
    [consumeMentionQuery, setValue, focusCaret, closeMenu],
  );

  const stripMentionQuery = useCallback(() => {
    const range = mentionRangeRef.current;
    if (menuMode === "mention" && range) {
      const updated = value.slice(0, range.start) + value.slice(range.end);
      setValue(updated);
      focusCaret(range.start);
    } else {
      bodyRef.current?.focus();
    }
  }, [menuMode, value, setValue, bodyRef, focusCaret]);

  const openMention = useCallback(
    (
      start: number,
      end: number,
      q: string,
      highlight: "team" | "attach" = "team",
    ) => {
      mentionRangeRef.current = { start, end };
      setQuery(q);
      if (menuMode !== "mention") {
        setActiveCategory(null);
        highlightPrefRef.current = highlight;
        logEvent("info", "mention.menu_open", { mode: "mention" });
      } else if (highlight === "attach") {
        highlightPrefRef.current = highlight;
      }
      setMenuMode("mention");
      setMenuError(null);
      void ensureIndex();
    },
    [ensureIndex, menuMode],
  );

  const clearActiveMention = useCallback(() => {
    stripMentionQuery();
    closeMenu();
  }, [stripMentionQuery, closeMenu]);

  /** 工具栏 @：插入 `@` 开菜单（高亮附件）；已在 @query 内不插第二个；菜单已开则关。 */
  const toggleAtMention = useCallback(() => {
    if (menuMode) {
      closeMenu();
      bodyRef.current?.focus();
      return;
    }
    const caret = bodyRef.current?.getCaret() ?? value.length;
    const existing = detectMention(value, caret);
    if (existing) {
      openMention(existing.start, caret, existing.query, "attach");
      bodyRef.current?.focus();
      return;
    }
    const needsSpace = caret > 0 && !/\s/.test(value[caret - 1] ?? "");
    const insert = `${needsSpace ? " " : ""}@`;
    const next = value.slice(0, caret) + insert + value.slice(caret);
    const atPos = caret + (needsSpace ? 1 : 0);
    setValue(next);
    openMention(atPos, atPos + 1, "", "attach");
    focusCaret(atPos + 1);
  }, [menuMode, closeMenu, value, setValue, openMention, bodyRef, focusCaret]);

  const openBrowse = useCallback(() => {
    if (menuMode === "browse") {
      closeMenu();
      return;
    }
    mentionRangeRef.current = null;
    setQuery("");
    setActiveCategory(null);
    setMenuMode("browse");
    setMenuError(null);
    logEvent("info", "mention.menu_open", { mode: "browse" });
    void ensureIndex();
    requestAnimationFrame(() => searchInputRef.current?.focus());
  }, [menuMode, closeMenu, ensureIndex]);

  const syncMention = useCallback(
    (text: string, caret: number) => {
      const m = detectMention(text, caret);
      if (m) {
        openMention(m.start, caret, m.query);
      } else if (menuMode === "mention") {
        closeMenu();
      }
    },
    [menuMode, openMention, closeMenu],
  );

  const selectAgent = useCallback(
    (agentId: string, role: string) => {
      if (agentMentions.some((a) => a.agentId === agentId)) {
        stripMentionQuery();
        closeMenu();
        return;
      }
      if (agentMentions.length >= MAX_AGENT_MENTIONS) {
        setMenuError(`最多点名 ${MAX_AGENT_MENTIONS} 个角色`);
        return;
      }
      const index = agentMentions.length;
      setAgentMentions((prev) => [
        ...prev,
        { id: crypto.randomUUID(), agentId, role },
      ]);
      logEvent("info", "mention.select", { category: "team" });
      commitInline("M", index);
    },
    [
      agentMentions,
      setAgentMentions,
      stripMentionQuery,
      closeMenu,
      commitInline,
    ],
  );

  const attachEntry = useCallback(
    async (entry: IndexedEntry) => {
      const key = `${entry.kind}:${entry.sourceId}:${entry.relPath}`;
      if (attachments.some((a) => a.key === key)) {
        stripMentionQuery();
        closeMenu();
        return;
      }

      let next: PendingAttachment | null = null;
      if (entry.kind === "conversation") {
        let win: Awaited<ReturnType<typeof fetchMessageWindow>>;
        try {
          win = await fetchMessageWindow(entry.relPath, {
            limit: CONV_MENTION_MSG_LIMIT,
          });
        } catch {
          setMenuError("读取对话失败");
          return;
        }
        const { text, truncated } = formatConversationContext(win.messages);
        if (!text) {
          setMenuError("该对话暂无可引用的内容");
          return;
        }
        next = {
          id: crypto.randomUUID(),
          key,
          name: entry.name,
          path: "对话",
          text,
          truncated: truncated || win.hasMoreBefore,
          kind: "conversation",
          conversationId: entry.relPath,
        };
      } else if (entry.kind === "dir") {
        const listing = buildDirListing(fileIndex, entry);
        if (listing.fileCount === 0) {
          setMenuError("该目录内没有可索引的文件");
          return;
        }
        next = {
          id: crypto.randomUUID(),
          key,
          name: entry.name,
          path: entry.display,
          text: listing.text,
          truncated: listing.truncated,
          kind: "dir",
        };
      } else {
        // 文件：区内引用原路径；区外才复制进 attachments/（含二进制 xlsx）。
        // 本地根 sourceId = ``local:<rootId>`` 或 ``local:<rootId>:<subpath>``。
        const localMatch = /^local:([^:]+)(?::(.*))?$/.exec(entry.sourceId);
        if (localMatch && hasLocalFiles()) {
          const rootId = localMatch[1];
          const subBase = (localMatch[2] || "").replace(/^\/+|\/+$/g, "");
          const containerRel = subBase
            ? `${subBase}/${entry.relPath}`.replace(/\/+/g, "/")
            : entry.relPath;
          const staged = await stageRootFileAttachment(
            conversationId,
            rootId,
            containerRel,
          );
          if (!staged.ok) {
            setMenuError(staged.reason);
            return;
          }
          next = {
            id: crypto.randomUUID(),
            key,
            name: staged.name,
            path: staged.path,
            text: staged.text,
            truncated: staged.truncated,
            kind: "file",
            workspacePath: staged.workspacePath,
            stagingId: staged.stagingId,
            binary: staged.binary,
            citedRootId: staged.citedRootId,
            citedRelPath: staged.citedRelPath,
          };
        } else {
          const source = sourcesRef.current.get(entry.sourceId);
          if (!source) {
            setMenuError("文件来源已失效，请重试");
            return;
          }
          let res: Awaited<ReturnType<FileSource["read"]>>;
          try {
            res = await source.read(entry.relPath);
          } catch {
            setMenuError("读取文件失败");
            return;
          }
          if (res.kind !== "text") {
            setMenuError(
              res.kind === "too-large"
                ? "文件过大，无法作为附件"
                : "图片或二进制请用 @ 附件或拖入附加（将驻留到工作区）",
            );
            return;
          }
          next = {
            id: crypto.randomUUID(),
            key,
            name: entry.name,
            path: entry.display,
            text: res.text,
            truncated: res.truncated,
            kind: "file",
            workspacePath: entry.relPath.replace(/\\/g, "/"),
          };
        }
      }

      const attachment = next;
      recordMentionRecent(entry);
      const category =
        entry.kind === "dir"
          ? "folder"
          : entry.kind === "conversation"
            ? "conversation"
            : "file";
      logEvent("info", "mention.select", { category });
      const index = attachments.length;
      setAttachments((prev) => [...prev, attachment]);
      startCloudUpload(attachment);

      if (!conversationId && onAttachmentFolderHint) {
        const resolved = resolveFolderFromIndexedEntry(entry);
        if (resolved) onAttachmentFolderHint(resolved);
      }

      commitInline("A", index);
    },
    [
      attachments,
      conversationId,
      fileIndex,
      closeMenu,
      onAttachmentFolderHint,
      setAttachments,
      startCloudUpload,
      stripMentionQuery,
      commitInline,
    ],
  );

  const selectItem = useCallback(
    (item: MentionMenuSelectable) => {
      if (isAgentItem(item)) {
        selectAgent(item.agentId, item.role);
        return;
      }
      void attachEntry(item);
    },
    [selectAgent, attachEntry],
  );

  const handleAddRoot = useCallback(async () => {
    const picked = await window.fsApi.addRoot();
    if (!picked.ok) return;
    indexLoadedRef.current = false;
    await ensureIndex();
  }, [ensureIndex]);

  /** 一级「附件」/ 菜单：从本机任选文件（含工作区外），主进程驻留。 */
  const pickLocalFile = useCallback(async () => {
    if (!hasLocalFiles()) {
      onBrowserFilePick?.();
      return;
    }
    setMenuError(null);
    const res = await pickLocalFileAttachment(conversationId);
    if (res === null) return;
    if (!res.ok) {
      setMenuError(res.reason);
      if (!menuMode) {
        setMenuMode("browse");
        mentionRangeRef.current = null;
      }
      return;
    }
    const key = `picked:${res.name}:${res.workspacePath ?? res.stagingId ?? res.name}`;
    if (attachments.some((a) => a.key === key)) {
      clearActiveMention();
      return;
    }
    const attachment: PendingAttachment = {
      id: crypto.randomUUID(),
      key,
      name: res.name,
      path: res.path,
      text: res.text,
      truncated: res.truncated,
      kind: "file",
      workspacePath: res.workspacePath,
      stagingId: res.stagingId,
      binary: res.binary,
      citedRootId: res.citedRootId,
      citedRelPath: res.citedRelPath,
    };
    const index = attachments.length;
    setAttachments((prev) => [...prev, attachment]);
    startCloudUpload(attachment);
    if (!conversationId && onAttachmentFolderHint) {
      const cited =
        res.citedRootId && res.citedRelPath
          ? resolveFolderFromCitedRoot(res.citedRootId, res.citedRelPath)
          : null;
      if (cited) onAttachmentFolderHint(cited);
    }
    logEvent("info", "mention.select", { category: "attach" });
    commitInline("A", index);
  }, [
    attachments,
    clearActiveMention,
    commitInline,
    conversationId,
    menuMode,
    onAttachmentFolderHint,
    onBrowserFilePick,
    setAttachments,
    startCloudUpload,
  ]);

  const setMenuQuery = useCallback(
    (q: string) => {
      if (menuMode === "browse" && q.trim()) setActiveCategory(null);
      setQuery(q);
    },
    [menuMode],
  );

  const drillCategory = useCallback((id: MentionSectionId) => {
    setActiveCategory(id);
    setActiveIndex(0);
    setMenuError(null);
  }, []);

  const goBack = useCallback(() => {
    if (activeCategory) {
      setActiveCategory(null);
      setActiveIndex(0);
      return;
    }
    if (!sectionFilter) return;
    if (menuMode === "mention") {
      const range = mentionRangeRef.current;
      const next = filterText;
      if (range) {
        const updated =
          value.slice(0, range.start + 1) + next + value.slice(range.end);
        setValue(updated);
        mentionRangeRef.current = {
          start: range.start,
          end: range.start + 1 + next.length,
        };
      }
      setQuery(next);
    } else {
      setQuery(filterText);
    }
    setActiveIndex(0);
  }, [activeCategory, sectionFilter, menuMode, filterText, value, setValue]);

  const canGoBack = focusedSection !== null && !showCategoryLevel;
  const canKeyBack = canGoBack && !filterText.trim();
  const focusedSectionLabel = focusedSection
    ? MENTION_CATEGORY_LABEL[focusedSection]
    : undefined;

  const handleMenuNavKey = useCallback(
    (e: KeyboardEvent): boolean => {
      if (!menuMode) return false;
      const action = mentionMenuKeyAction(e.key, {
        showCategoryLevel,
        categoryCount: categories.length,
        activeIndex,
        categoryDisabled: Boolean(categories[activeIndex]?.disabled),
        categoryAttach: categories[activeIndex]?.id === "attach",
        itemCount: flatItems.length,
        canKeyBack,
      });
      switch (action.type) {
        case "move":
          e.preventDefault();
          setActiveIndex(action.index);
          return true;
        case "attach":
          e.preventDefault();
          void pickLocalFile();
          return true;
        case "drill": {
          const row = categories[activeIndex];
          if (!row || row.disabled || !isMentionSectionId(row.id)) return true;
          e.preventDefault();
          drillCategory(row.id);
          return true;
        }
        case "back":
          e.preventDefault();
          goBack();
          return true;
        case "select":
          if (flatItems[activeIndex]) {
            e.preventDefault();
            selectItem(flatItems[activeIndex]);
            return true;
          }
          return false;
        case "close":
          e.preventDefault();
          closeMenu();
          bodyRef.current?.focus();
          return true;
        case "consume":
          e.preventDefault();
          return true;
        default:
          return false;
      }
    },
    [
      menuMode,
      showCategoryLevel,
      categories,
      activeIndex,
      flatItems,
      canKeyBack,
      drillCategory,
      goBack,
      selectItem,
      pickLocalFile,
      closeMenu,
      bodyRef,
    ],
  );

  return {
    menuMode,
    sections,
    flatItems,
    /** @deprecated 兼容旧调用；等同 flatItems */
    items: flatItems,
    activeIndex,
    indexLoading,
    menuError,
    query,
    sourceCount,
    indexLoadedRef,
    searchInputRef,
    showCategoryLevel,
    categories,
    canGoBack,
    focusedSectionLabel,
    setQuery: setMenuQuery,
    setActiveIndex,
    openBrowse,
    toggleAtMention,
    clearActiveMention,
    syncMention,
    attachEntry,
    selectItem,
    drillCategory,
    goBack,
    closeMenu,
    handleMenuNavKey,
    handleAddRoot,
    pickLocalFile,
  };
}
