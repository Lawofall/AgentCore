import { getTokens } from "@/api/client";
import {
  type DownloadedFile,
  type FileNode,
  type WorkspaceListing,
  buildTree,
} from "@/api/workspace";
import { Markdown } from "@/components/Markdown";
import { Modal } from "@/components/Modal";
import {
  type EntryChange,
  FileEntryActions,
} from "@/components/fileBrowser/FileEntryActions";
import { FileTextEditor } from "@/components/fileBrowser/FileTextEditor";
import { NewFolderDialog } from "@/components/fileBrowser/NewFolderDialog";
import type { FileBrowserOps } from "@/components/fileBrowser/ops";
import { isInsideDir, joinPath } from "@/components/fileBrowser/paths";
import { FILE_NOT_IN_CLOUD_TREE } from "@/lib/fileDownloadError";
import { canShareFiles, downloadBlob, shareOrDownloadFile } from "@/lib/share";
import {
  AGENTCORE_ROOT_TOOLTIP,
  type PresentCrumb,
  canonicalBrowseDir,
  countDescendantFiles,
  displayDirName,
  isAgentCoreRootDir,
  matchesBrowseQuery,
  presentCrumbs,
  stageDirCaption,
  stageDirMeta,
  workroomChildren,
} from "@/lib/stageDirs";
// Shared workspace file browser (手机端布局重构 · 文件浏览复用).
//
// The crumbs + in-memory folder nav + full-screen previewer, extracted so both file surfaces
// reuse one implementation: the per-conversation files page (/c/:id/files, reached from a
// chat) and the 文件 tab's per-workspace browse (/files/:wsId). They differ only in addressing
// (conversation alias vs first-class workspace id) and the page chrome (header / back target /
// upload) — that stays in each page; the data source is injected as `source`.
//
// `cwd` is CONTROLLED by the parent so the page's header「上传」knows the target folder; the
// parent resets it to "" when the workspace changes and leaves it alone on an upload-triggered
// `reloadKey` bump (stay in the current folder after a write). The list endpoint only returns
// 顶层 or 整树, so the whole tree is fetched once (recursive) and walked in memory — one
// round-trip, instant folder nav (same as the original per-conversation browser).
import {
  ChevronRight,
  File,
  FileCode,
  FileJson,
  FileText,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  type LucideIcon,
  MoreVertical,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";
import {
  type TouchEvent as ReactTouchEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

/** The injected data source: how to list the tree and fetch one file's bytes. */
export interface FileBrowserSource {
  list: () => Promise<WorkspaceListing>;
  download: (path: string) => Promise<DownloadedFile>;
}

const IMAGE_EXT = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "bmp",
  "ico",
  "avif",
]);
const MARKDOWN_EXT = new Set(["md", "markdown"]);
const CODE_EXT = new Set([
  "js",
  "jsx",
  "ts",
  "tsx",
  "mjs",
  "cjs",
  "css",
  "scss",
  "less",
  "html",
  "htm",
  "xml",
  "yaml",
  "yml",
  "toml",
  "py",
  "rb",
  "go",
  "rs",
  "java",
  "kt",
  "c",
  "h",
  "cpp",
  "hpp",
  "cc",
  "sh",
  "bash",
  "zsh",
  "sql",
  "vue",
  "svelte",
  "php",
  "lua",
  "r",
  "dart",
  "swift",
  "scala",
  "pl",
  "ps1",
  "bat",
  "gradle",
]);
const TEXT_EXT = new Set([
  "txt",
  "md",
  "markdown",
  "json",
  "jsonl",
  "js",
  "jsx",
  "ts",
  "tsx",
  "mjs",
  "cjs",
  "css",
  "scss",
  "less",
  "html",
  "htm",
  "xml",
  "yaml",
  "yml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "csv",
  "tsv",
  "log",
  "py",
  "rb",
  "go",
  "rs",
  "java",
  "kt",
  "c",
  "h",
  "cpp",
  "hpp",
  "cc",
  "sh",
  "bash",
  "zsh",
  "sql",
  "env",
  "gitignore",
  "dockerfile",
  "makefile",
  "vue",
  "svelte",
  "php",
  "lua",
  "r",
  "dart",
  "swift",
  "scala",
  "pl",
  "ps1",
  "bat",
  "properties",
  "gradle",
]);
const TEXT_PREVIEW_MAX = 512 * 1024;
const CRUMB_COLLAPSE_AT = 4;
const PULL_THRESHOLD_PX = 64;

function ext(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : name.toLowerCase();
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/** Short Chinese relative time for list subtitles (e.g. 刚刚 / 昨天 / 3 天前). */
export function formatFileMtime(mtimeMs: number, nowMs = Date.now()): string {
  const sec = Math.max(0, Math.floor((nowMs - mtimeMs) / 1000));
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const d = new Date(mtimeMs);
  const now = new Date(nowMs);
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  if (days === 1) return "昨天";
  if (days > 1 && days < 30) return `${days} 天前`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function fileIcon(name: string, isDir: boolean): LucideIcon {
  if (isDir) return Folder;
  const e = ext(name);
  if (IMAGE_EXT.has(e)) return ImageIcon;
  if (MARKDOWN_EXT.has(e) || e === "txt" || e === "log") return FileText;
  if (e === "json" || e === "jsonl") return FileJson;
  if (CODE_EXT.has(e)) return FileCode;
  return File;
}

/** List-row subtitle: mtime only. Size stays on listing/meta and in the previewer. */
export function fileSubtitle(node: {
  mtimeMs?: number;
  sizeBytes?: number;
}): string | null {
  if (node.mtimeMs != null) return formatFileMtime(node.mtimeMs);
  return null;
}

type CrumbItem =
  | { kind: "seg"; crumb: PresentCrumb; last: boolean }
  | { kind: "ellipsis"; jumpPath: string; title: string };

/** Collapse deep crumbs: keep first + last two; middle → one 「…」. */
function collapseCrumbs(crumbs: PresentCrumb[]): CrumbItem[] {
  if (crumbs.length <= CRUMB_COLLAPSE_AT) {
    return crumbs.map((crumb, index) => ({
      kind: "seg" as const,
      crumb,
      last: index === crumbs.length - 1,
    }));
  }
  const first = crumbs[0];
  const penult = crumbs[crumbs.length - 2];
  const lastCrumb = crumbs[crumbs.length - 1];
  if (!first || !penult || !lastCrumb) return [];
  const collapsed = crumbs.slice(1, -2);
  const jump = crumbs[crumbs.length - 3] ?? first;
  return [
    { kind: "seg", crumb: first, last: false },
    {
      kind: "ellipsis",
      jumpPath: jump.path,
      title: collapsed.map((c) => c.label).join("/"),
    },
    { kind: "seg", crumb: penult, last: false },
    { kind: "seg", crumb: lastCrumb, last: true },
  ];
}

export function FileBrowser({
  source,
  cwd,
  onCwdChange,
  reloadKey = 0,
  emptyHint = "此文件夹还没有文件。",
  openPath = null,
  onUpload,
  ops,
}: {
  source: FileBrowserSource;
  cwd: string;
  onCwdChange: (cwd: string) => void;
  reloadKey?: number;
  emptyHint?: string;
  /** 深链：树加载后自动打开该路径文件的预览并落到其所在目录（聊天「本回合产出文件」卡的
   *  一键直达）。只消费一次；树里找不到则提示诚实缺文件，不再合成节点硬下（避免假 404）。 */
  openPath?: string | null;
  /** Optional empty-state CTA — typically opens the parent page's hidden upload input. */
  onUpload?: () => void;
  /** 写能力（改名/移动/删除/新建文件夹/编辑）。不给 = 只读浏览，一个写入口都不显示。 */
  ops?: FileBrowserOps;
}) {
  const navigate = useNavigate();
  const [tree, setTree] = useState<Map<string, FileNode[]> | null>(null);
  // 服务端条目上限吃掉了树的一部分。整树一次拉取时命中上限，深层文件会整片消失，
  // 界面却看不出少了东西——必须明说，不能让用户以为文件丢了。
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<FileNode | null>(null);
  const [openMissing, setOpenMissing] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [localReload, setLocalReload] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [pullDy, setPullDy] = useState(0);
  // 写动作：正在操作的条目、新建文件夹对话框、以及一行结果回执（成功/失败都要说出来）。
  const [acting, setActing] = useState<FileNode | null>(null);
  const [newFolder, setNewFolder] = useState(false);
  const [newFolderBusy, setNewFolderBusy] = useState(false);
  const [newFolderError, setNewFolderError] = useState<string | null>(null);
  const [opStatus, setOpStatus] = useState<string | null>(null);
  const openedRef = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);
  const pullStartY = useRef<number | null>(null);
  const pulling = useRef(false);

  // Blank the list only when the injected source identity changes (not on refresh/upload
  // reloadKey bumps — those keep the previous tree until the new listing arrives).
  // biome-ignore lint/correctness/useExhaustiveDependencies: source identity is the intentional reset trigger
  useEffect(() => {
    setTree(null);
    setTruncated(false);
    setError(null);
    setQuery("");
    setOpenMissing(null);
    setOpStatus(null);
    openedRef.current = false;
  }, [source]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey / localReload are intentional manual-reload triggers
  useEffect(() => {
    let cancelled = false;
    setError(null);
    setRefreshing(true);
    source
      .list()
      .then((listing) => {
        if (cancelled) return;
        setTree(buildTree(listing.entries));
        setTruncated(listing.truncated);
      })
      .catch((e) => {
        if (cancelled) return;
        // A cleared token → the api layer couldn't refresh; route to login (mirrors the
        // other pages' guard) rather than showing a load error.
        if (!getTokens()) {
          navigate("/login", { replace: true });
          return;
        }
        setError(e instanceof Error ? e.message : "加载文件列表失败");
        setTree(new Map());
        setTruncated(false);
      })
      .finally(() => {
        if (!cancelled) setRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [source, reloadKey, localReload, navigate]);

  // Clear cwd filter (and any stale write receipt) when navigating folders.
  // biome-ignore lint/correctness/useExhaustiveDependencies: cwd change is the intentional clear trigger
  useEffect(() => {
    setQuery("");
    setOpStatus(null);
  }, [cwd]);

  // 一键直达：树就绪后，把请求的文件落到其目录并打开预览。只跑一次（openedRef 守门），
  // 避免折回目录/二次打开。树里没有精确节点 → 诚实提示，禁止合成节点硬打下载。
  useEffect(() => {
    if (!openPath || openedRef.current || tree === null) return;
    openedRef.current = true;
    const slash = openPath.lastIndexOf("/");
    const dir = slash >= 0 ? openPath.slice(0, slash) : "";
    const node = (tree.get(dir) ?? []).find(
      (n) => !n.isDir && n.path === openPath,
    );
    if (dir) onCwdChange(canonicalBrowseDir(dir));
    if (node) {
      setOpenMissing(null);
      setViewing(node);
    } else {
      setOpenMissing(openPath);
    }
  }, [openPath, tree, onCwdChange]);

  const children = useMemo(
    () => (tree ? workroomChildren(tree, cwd) : []),
    [tree, cwd],
  );
  const filtered = useMemo(() => {
    if (!query.trim()) return children;
    return children.filter((n) => matchesBrowseQuery(n, query));
  }, [children, query]);
  const crumbs = presentCrumbs(cwd);
  const crumbItems = collapseCrumbs(crumbs);

  function refresh() {
    setLocalReload((k) => k + 1);
  }

  // A rename / move / delete landed: say what happened, drop a preview that now points at
  // a path which moved or is gone, and re-list so the tree matches the cloud again.
  function onEntryChange(change: EntryChange) {
    setActing(null);
    setOpStatus(change.message);
    if (viewing && isInsideDir(viewing.path, change.from)) setViewing(null);
    refresh();
  }

  async function onCreateFolder(name: string) {
    if (!ops) return;
    setNewFolderBusy(true);
    setNewFolderError(null);
    try {
      await ops.createDir(joinPath(canonicalBrowseDir(cwd), name));
      setNewFolder(false);
      setOpStatus(`已新建文件夹「${name}」`);
      refresh();
    } catch (e) {
      setNewFolderError(e instanceof Error ? e.message : "新建文件夹失败");
    } finally {
      setNewFolderBusy(false);
    }
  }

  function onTouchStart(e: ReactTouchEvent) {
    const el = listRef.current;
    if (!el || el.scrollTop > 0 || refreshing) return;
    pullStartY.current = e.touches[0]?.clientY ?? null;
    pulling.current = true;
  }

  function onTouchMove(e: ReactTouchEvent) {
    if (!pulling.current || pullStartY.current == null) return;
    const y = e.touches[0]?.clientY ?? pullStartY.current;
    const dy = Math.max(0, Math.min(96, y - pullStartY.current));
    setPullDy(dy);
  }

  function onTouchEnd() {
    if (!pulling.current) return;
    const should = pullDy >= PULL_THRESHOLD_PX;
    pulling.current = false;
    pullStartY.current = null;
    setPullDy(0);
    if (should) refresh();
  }

  const emptyRoot =
    tree !== null && cwd === "" && children.length === 0 && !error;
  const emptyFolder =
    tree !== null && cwd !== "" && children.length === 0 && !error;
  const emptyFilter =
    tree !== null && children.length > 0 && filtered.length === 0 && !error;

  return (
    <>
      {openMissing && (
        <p className="error hint" style={{ padding: "8px 16px", margin: 0 }}>
          打不开「{openMissing}」：{FILE_NOT_IN_CLOUD_TREE}
        </p>
      )}

      {tree !== null && (
        <div className="file-browser-toolbar">
          <div className="file-search">
            <Search size={16} className="file-search-icon" aria-hidden />
            <input
              type="search"
              className="file-search-input"
              placeholder="搜索当前目录"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              enterKeyHint="search"
              autoComplete="off"
            />
            {query && (
              <button
                type="button"
                className="file-search-clear"
                aria-label="清除搜索"
                onClick={() => setQuery("")}
              >
                ✕
              </button>
            )}
          </div>
          {ops && (
            <button
              type="button"
              className="file-refresh"
              aria-label="新建文件夹"
              onClick={() => {
                setNewFolderError(null);
                setNewFolder(true);
              }}
            >
              <FolderPlus size={16} aria-hidden />
            </button>
          )}
          <button
            type="button"
            className="file-refresh"
            aria-label="刷新"
            disabled={refreshing}
            onClick={refresh}
          >
            <RefreshCw
              size={16}
              className={refreshing ? "file-refresh-spin" : undefined}
              aria-hidden
            />
          </button>
        </div>
      )}

      {tree !== null && (
        <div className="crumbs">
          <button
            type="button"
            className="crumb"
            disabled={cwd === ""}
            onClick={() => onCwdChange("")}
          >
            根目录
          </button>
          {crumbItems.map((item) => {
            if (item.kind === "ellipsis") {
              return (
                <span key="crumb-ellipsis" className="crumb-seg">
                  <span className="crumb-sep">/</span>
                  <button
                    type="button"
                    className="crumb crumb-ellipsis"
                    title={item.title}
                    onClick={() => onCwdChange(item.jumpPath)}
                  >
                    …
                  </button>
                </span>
              );
            }
            return (
              <span key={item.crumb.path} className="crumb-seg">
                <span className="crumb-sep">/</span>
                <button
                  type="button"
                  className="crumb"
                  disabled={item.last}
                  onClick={() => onCwdChange(item.crumb.path)}
                >
                  {item.crumb.label}
                </button>
              </span>
            );
          })}
        </div>
      )}

      <div
        ref={listRef}
        className="list file-list"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
      >
        {(pullDy > 0 || (refreshing && tree !== null)) && (
          <div
            className="file-pull-hint"
            style={{
              height:
                refreshing && tree !== null ? 28 : Math.max(0, pullDy * 0.5),
            }}
          >
            {refreshing && tree !== null
              ? "刷新中…"
              : pullDy >= PULL_THRESHOLD_PX
                ? "松开刷新"
                : "下拉刷新"}
          </div>
        )}
        {tree === null && !error && <p className="muted hint">加载中…</p>}
        {emptyRoot && (
          <div className="file-empty">
            <p className="file-empty-title">{emptyHint}</p>
            {onUpload && (
              <button
                type="button"
                className="file-empty-cta"
                onClick={onUpload}
              >
                <Upload size={16} aria-hidden />
                上传文件
              </button>
            )}
          </div>
        )}
        {emptyFolder && (
          <div className="file-empty">
            <p className="file-empty-title">此文件夹是空的</p>
            {onUpload && (
              <button
                type="button"
                className="file-empty-cta"
                onClick={onUpload}
              >
                <Upload size={16} aria-hidden />
                上传到这里
              </button>
            )}
          </div>
        )}
        {emptyFilter && (
          <p className="muted hint">当前目录没有匹配「{query.trim()}」的项</p>
        )}
        {truncated && (
          <p className="muted hint">
            文件很多，本次只取回了一部分，深层文件可能还没显示出来。
          </p>
        )}
        {opStatus && <p className="muted hint">{opStatus}</p>}
        {filtered.map((node) => {
          const Icon = fileIcon(node.name, node.isDir);
          const shownName = displayDirName(node.path, node.name);
          const isWorkroom = isAgentCoreRootDir(node.path);
          const stage = node.isDir ? stageDirMeta(node.path) : null;
          const stageCaption =
            stage && tree
              ? stageDirCaption(
                  stage,
                  countDescendantFiles(node.path, (d) => tree.get(d)),
                )
              : null;
          // Stage badge wins for dirs; 抽屉行不挂 mtime（次要行，不跟用户文件抢元信息）。
          const subtitle =
            isWorkroom || stageCaption ? null : fileSubtitle(node);
          return (
            <div key={node.path} className="file-row-item">
              <button
                type="button"
                className="file-row"
                title={
                  stage?.tooltip ??
                  (isWorkroom ? AGENTCORE_ROOT_TOOLTIP : undefined)
                }
                onClick={() =>
                  node.isDir
                    ? onCwdChange(canonicalBrowseDir(node.path))
                    : setViewing(node)
                }
              >
                <span
                  className={`file-icon${node.isDir ? "" : " file-icon-doc"}`}
                  aria-hidden
                >
                  <Icon size={16} />
                </span>
                <span className="file-row-main">
                  <span
                    className={isWorkroom ? "file-name muted" : "file-name"}
                  >
                    {shownName}
                  </span>
                  {subtitle && <span className="file-sub">{subtitle}</span>}
                </span>
                {stageCaption && (
                  <span className="file-tag" title={stage?.tooltip}>
                    {stageCaption}
                  </span>
                )}
                {node.isDir && (
                  <span className="file-chevron" aria-hidden>
                    <ChevronRight size={18} />
                  </span>
                )}
              </button>
              {ops && (
                <button
                  type="button"
                  className="file-row-more"
                  aria-label={`${shownName} 的更多操作`}
                  onClick={() => {
                    setOpStatus(null);
                    setActing(node);
                  }}
                >
                  <MoreVertical size={18} aria-hidden />
                </button>
              )}
            </div>
          );
        })}
      </div>

      {error && <div className="error bar">{error}</div>}

      {ops && acting && tree && (
        <FileEntryActions
          entry={acting}
          ops={ops}
          tree={tree}
          onClose={() => setActing(null)}
          onDone={onEntryChange}
        />
      )}

      {ops && newFolder && (
        <NewFolderDialog
          parentLabel={
            cwd ? (presentCrumbs(cwd).at(-1)?.label ?? cwd) : "根目录"
          }
          busy={newFolderBusy}
          error={newFolderError}
          onClose={() => setNewFolder(false)}
          onCreate={(name) => void onCreateFolder(name)}
        />
      )}

      {viewing && (
        <FileViewer
          node={viewing}
          download={source.download}
          ops={ops}
          onSaved={refresh}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

type View =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "image"; url: string }
  | { kind: "markdown"; text: string }
  | { kind: "text"; text: string }
  | { kind: "binary"; size: number };

/** Full-screen preview for one file: Markdown reading view, text in a <pre>, images
 *  full-width, anything else a clear download-only notice. Bytes are fetched once via the
 *  injected `download`; 分享/下载 reuse them.
 *
 *  编辑 lives here rather than in the row menu: whether a file is editable text is decided
 *  by what actually came back (content type + size), not by guessing from its extension in
 *  a list — one judgement, made where the bytes are. */
function FileViewer({
  node,
  download,
  ops,
  onSaved,
  onClose,
}: {
  node: FileNode;
  download: (path: string) => Promise<DownloadedFile>;
  /** Present = the workspace is writable; enables 编辑 for text/Markdown. */
  ops?: Pick<FileBrowserOps, "readForEdit" | "writeText">;
  onSaved?: () => void;
  onClose: () => void;
}) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [file, setFile] = useState<DownloadedFile | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setView({ kind: "loading" });
    setFile(null);
    download(node.path)
      .then(async (f) => {
        if (cancelled) return;
        setFile(f);
        const e = ext(node.name);
        const isImage = f.contentType.startsWith("image/") || IMAGE_EXT.has(e);
        const isMarkdown = MARKDOWN_EXT.has(e);
        const isText =
          f.contentType.startsWith("text/") ||
          /json|javascript|xml|yaml|toml|csv/.test(f.contentType) ||
          TEXT_EXT.has(e);
        if (isImage) {
          objectUrl = URL.createObjectURL(f.blob);
          setView({ kind: "image", url: objectUrl });
        } else if (isText && f.blob.size <= TEXT_PREVIEW_MAX) {
          const text = await f.blob.text();
          if (cancelled) return;
          if (isMarkdown) setView({ kind: "markdown", text });
          else setView({ kind: "text", text });
        } else {
          setView({ kind: "binary", size: f.blob.size });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setView({
          kind: "error",
          message: err instanceof Error ? err.message : "下载失败",
        });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [node, download]);

  // Capability is stable for the session; compute once so the 分享 action only shows
  // where the OS sheet can actually take a file (else 下载 is the path).
  const sharable = canShareFiles();
  const editable = view.kind === "text" || view.kind === "markdown";

  function save() {
    if (file) downloadBlob(file.blob, file.filename);
  }

  async function share() {
    if (file)
      await shareOrDownloadFile(file.blob, file.filename, file.contentType);
  }

  return (
    <Modal className="viewer" onClose={onClose} label={node.name}>
      <header className="bar viewer-bar">
        <button type="button" className="link" onClick={onClose}>
          ← 文件
        </button>
        <span className="viewer-name" title={node.name}>
          {node.name}
        </span>
        <span className="bar-right viewer-actions">
          {ops && editable && (
            <button
              type="button"
              className="link"
              onClick={() => setEditing(true)}
            >
              编辑
            </button>
          )}
          {sharable && (
            <button
              type="button"
              className="link"
              onClick={() => void share()}
              disabled={!file}
            >
              分享
            </button>
          )}
          <button
            type="button"
            className="link"
            onClick={save}
            disabled={!file}
          >
            下载
          </button>
        </span>
      </header>
      <div
        className={`viewer-body${view.kind === "markdown" ? " viewer-body-md" : ""}`}
      >
        {view.kind === "loading" && <p className="muted hint">加载中…</p>}
        {view.kind === "error" && <p className="error hint">{view.message}</p>}
        {view.kind === "image" && (
          <img className="viewer-img" src={view.url} alt={node.name} />
        )}
        {view.kind === "markdown" && (
          <div className="viewer-md">
            <Markdown content={view.text} />
          </div>
        )}
        {view.kind === "text" && <pre className="viewer-text">{view.text}</pre>}
        {view.kind === "binary" && (
          <div className="file-empty">
            <p className="file-empty-title">无法预览此文件类型</p>
            <p className="muted hint">
              {formatFileSize(view.size)}
              。可用右上角「下载」或「分享」保存。
            </p>
          </div>
        )}
      </div>

      {ops && editing && (
        <FileTextEditor
          path={node.path}
          name={node.name}
          ops={ops}
          onClose={() => setEditing(false)}
          onSaved={(text) => {
            // Keep the preview showing what was just written instead of the bytes
            // fetched before the save (no second download).
            setView((v) =>
              v.kind === "markdown" || v.kind === "text" ? { ...v, text } : v,
            );
            onSaved?.();
          }}
        />
      )}
    </Modal>
  );
}
