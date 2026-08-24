import { Markdown } from "@/components/chat/Markdown";
import { FilePreviewBody } from "@/components/files/FilePreviewBody";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { Centered, InlineError } from "@/components/files/parts";
import { Button, IconButton } from "@/components/ui";
import { noticeChipNeutral } from "@/components/ui/tone-presets";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  type EditEncoding,
  type EditEol,
  type FilePreviewResult,
  type FileSource,
  type FileVersion,
  canOpenPathWithOsDefaultApp,
  isHtmlPath,
  isMarkdownPath,
} from "@/lib/fileSource";
import { notifyActionError, notifyError } from "@/lib/toast";
import { LocalFsError } from "@/services/sources/localRootSource";
import {
  AlertTriangle,
  AppWindow,
  ChevronLeft,
  Download,
  ExternalLink,
  FolderSearch,
  Globe,
  Loader2,
  Pencil,
  Save,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

/**
 * 一次编辑会话：进编辑时 `readForEdit` 拿到的全文 + 写前 CAS 基线 + 原文编码/换行。
 * 存在即「正在编辑」——没有基线就不可能进编辑态，故不会出现无基线的盲写。
 */
interface EditSession {
  /** 进入编辑（或上次成功保存）时的正文，脏标以此为准。 */
  baseText: string;
  version: FileVersion;
  encoding: EditEncoding;
  eol: EditEol;
}

/**
 * In-panel preview of one file from a {@link FileSource}, with opt-in editing for
 * whole text files. Takes over the files section (with a back arrow); a header
 * download button (when the source can transfer) pulls the raw file. Binary /
 * oversized files fall back to a download-only notice.
 *
 * 编辑走的是**带 CAS 的编辑契约**（`readForEdit` 取全文 + 版本基线 → `writeText` 写前比对），
 * 与 `MarkdownFileEditor` 同一套：同回合 Agent 正在写同一个文件时，保存返回冲突而不是盲
 * 覆盖，用户拿到「重新加载 / 仍然覆盖」的明确选择。故编辑入口门控 `readForEdit + writeText`
 * 而非裸 `writeBytes`（后者无基线，必然静默覆盖）。
 *
 * HTML 与其他文本文件一致显示源码（C+ 决策：面板内静态快照已取消，页面效果只在真浏览器
 * 环境呈现）。完整效果出口在标题栏，按能力显隐：「完整预览」（内置浏览器 tab）→
 * 「在浏览器打开」（系统浏览器）；web 无这两项时只剩下载。
 */
export function FilePreviewView({
  source,
  path,
  name,
  onClose,
}: {
  source: FileSource;
  path: string;
  name: string;
  onClose: () => void;
}) {
  const [result, setResult] = useState<FilePreviewResult | null>(null);
  const [error, setError] = useState(false);
  const [missing, setMissing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [openingInBrowser, setOpeningInBrowser] = useState(false);
  const [openingPreview, setOpeningPreview] = useState(false);
  const [edit, setEdit] = useState<EditSession | null>(null);
  const [opening, setOpening] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // 冲突未决时的**磁盘当前版本**：既是横幅的开关，也是「仍然覆盖」重写时的基线。
  const [conflictVersion, setConflictVersion] = useState<FileVersion | null>(
    null,
  );
  const [saveError, setSaveError] = useState<string | null>(null);
  const editing = edit !== null;
  const isHtml = isHtmlPath(name);
  const isMarkdown = isMarkdownPath(name);

  const load = useCallback(async () => {
    setResult(null);
    setError(false);
    setMissing(false);
    setEdit(null);
    setConflictVersion(null);
    setSaveError(null);
    try {
      setResult(await source.read(path));
    } catch (err) {
      const notFound =
        (err instanceof LocalFsError && err.code === "not_found") ||
        (typeof err === "object" &&
          err !== null &&
          "code" in err &&
          (err as { code: unknown }).code === "not_found");
      if (notFound) {
        setMissing(true);
      } else {
        console.error(
          `[FilePreview] source.read failed ${JSON.stringify({
            path,
            sourceId: source.id,
            error: err instanceof Error ? err.message : String(err),
          })}`,
        );
        setError(true);
      }
    }
  }, [source, path]);

  useEffect(() => {
    void load();
  }, [load]);

  const onDownload = async () => {
    if (downloading || !source.download) return;
    setDownloading(true);
    try {
      await source.download(path, name);
    } catch (e) {
      notifyActionError("下载失败", e);
    } finally {
      setDownloading(false);
    }
  };

  // 系统集成（reveal 仅本地源有；外部打开两源都有但云端过白名单谓词 → 按能力显隐，不按源分支）。
  const canOpenExternal = canOpenPathWithOsDefaultApp(source, path);
  const onReveal = async () => {
    try {
      await source.revealInOsFileManager?.(path);
    } catch (e) {
      notifyActionError("无法在资源管理器中显示", e);
    }
  };
  const onOpenExternal = async () => {
    try {
      await source.openWithOsDefaultApp?.(path);
    } catch (e) {
      notifyActionError("无法用默认程序打开", e);
    }
  };
  // 「在浏览器打开」完整效果（HTML）：本地直开磁盘文件；云端先取快照解压临时目录再开。
  // 云端要等快照 + 下载，故用 loading 态防重复点击。
  const onOpenInBrowser = async () => {
    if (openingInBrowser || !source.openInBrowser) return;
    setOpeningInBrowser(true);
    try {
      await source.openInBrowser(path);
    } catch (e) {
      notifyActionError("无法在浏览器打开", e);
    } finally {
      setOpeningInBrowser(false);
    }
  };
  // 应用内「完整预览」：右坞 BrowserPanel + workspace:// 代理工作区字节。
  // 云端源（对话侧栏 / hub `conv:` / hub `folder:`）在有能力位时挂 openInAppPreview。
  const onOpenInAppPreview = async () => {
    if (openingPreview || !source.openInAppPreview) return;
    setOpeningPreview(true);
    try {
      await source.openInAppPreview(path);
    } catch (e) {
      notifyActionError("无法打开完整预览", e);
    } finally {
      setOpeningPreview(false);
    }
  };

  // Editing is offered only for a whole text file on a source that can write it
  // back **with a baseline**: a truncated preview would drop its tail on save, so
  // oversized/binary stay read-only (download); a source without the CAS pair
  // could only ever clobber, so it doesn't get an edit affordance at all.
  const canEdit =
    result?.kind === "text" &&
    !result.truncated &&
    source.caps.edit &&
    !!source.readForEdit &&
    !!source.writeText;
  const dirty = edit !== null && draft !== edit.baseText;

  // 进/重进编辑：预览可能被截断或做过换行归一，故正文与基线都取自 `readForEdit`，
  // 保证保存写回的是「读到什么就写什么」。
  const openEditSession = useCallback(async () => {
    const readForEdit = source.readForEdit;
    if (opening || !readForEdit) return;
    setOpening(true);
    try {
      const doc = await readForEdit(path);
      if (doc.encoding === "gbk") {
        // GBK 回写未启用：不进编辑态，免得白编辑一场存不下（与 md 编辑器同一判定）。
        notifyError("此文件为 GBK 编码，暂不支持回写，只能只读查看");
        return;
      }
      setDraft(doc.text);
      setEdit({
        baseText: doc.text,
        version: doc.version,
        encoding: doc.encoding,
        eol: doc.eol,
      });
      setConflictVersion(null);
      setSaveError(null);
    } catch (e) {
      notifyActionError("无法打开编辑", e);
    } finally {
      setOpening(false);
    }
  }, [opening, source, path]);

  const startEdit = () => {
    if (canEdit) void openEditSession();
  };

  const onSave = useCallback(
    async (force = false) => {
      const writeText = source.writeText;
      if (saving || !edit || !writeText) return;
      setSaving(true);
      setSaveError(null);
      try {
        // 冲突后的「仍然覆盖」以**磁盘当前版本**为基线重写——明确的覆盖，不是盲写。
        const outcome = await writeText(path, {
          content: draft,
          encoding: edit.encoding,
          eol: edit.eol,
          baseline: force ? conflictVersion : edit.version,
        });
        if (outcome.ok) {
          setResult({ kind: "text", text: draft, truncated: false });
          setEdit(null);
          setConflictVersion(null);
        } else if (outcome.reason === "conflict") {
          setConflictVersion(outcome.version);
          setSaveError("磁盘上的文件已被改动");
        } else if (outcome.reason === "denied") {
          setSaveError("没有写入权限，无法保存");
        } else if (outcome.reason === "locked") {
          setSaveError("文件被其他程序占用，无法保存");
        } else {
          setSaveError(outcome.message ?? "保存失败");
        }
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving(false);
      }
    },
    [saving, source, path, draft, edit, conflictVersion],
  );

  // Confirm before discarding unsaved edits (back to list, cancel editing, or
  // taking the disk version on a conflict).
  const requestClose = () => {
    if (dirty && !window.confirm("有未保存的改动，确定放弃并返回？")) return;
    onClose();
  };
  const cancelEdit = () => {
    if (dirty && !window.confirm("有未保存的改动，确定放弃编辑？")) return;
    setEdit(null);
    setConflictVersion(null);
    setSaveError(null);
  };
  const reloadFromDisk = () => {
    if (dirty && !window.confirm("有未保存的改动，确定放弃并重新加载？"))
      return;
    void openEditSession();
  };

  // Ctrl/Cmd+S saves while editing (and swallows the browser's save dialog).
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void onSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, onSave]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border pl-1 pr-1">
        <SimpleTooltip label="返回文件列表">
          <IconButton onClick={requestClose} aria-label="返回文件列表">
            <ChevronLeft size={16} />
          </IconButton>
        </SimpleTooltip>
        <FileTypeIcon name={name} path={path} size={13} />
        <SimpleTooltip label={path}>
          <span className="min-w-0 flex-1 truncate text-xs font-medium">
            {dirty && <span className="text-primary">● </span>}
            {name}
          </span>
        </SimpleTooltip>
        {editing ? (
          <>
            <Button
              className="shrink-0 disabled:opacity-60"
              disabled={saving}
              onClick={() => void onSave()}
              icon={
                saving ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Save size={13} />
                )
              }
            >
              保存
            </Button>
            <SimpleTooltip label="取消编辑">
              <IconButton onClick={cancelEdit} aria-label="取消编辑">
                <X size={14} />
              </IconButton>
            </SimpleTooltip>
          </>
        ) : (
          <>
            {canEdit && (
              <SimpleTooltip label="编辑">
                <IconButton
                  disabled={opening}
                  onClick={startEdit}
                  aria-label="编辑"
                >
                  {opening ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Pencil size={14} />
                  )}
                </IconButton>
              </SimpleTooltip>
            )}
            {isHtml && source.openInAppPreview && (
              <SimpleTooltip label="完整预览（内置浏览器 · 跑 JS）">
                <IconButton
                  disabled={openingPreview}
                  onClick={() => void onOpenInAppPreview()}
                  aria-label="完整预览"
                >
                  {openingPreview ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <AppWindow size={14} />
                  )}
                </IconButton>
              </SimpleTooltip>
            )}
            {isHtml && source.openInBrowser && (
              <SimpleTooltip label="在浏览器打开（完整效果）">
                <IconButton
                  disabled={openingInBrowser}
                  onClick={() => void onOpenInBrowser()}
                  aria-label="在浏览器打开"
                >
                  {openingInBrowser ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Globe size={14} />
                  )}
                </IconButton>
              </SimpleTooltip>
            )}
            {!isHtml && canOpenExternal && (
              <SimpleTooltip label="用默认程序打开">
                <IconButton
                  onClick={() => void onOpenExternal()}
                  aria-label="用默认程序打开"
                >
                  <ExternalLink size={14} />
                </IconButton>
              </SimpleTooltip>
            )}
            {source.revealInOsFileManager && (
              <SimpleTooltip label="在资源管理器中显示">
                <IconButton
                  onClick={() => void onReveal()}
                  aria-label="在资源管理器中显示"
                >
                  <FolderSearch size={14} />
                </IconButton>
              </SimpleTooltip>
            )}
            {source.download && (
              <SimpleTooltip label="下载文件">
                <IconButton
                  disabled={downloading}
                  onClick={() => void onDownload()}
                  aria-label="下载文件"
                >
                  {downloading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Download size={14} />
                  )}
                </IconButton>
              </SimpleTooltip>
            )}
          </>
        )}
      </div>

      {/* 冲突三态与 md 编辑器同一套语汇：说明「会覆盖」+ 两个明确出口，绝不静默落盘。 */}
      {conflictVersion && (
        <div className="flex flex-wrap shrink-0 items-center gap-2 border-b border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-foreground">
          <AlertTriangle size={14} className="shrink-0 text-primary" />
          <span>磁盘上的文件已被改动，保存会覆盖磁盘版本。</span>
          <Button
            variant="ghost"
            onClick={reloadFromDisk}
            className="h-auto px-0 py-0 underline-offset-2 hover:underline"
          >
            重新加载
          </Button>
          <Button
            variant="danger"
            disabled={saving}
            onClick={() => void onSave(true)}
            className="h-auto px-0 py-0 underline-offset-2 hover:underline"
          >
            仍然覆盖
          </Button>
        </div>
      )}

      {saveError && !conflictVersion && (
        <div
          className={`flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-xs ${noticeChipNeutral}`}
        >
          <AlertTriangle size={14} className="shrink-0 text-muted-foreground" />
          <span>{saveError}</span>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="block h-full w-full resize-none border-0 bg-transparent px-3 py-2 font-mono text-xs leading-relaxed text-foreground outline-none"
          />
        ) : error ? (
          <InlineError onRetry={() => void load()} />
        ) : missing ? (
          <Centered>
            <p className="text-xs text-muted-foreground">文件不存在</p>
          </Centered>
        ) : result === null ? (
          <Centered>
            <Loader2
              size={18}
              className="animate-spin text-muted-foreground/50"
            />
          </Centered>
        ) : isMarkdown && result.kind === "text" && !result.truncated ? (
          // 阅读优先：md 默认渲染预览（复用聊天渲染器）。截断的 md 会渲染不全 →
          // 回落源码 + FilePreviewBody 的截断提示。
          <div className="mx-auto max-w-3xl px-6 py-6">
            <Markdown content={result.text} fileSource={source} />
          </div>
        ) : (
          <FilePreviewBody
            result={result}
            name={name}
            onOpenWithOsDefaultApp={
              canOpenExternal ? () => void onOpenExternal() : undefined
            }
            onDownload={source.download ? () => void onDownload() : undefined}
          />
        )}
      </div>
    </div>
  );
}
