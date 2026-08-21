import { getTokens } from "@/api/client";
import {
  type AlwaysQuota,
  type DocumentApplyMode,
  type DocumentNode,
  createRuleDocument,
  deleteDocument,
  getAlwaysQuota,
  getDocument,
  isDocumentsUnavailable,
  listScopeEntries,
  renameDocument,
  updateDocumentApplyMode,
  writeDocument,
} from "@/api/documents";
import {
  type MemoryKind,
  type MemoryUpdateFeedEntry,
  getMemoryFile,
  getMemoryTopic,
  isFeatureUnavailable,
  listMemoryUpdates,
  writeMemoryFile,
  writeMemoryTopic,
} from "@/api/memory";
// 全局设定 (/memory) — mobile form of the desktop file-rail「全局设定」after 三分
// 取消: flat GLOBAL entries + always-pool meter +「最近更新」. Not a memory-only
// half form. 偏好/画像 (incl. placeholders) keep the memory-files API; 主题/… keep
// the topics API; everything else is documents. No 纠错通道, no folder scope.
import {
  MEMORY_UPDATE_ACTION_META,
  visibleMemoryUpdateItems,
} from "@/lib/memoryUpdateDisplay";
import { ChevronLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "@/pages/more/more.css";

const APPLY_LABEL: Record<DocumentApplyMode, string> = {
  always: "常驻",
  on_demand: "按需",
};

const APPLY_HINT: Record<DocumentApplyMode, string> = {
  always: "每次对话都会带上",
  on_demand: "需要时再查阅",
};

const AI_CORE_NAMES = new Set(["偏好.md", "画像.md", "导航.md"]);
const NEAR_FULL_PERCENT = 80;
const ROW_CHARS_FLOOR = 1000;
/** Empty AI memory/topic hint — textarea placeholder only, never written into the body. */
const MEMORY_EMPTY_PLACEHOLDER =
  "AI 会把记得的内容写在这里，你也可以直接改或删除。";

type EntrySource =
  | { channel: "memory-file"; memoryKind: MemoryKind }
  | { channel: "memory-topic"; slug: string }
  | { channel: "document"; id: string };

type DisplayRow =
  | { kind: "doc"; doc: DocumentNode }
  | { kind: "placeholder"; name: string; applyMode: DocumentApplyMode };

function isAiCoreMemoryLeaf(
  doc: Pick<DocumentNode, "name" | "aiMaintained">,
): boolean {
  return doc.aiMaintained && AI_CORE_NAMES.has(doc.name);
}

function ensureMdName(name: string): string {
  return /\.(md|markdown)$/i.test(name) ? name : `${name}.md`;
}

function nextEntryName(existing: Iterable<string>): string {
  const taken = new Set(existing);
  const base = "新条目";
  if (!taken.has(`${base}.md`)) return `${base}.md`;
  for (let i = 2; ; i++) {
    const candidate = `${base} ${i}.md`;
    if (!taken.has(candidate)) return candidate;
  }
}

function mergeDisplayRows(docs: DocumentNode[]): DisplayRow[] {
  const present = new Set(docs.map((d) => d.name));
  const rows: DisplayRow[] = [
    ...docs.map((doc): DisplayRow => ({ kind: "doc", doc })),
    ...(["偏好.md", "画像.md"] as const)
      .filter((name) => !present.has(name))
      .map(
        (name): DisplayRow => ({
          kind: "placeholder",
          name,
          applyMode: "always",
        }),
      ),
  ];
  return rows.sort((a, b) => {
    const an = a.kind === "doc" ? a.doc.name : a.name;
    const bn = b.kind === "doc" ? b.doc.name : b.name;
    return an.localeCompare(bn, "zh");
  });
}

function entrySource(name: string, docId: string | null): EntrySource | null {
  if (name === "偏好.md")
    return { channel: "memory-file", memoryKind: "preferences" };
  if (name === "画像.md")
    return { channel: "memory-file", memoryKind: "profile" };
  const topic = /^主题\/(.+?)(?:\.md)?$/i.exec(name);
  if (topic?.[1]) return { channel: "memory-topic", slug: topic[1] };
  if (docId) return { channel: "document", id: docId };
  return null;
}

function formatRoughChars(n: number): string {
  const chars = Math.max(0, Math.round(n));
  if (chars === 0) return "0 字";
  if (chars < 1000) return "不足千字";
  if (chars < 9500) return `约 ${Math.max(1, Math.round(chars / 1000))} 千字`;
  const wan = Math.round(chars / 1000) / 10;
  const label = Number.isInteger(wan) ? String(wan) : wan.toFixed(1);
  return `约 ${label} 万字`;
}

function alwaysMeterTone(q: AlwaysQuota): "ok" | "near" | "over" {
  if (q.maxChars > 0 && q.usedChars > q.maxChars) return "over";
  if (q.usedChars <= 0) return "ok";
  if (q.percent >= NEAR_FULL_PERCENT) return "near";
  return "ok";
}

function glueCapacity(verb: "还剩" | "超出", amount: string): string {
  if (amount.startsWith("约 ")) return `${verb}${amount}`;
  if (amount === "0 字") return `${verb} ${amount}`;
  return `${verb}${amount}`;
}

/** Global-only meter line (desktop `formatMeterHeadline` · variant=global). */
function formatMeterHeadline(q: AlwaysQuota): string {
  const tone = alwaysMeterTone(q);
  if (tone === "over") {
    const overBy = Math.max(0, q.usedChars - q.maxChars);
    return `常驻 · 已满，${glueCapacity("超出", formatRoughChars(overBy))}`;
  }
  const remain = glueCapacity(
    "还剩",
    formatRoughChars(Math.max(0, q.maxChars - q.usedChars)),
  );
  if (tone === "near") return `常驻 · 快满了，${remain}`;
  return `常驻 · ${remain}`;
}

function entryDescription(doc: DocumentNode): string {
  return (doc.description ?? "").trim();
}

function entryFrontmatterError(doc: DocumentNode): string | null {
  const raw = doc.frontmatterError?.trim();
  return raw ? raw : null;
}

export function MemoryPage() {
  const navigate = useNavigate();
  const location = useLocation();

  const onAuthError = useCallback(
    (e: unknown) => {
      if (!getTokens()) navigate("/login", { replace: true });
      return e;
    },
    [navigate],
  );

  useEffect(() => {
    if (location.hash !== "#updates") return;
    const el = document.getElementById("memory-updates");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [location.hash]);

  return (
    <div className="screen">
      <header className="bar">
        <button
          type="button"
          className="link icon-btn"
          aria-label="返回"
          onClick={() => navigate("/files")}
        >
          <ChevronLeft size={20} />
        </button>
        <span className="bar-title">全局设定</span>
        <span className="bar-right" aria-hidden />
      </header>

      <div className="settings-body">
        <p className="settings-desc">
          短硬约束用常驻，厚知识用按需。你可以在这里查看、编辑或删除条目；AI
          也会从对话记下长期偏好与事实。
        </p>
        <AlwaysQuotaBlock onAuthError={onAuthError} />
        <RecentUpdates onAuthError={onAuthError} />
        <EntryList onAuthError={onAuthError} />
      </div>
    </div>
  );
}

function AlwaysQuotaBlock({
  onAuthError,
}: {
  onAuthError: (e: unknown) => unknown;
}) {
  const [quota, setQuota] = useState<AlwaysQuota | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setUnavailable(false);
    setFailed(false);
    getAlwaysQuota()
      .then(setQuota)
      .catch((e) => {
        onAuthError(e);
        if (isDocumentsUnavailable(e)) {
          setUnavailable(true);
          setQuota(null);
        } else {
          setFailed(true);
          setQuota(null);
        }
      });
  }, [onAuthError]);

  useEffect(() => {
    load();
  }, [load]);

  if (unavailable) return null;
  if (failed) {
    return (
      <button type="button" className="mem-quota-retry" onClick={load}>
        用量加载失败，点此重试
      </button>
    );
  }
  if (!quota) return null;

  const tone = alwaysMeterTone(quota);
  const headline = formatMeterHeadline(quota);
  if (tone === "ok") {
    return (
      <p className="mem-quota" title="每次对话都会带上">
        {headline}
      </p>
    );
  }

  return (
    <div className="mem-quota-need">
      <p className="mem-quota-headline">{headline}</p>
      <p className="mem-quota-hint">去整理</p>
    </div>
  );
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function RecentUpdates({
  onAuthError,
}: {
  onAuthError: (e: unknown) => unknown;
}) {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<MemoryUpdateFeedEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const load = useCallback(() => {
    setError(null);
    setUnavailable(false);
    listMemoryUpdates(30)
      .then((rows) =>
        setEntries(
          rows.filter((u) => {
            const visible = visibleMemoryUpdateItems(u.items);
            return (
              visible.length > 0 ||
              (u.kind === "quota" && Boolean((u.summary ?? "").trim()))
            );
          }),
        ),
      )
      .catch((e) => {
        onAuthError(e);
        if (isFeatureUnavailable(e)) {
          setUnavailable(true);
          setEntries([]);
        } else {
          setError("加载失败");
          setEntries([]);
        }
      });
  }, [onAuthError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="section" id="memory-updates">
      <h2 className="section-title">最近更新</h2>
      <p className="section-note">
        AI
        最近从各处对话里记下的内容。整理在后台异步进行，刚聊完可能稍晚才出现。
      </p>
      <div className="section-card mem-feed">
        {entries === null ? (
          <p className="section-note">加载中…</p>
        ) : unavailable ? (
          <p className="section-note">暂不可用（后端尚未部署此接口）</p>
        ) : error ? (
          <>
            <p className="error">{error}</p>
            <button type="button" className="btn-outline" onClick={load}>
              重试
            </button>
          </>
        ) : entries.length === 0 ? (
          <p className="section-note">
            还没有记忆更新。AI
            会在对话后台整理长期记忆；记下新内容时，这里会按时间列出。
          </p>
        ) : (
          <div className="mem-updates">
            {entries.map((entry) => {
              const visibleItems = visibleMemoryUpdateItems(entry.items);
              return (
                <div key={entry.id} className="mem-update">
                  <div className="mem-update-head">
                    <span className="mem-update-when mem-feed-when">
                      {formatWhen(entry.createdAt)}
                    </span>
                    {entry.kind === "quota" ? (
                      <span className="mem-update-title">常驻已满</span>
                    ) : null}
                    <button
                      type="button"
                      className="mem-update-link mem-feed-source"
                      onClick={() => navigate(`/c/${entry.conversationId}`)}
                    >
                      查看来源对话
                    </button>
                  </div>
                  {entry.kind === "quota" && entry.summary ? (
                    <p className="mem-item-text">{entry.summary}</p>
                  ) : null}
                  {visibleItems.length > 0 ? (
                    <ul className="mem-update-list">
                      {visibleItems.map((it, i) => {
                        const meta = MEMORY_UPDATE_ACTION_META[it.action];
                        const leaf = it.section
                          ? `${it.file} · ${it.section}`
                          : it.file;
                        const removed = it.action === "remove";
                        return (
                          <li
                            key={`${it.action}:${it.file}:${it.section}:${i}`}
                            className="mem-item"
                          >
                            <span className={`mem-action ${meta.cls}`}>
                              {meta.label}
                            </span>
                            <div className="mem-item-body">
                              <div className="mem-item-meta">
                                <span className="mem-item-leaf">{leaf}</span>
                                <span className="mem-item-scope">
                                  {it.scope === "project" ? "本文件夹" : "全局"}
                                </span>
                              </div>
                              {it.content && (
                                <p
                                  className={`mem-item-text${
                                    removed ? " mem-item-removed" : ""
                                  }`}
                                >
                                  {it.content}
                                </p>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

function EntryList({
  onAuthError,
}: {
  onAuthError: (e: unknown) => unknown;
}) {
  const [entries, setEntries] = useState<DocumentNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    setUnavailable(false);
    listScopeEntries(null)
      .then((rows) => setEntries(rows.filter((r) => r.kind === "document")))
      .catch((e) => {
        onAuthError(e);
        if (isDocumentsUnavailable(e)) {
          setUnavailable(true);
          setEntries([]);
        } else {
          setError("加载失败");
          setEntries([]);
        }
      });
  }, [onAuthError]);

  useEffect(() => {
    load();
  }, [load]);

  async function createEntry() {
    if (creating || unavailable) return;
    setCreating(true);
    setStatus(null);
    setError(null);
    try {
      const name = nextEntryName((entries ?? []).map((r) => r.name));
      const doc = await createRuleDocument(name);
      setEntries((prev) =>
        [...(prev ?? []).filter((r) => r.id !== doc.id), doc].sort((a, b) =>
          a.name.localeCompare(b.name, "zh"),
        ),
      );
      setCreatedId(doc.id);
      setStatus("已新建条目（默认常驻）");
    } catch (e) {
      onAuthError(e);
      if (isDocumentsUnavailable(e)) {
        setUnavailable(true);
        setError(null);
      } else {
        setError("新建条目失败，请重试");
      }
    } finally {
      setCreating(false);
    }
  }

  function patchEntry(id: string, next: DocumentNode) {
    setEntries((prev) =>
      (prev ?? [])
        .map((r) => (r.id === id ? next : r))
        .sort((a, b) => a.name.localeCompare(b.name, "zh")),
    );
  }

  function removeFromList(id: string) {
    setEntries((prev) => (prev ?? []).filter((r) => r.id !== id));
  }

  const rows = mergeDisplayRows(entries ?? []);
  const ready = entries !== null;

  return (
    <section className="section">
      <div className="rule-section-head">
        <h2 className="section-title">条目</h2>
        <button
          type="button"
          className="btn-outline"
          disabled={creating || unavailable}
          onClick={() => void createEntry()}
        >
          {creating ? "新建中…" : "新建条目"}
        </button>
      </div>
      <p className="section-note">点开可编辑。常驻 / 按需仅用户条目可切换。</p>
      <div className="section-card mem-entry-card">
        {!ready ? (
          <p className="section-note">加载中…</p>
        ) : (
          <>
            {unavailable && <p className="section-note">暂不可用</p>}
            {error && !unavailable && (
              <>
                <p className="error">{error}</p>
                {entries.length === 0 && (
                  <button type="button" className="btn-outline" onClick={load}>
                    重试
                  </button>
                )}
              </>
            )}
            {rows.map((row) =>
              row.kind === "doc" ? (
                <EntryItem
                  key={row.doc.id}
                  name={row.doc.name}
                  doc={row.doc}
                  initialOpen={row.doc.id === createdId}
                  onPatched={(next) => patchEntry(row.doc.id, next)}
                  onDeleted={() => removeFromList(row.doc.id)}
                  onStatus={setStatus}
                  onAuthError={onAuthError}
                />
              ) : (
                <EntryItem
                  key={`placeholder:${row.name}`}
                  name={row.name}
                  doc={null}
                  applyMode={row.applyMode}
                  onPatched={() => undefined}
                  onDeleted={() => undefined}
                  onStatus={setStatus}
                  onAuthError={onAuthError}
                />
              ),
            )}
          </>
        )}
        {status && <p className="section-note">{status}</p>}
      </div>
    </section>
  );
}

function EntryItem({
  name,
  doc,
  applyMode: placeholderMode = "always",
  initialOpen = false,
  onPatched,
  onDeleted,
  onStatus,
  onAuthError,
}: {
  name: string;
  doc: DocumentNode | null;
  applyMode?: DocumentApplyMode;
  initialOpen?: boolean;
  onPatched: (next: DocumentNode) => void;
  onDeleted: () => void;
  onStatus: (msg: string | null) => void;
  onAuthError: (e: unknown) => unknown;
}) {
  const source = useMemo(
    () => entrySource(name, doc?.id ?? null),
    [doc?.id, name],
  );
  const mode = doc?.applyMode ?? placeholderMode;
  const other: DocumentApplyMode = mode === "always" ? "on_demand" : "always";
  const fmError = doc ? entryFrontmatterError(doc) : null;
  const description = doc ? entryDescription(doc) : "";
  const canToggleApply = Boolean(doc && !doc.aiMaintained && !fmError);
  const canRename = Boolean(
    doc && !doc.aiMaintained && source?.channel === "document",
  );
  const canDelete = Boolean(
    doc && !isAiCoreMemoryLeaf(doc) && source?.channel !== "memory-file",
  );
  const alwaysChars = doc?.alwaysChars;
  const showAlwaysChars =
    mode === "always" &&
    doc?.disputedAt == null &&
    typeof alwaysChars === "number" &&
    Number.isFinite(alwaysChars) &&
    alwaysChars >= ROW_CHARS_FLOOR;

  const [open, setOpen] = useState(initialOpen);
  const [loaded, setLoaded] = useState(false);
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState("");
  const [version, setVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBody = useCallback(async () => {
    if (!source) {
      setError("无法打开此条目");
      return;
    }
    setError(null);
    try {
      if (source.channel === "memory-file") {
        const d = await getMemoryFile(source.memoryKind);
        setContent(d.content);
        setSaved(d.content);
        setVersion(d.version);
      } else if (source.channel === "memory-topic") {
        const d = await getMemoryTopic(source.slug);
        setContent(d.content);
        setSaved(d.content);
        setVersion(d.version);
      } else {
        const d = await getDocument(source.id);
        setContent(d.content);
        setSaved(d.content);
        setVersion(d.version);
        if (doc && (d.applyMode !== doc.applyMode || d.name !== doc.name)) {
          onPatched({ ...doc, applyMode: d.applyMode, name: d.name });
        }
      }
      setLoaded(true);
    } catch (e) {
      onAuthError(e);
      setError("加载失败");
    }
  }, [doc, onAuthError, onPatched, source]);

  // Created rows open once on mount; later expands go through expand().
  // biome-ignore lint/correctness/useExhaustiveDependencies: open-once on create
  useEffect(() => {
    if (!initialOpen) return;
    void loadBody();
  }, [initialOpen]);

  function expand() {
    setOpen((cur) => {
      const next = !cur;
      if (next && !loaded) void loadBody();
      return next;
    });
  }

  async function toggleApplyMode() {
    if (!doc || !canToggleApply || busy) return;
    setBusy(true);
    setError(null);
    onStatus(null);
    try {
      const next = await updateDocumentApplyMode(doc.id, other);
      onPatched(next);
      onStatus(`已设为${APPLY_LABEL[other]}`);
    } catch (e) {
      onAuthError(e);
      setError("切换失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function reloadLive() {
    if (!source) return;
    if (source.channel === "memory-file") {
      const live = await getMemoryFile(source.memoryKind);
      setContent(live.content);
      setSaved(live.content);
      setVersion(live.version);
      return;
    }
    if (source.channel === "memory-topic") {
      const live = await getMemoryTopic(source.slug);
      setContent(live.content);
      setSaved(live.content);
      setVersion(live.version);
      return;
    }
    const live = await getDocument(source.id);
    setContent(live.content);
    setSaved(live.content);
    setVersion(live.version);
  }

  async function save() {
    if (!source) return;
    setBusy(true);
    setError(null);
    try {
      const r =
        source.channel === "memory-file"
          ? await writeMemoryFile(source.memoryKind, content, version)
          : source.channel === "memory-topic"
            ? await writeMemoryTopic(source.slug, content, version)
            : await writeDocument(source.id, content, version);
      if (r.conflict) {
        await reloadLive();
        setError("内容已在别处更新，已为你刷新，请重新编辑后保存。");
        return;
      }
      setSaved(content);
      setVersion(r.version);
      const quota =
        "quotaWarning" in r && typeof r.quotaWarning === "string"
          ? r.quotaWarning.trim()
          : "";
      const frontmatter =
        "frontmatterError" in r && typeof r.frontmatterError === "string"
          ? r.frontmatterError.trim()
          : "";
      onStatus(quota || frontmatter || "已保存");
    } catch (e) {
      onAuthError(e);
      setError("保存失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function rename() {
    if (!doc || !canRename) return;
    const input = window.prompt("条目名称", doc.name);
    if (input === null) return;
    const nextName = ensureMdName(input.trim());
    if (nextName === ".md" || nextName === doc.name) return;
    setBusy(true);
    setError(null);
    try {
      const next = await renameDocument(doc.id, nextName);
      onPatched(next);
      onStatus("已重命名");
    } catch (e) {
      onAuthError(e);
      setError("重命名失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!doc || !canDelete || !source) return;
    if (!window.confirm(`确定删除「${doc.name}」？此操作不可撤销。`)) return;
    setBusy(true);
    setError(null);
    try {
      if (source.channel === "memory-topic") {
        const r = await writeMemoryTopic(source.slug, "", null);
        if (!r.ok) throw new Error("写入冲突");
      } else if (source.channel === "document") {
        const r = await deleteDocument(doc.id);
        if (!r.ok) throw new Error("删除冲突");
      } else {
        setBusy(false);
        return;
      }
      onDeleted();
      onStatus("已删除");
    } catch (e) {
      onAuthError(e);
      setError("删除失败，请重试");
      setBusy(false);
    }
  }

  const dirty = loaded && content !== saved;
  const leafNote =
    source?.channel === "memory-file"
      ? "留空并保存即清空。"
      : `${APPLY_LABEL[mode]} · ${APPLY_HINT[mode]}`;

  return (
    <div className="mem-topic">
      <div className="mem-entry-row">
        <button
          type="button"
          className="mem-topic-head mem-entry-main"
          aria-expanded={open}
          onClick={() => void expand()}
        >
          <span className="mem-entry-meta">
            <span className="mem-entry-title">
              <span className="mem-topic-name">{name}</span>
              {fmError ? (
                <span className="mem-entry-invalid">不生效</span>
              ) : null}
            </span>
            {fmError ? (
              <span className="mem-entry-desc">{fmError}</span>
            ) : description ? (
              <span className="mem-entry-desc">{description}</span>
            ) : null}
          </span>
          {showAlwaysChars ? (
            <span className="mem-entry-chars">
              {formatRoughChars(alwaysChars)}
            </span>
          ) : null}
          <span className="mem-topic-chevron" aria-hidden>
            {open ? "▾" : "›"}
          </span>
        </button>
        {canToggleApply ? (
          <button
            type="button"
            className="rule-apply-chip"
            title={`${APPLY_LABEL[mode]} · ${APPLY_HINT[mode]}（点击切换）`}
            aria-label={`生效方式：${APPLY_LABEL[mode]}，点击切换`}
            disabled={busy}
            onClick={() => void toggleApplyMode()}
          >
            {APPLY_LABEL[mode]}
          </button>
        ) : (
          <span className="rule-apply-chip" aria-hidden>
            {APPLY_LABEL[mode]}
          </span>
        )}
      </div>
      {open && (
        <div className="mem-topic-body">
          {!loaded && !error ? (
            <p className="section-note">加载中…</p>
          ) : (
            <>
              <p className="section-note">{leafNote}</p>
              <textarea
                className="mem-textarea"
                value={content}
                placeholder={
                  source?.channel === "memory-file" ||
                  source?.channel === "memory-topic"
                    ? MEMORY_EMPTY_PLACEHOLDER
                    : "（空）"
                }
                rows={6}
                onChange={(e) => setContent(e.target.value)}
              />
              {error && <p className="error">{error}</p>}
              <div className="field-actions">
                {canRename && (
                  <button
                    type="button"
                    className="btn-outline"
                    disabled={busy}
                    onClick={() => void rename()}
                  >
                    重命名
                  </button>
                )}
                {canDelete && (
                  <button
                    type="button"
                    className="btn-danger-outline"
                    disabled={busy}
                    onClick={() => void remove()}
                  >
                    删除
                  </button>
                )}
                <button
                  type="button"
                  disabled={!dirty || busy}
                  onClick={() => void save()}
                >
                  {busy ? "处理中…" : "保存"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {!open && error && <p className="error">{error}</p>}
    </div>
  );
}
