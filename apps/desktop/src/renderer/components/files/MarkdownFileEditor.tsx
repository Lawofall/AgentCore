/**
 * `.md` 文件的编辑宿主：把源无关的 {@link MarkdownSourceEditor} 接到 {@link FileSource}
 * 的编辑契约（`readForEdit` / `writeText`），让 AI 产物从只读升级为可编辑文档。
 *
 * - 读：`source.readForEdit`（完整正文 + 基线版本/编码/换行）
 * - 写：`source.writeText`（带 baseline 写前 CAS——磁盘/远端被改即冲突，绝不盲覆盖）
 * - 视图：编辑（CodeMirror 源码）↔ 预览（复用聊天 {@link Markdown} 渲染器）切换。**默认进
 *   预览**（阅读优先：点开文档先看渲染内容，编辑为次级动作）。窄侧栏下用「可切视图」而非
 *   并排 split，避免两栏都挤（见 `desktop-layout.mdc`）。
 * - 保存：防抖自动保存 + `Ctrl/Cmd+S` 即存 + 离开/退出冲刷未保存内容。
 *
 * 字节忠实——文本即典范，绝不隐式 reflow，故无「富文本无损往返」自检与源码降级，任何
 * `.md` 都能直接编辑。宿主只认 FileSource 接口，不分支本地/云端。
 */

import { Markdown } from "@/components/chat/Markdown";
import {
  MarkdownSourceEditor,
  type MarkdownSourceEditorHandle,
} from "@/components/markdown/MarkdownSourceEditor";
import type { SelectionContext } from "@/components/markdown/aiRewrite";
import { SourceToolbar } from "@/components/markdown/sourceToolbar";
import { Button, IconButton } from "@/components/ui";
import { noticeChipNeutral } from "@/components/ui/tone-presets";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type {
  EditEncoding,
  EditEol,
  FileSource,
  FileVersion,
} from "@/lib/fileSource";
import { canOpenPathWithOsDefaultApp } from "@/lib/fileSource";
import { notifyActionError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import { rewriteSelection } from "@/services/rewrite";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ExternalLink,
  Eye,
  FileText,
  FolderSearch,
  Loader2,
  PencilLine,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

type SaveState = "idle" | "saving" | "saved" | "error";
type ViewMode = "edit" | "preview";

const AUTOSAVE_DEBOUNCE_MS = 1500; // 停止输入后多久自动落盘

/** 记忆源空预览的固定说明——只出现在编辑器空状态，不写入 md。 */
const MEMORY_PREVIEW_EMPTY_HINT =
  "AI 会把记得的内容写在这里，你也可以直接改或删除。";

/** 退役的人面 H1；导航等其它标题禁止按此剥。 */
const RETIRED_MEMORY_H1 = /^#\s+用户记忆\s*$/;

/**
 * 预览专用：剥掉存量「# 用户记忆」+ 紧随的说明引用块。不泛剥其它 H1（导航一句话
 * 定位要留）。不改 `content`、不标脏、不触发保存——编辑态仍是原文。
 */
function stripRetiredUserMemoryChrome(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i++;
  if (i >= lines.length || !RETIRED_MEMORY_H1.test(lines[i])) {
    return markdown;
  }
  i += 1;
  while (
    i < lines.length &&
    (!lines[i].trim() || lines[i].trimStart().startsWith(">"))
  ) {
    i += 1;
  }
  return lines.slice(i).join("\n").trim();
}

interface Baseline {
  version: FileVersion;
  encoding: EditEncoding;
  eol: EditEol;
}

export function MarkdownFileEditor({
  source,
  path,
  name,
  onClose,
  embedded,
}: {
  source: FileSource;
  path: string;
  name: string;
  onClose: () => void;
  /** Hosted inside a larger shell (e.g. the 全局+本项目 split) that owns the single 返回
   * control and tab chrome — so suppress this editor's own back button. Everything else
   * (脏标 / 保存 / AI 改写 / 编辑·预览) stays, since each pane edits its own file. */
  embedded?: boolean;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("preview");
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [editorKey, setEditorKey] = useState(0);
  // AI 改写：指令输入条是否展开 / 指令文本 / 调用在途 / 评审态 / 错误。
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  // 改写加载 + 评审期间暂停自动保存/冲刷（别把未定稿的 diff 写盘）；触发时捕获的选区上下文。
  const aiActiveRef = useRef(false);
  const aiTargetRef = useRef<SelectionContext | null>(null);

  const baselineRef = useRef<Baseline>({
    version: {},
    encoding: "utf-8",
    eol: "lf",
  });
  const conflictVersionRef = useRef<FileVersion>({});
  const editorRef = useRef<MarkdownSourceEditorHandle>(null);
  const savingRef = useRef(false); // 写盘 in-flight 锁：连按保存时丢弃并发触发，避免假冲突
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushRef = useRef<() => void>(() => {}); // 离开/退出前冲刷未保存内容（持最新闭包）
  // 最近一次编辑器正文：预览态 / 切文件时编辑器已卸（editorRef 置空），取不到 getValue()，靠此兜底。
  const latestMdRef = useRef<string | null>(null);

  const load = useCallback(() => {
    setContent(null);
    setLoadError(null);
    setDirty(false);
    setSaveState("idle");
    setSaveError(null);
    setConflict(false);
    setReadOnly(false);
    if (!source.readForEdit) {
      setLoadError("该文件源不支持编辑");
      return;
    }
    source
      .readForEdit(path)
      .then((doc) => {
        baselineRef.current = {
          version: doc.version,
          encoding: doc.encoding,
          eol: doc.eol,
        };
        latestMdRef.current = doc.text;
        setReadOnly(doc.encoding === "gbk"); // GBK 回写未启用：只读打开，避免白编辑后存不下
        setContent(doc.text);
        setMode("preview"); // 阅读优先：点开默认渲染预览，编辑为次级动作
        setEditorKey((k) => k + 1);
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : String(e)),
      );
  }, [source, path]);

  useEffect(() => {
    load();
  }, [load]);

  // 「已保存」提示短暂展示后淡出
  useEffect(() => {
    if (saveState !== "saved") return;
    const id = setTimeout(
      () => setSaveState((s) => (s === "saved" ? "idle" : s)),
      2500,
    );
    return () => clearTimeout(id);
  }, [saveState]);

  const doSave = useCallback(
    async (force = false) => {
      if (savingRef.current) return; // 已有写盘在途：丢弃重复触发
      const base = baselineRef.current;
      if (base.encoding === "gbk") return; // GBK 只读：不回写
      if (!source.writeText) return;
      const md = editorRef.current?.getValue() ?? latestMdRef.current; // 编辑器已卸时回退
      if (md == null) return;
      if (autosaveTimerRef.current) {
        clearTimeout(autosaveTimerRef.current); // 手动/即时保存时取消待触发的自动保存
        autosaveTimerRef.current = null;
      }
      savingRef.current = true;
      setSaveState("saving");
      setSaveError(null);
      try {
        const result = await source.writeText(path, {
          content: md,
          encoding: base.encoding,
          eol: base.eol,
          baseline: force ? conflictVersionRef.current : base.version,
        });
        if (result.ok) {
          baselineRef.current = { ...base, version: result.version };
          setContent(md);
          setDirty(false);
          setConflict(false);
          setSaveState("saved");
        } else if (result.reason === "conflict") {
          conflictVersionRef.current = result.version;
          setConflict(true);
          setSaveState("error");
          setSaveError("磁盘上的文件已被改动");
        } else if (result.reason === "denied") {
          setSaveState("error");
          setSaveError("没有写入权限，无法保存");
        } else if (result.reason === "locked") {
          setSaveState("error");
          setSaveError("文件被其他程序占用，无法保存");
        } else {
          setSaveState("error");
          setSaveError(result.message ?? "保存失败");
        }
      } catch (e) {
        setSaveState("error");
        setSaveError(e instanceof Error ? e.message : String(e));
      } finally {
        savingRef.current = false;
      }
    },
    [source, path],
  );

  // 冲刷闭包随脏标记/路径更新：离开或退出时拿最新正文，fire-and-forget 落盘（不再 setState）。
  // 带 baseline CAS——磁盘被外部改动只会冲突而不写，不会覆盖他人改动。
  useEffect(() => {
    flushRef.current = () => {
      // AI 评审进行中：未定稿的 diff 不冲刷写盘（离开即丢弃这次改写）。
      if (
        savingRef.current ||
        !dirty ||
        readOnly ||
        conflict ||
        aiActiveRef.current
      )
        return;
      const base = baselineRef.current;
      if (base.encoding === "gbk" || !source.writeText) return;
      const md = editorRef.current?.getValue() ?? latestMdRef.current;
      if (md == null) return;
      void source.writeText(path, {
        content: md,
        encoding: base.encoding,
        eol: base.eol,
        baseline: base.version,
      });
    };
  }, [dirty, readOnly, conflict, source, path]);

  // 切文件按 key=path 重挂本组件 → 卸载即「离开当前文件」，冲刷未保存内容防静默丢失。
  // 仅卸载时冲刷一次；flushRef 持最新闭包，故依赖为空。
  useEffect(
    () => () => {
      if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
      flushRef.current();
    },
    [],
  );

  // 退出/刷新前尽力冲刷（桌面端关窗=隐藏到托盘不触发卸载；仅真正退出走到这里，异步写为尽力而为）。
  useEffect(() => {
    const onBeforeUnload = () => flushRef.current();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  const switchMode = useCallback(
    (next: ViewMode) => {
      if (reviewing) return; // 评审期切到预览会卸载编辑器、丢失 diff 状态——禁用
      setAiOpen(false); // 切视图即放弃未提交的改写指令（选区偏移会失效）
      setMode((prev) => {
        if (prev === next) return prev;
        if (prev === "edit") {
          // 离开编辑：把当前正文同步进预览（编辑器已卸时回退最近正文），未保存状态保留
          const md = editorRef.current?.getValue() ?? latestMdRef.current;
          if (md != null) setContent(md);
        }
        return next;
      });
    },
    [reviewing],
  );

  // 展开 AI 改写指令条：先确认有选区（无选区直接提示，不打开空条）。
  const openRewrite = useCallback(() => {
    const ctx = editorRef.current?.getSelectionContext();
    if (!ctx) {
      setAiError("请先选中要改写的文本");
      return;
    }
    aiTargetRef.current = ctx;
    setAiError(null);
    setAiInstruction("");
    setAiOpen(true);
  }, []);

  // 提交改写：调后端 → 用返回文本替换选区并进入 diff 评审。期间暂停自动保存。
  const submitRewrite = useCallback(async () => {
    const ctx = aiTargetRef.current;
    const instruction = aiInstruction.trim();
    if (!ctx || !instruction || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    aiActiveRef.current = true;
    try {
      const rewritten = await rewriteSelection({
        selection: ctx.selection,
        instruction,
        contextBefore: ctx.contextBefore,
        contextAfter: ctx.contextAfter,
      });
      const ok = editorRef.current?.startRewriteReview(
        { from: ctx.from, to: ctx.to, selection: ctx.selection },
        rewritten,
      );
      if (!ok) {
        aiActiveRef.current = false;
        setAiError("选区已改变，请重新选择后再试");
        return;
      }
      setAiOpen(false);
      setReviewing(true);
    } catch (e) {
      aiActiveRef.current = false;
      setAiError(
        e instanceof ApiError
          ? (e.serverMessage ?? e.message)
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setAiBusy(false);
    }
  }, [aiInstruction, aiBusy]);

  // 结束评审：accept 保留当前（逐块决策后的）正文，reject 整体还原。结束后恢复自动保存。
  const finishReview = useCallback(
    (accept: boolean) => {
      editorRef.current?.endRewriteReview(accept);
      aiActiveRef.current = false;
      setReviewing(false);
      const v = editorRef.current?.getValue() ?? latestMdRef.current;
      if (v == null) return;
      latestMdRef.current = v;
      if (v !== content) {
        setDirty(true);
        setSaveState("idle");
        if (!conflict && !readOnly) {
          if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
          autosaveTimerRef.current = setTimeout(
            () => void doSave(),
            AUTOSAVE_DEBOUNCE_MS,
          );
        }
      } else {
        setDirty(false);
      }
    },
    [content, conflict, readOnly, doSave],
  );

  // 系统集成（reveal 仅本地源有；外部打开两源都有但云端过白名单谓词 → 按能力显隐，不按源分支）。
  const canOpenExternal = canOpenPathWithOsDefaultApp(source, path);
  const isMemorySource = source.id === "memory";
  const previewBody =
    content == null || !isMemorySource
      ? content
      : stripRetiredUserMemoryChrome(content);
  const memoryPreviewEmpty =
    isMemorySource && previewBody !== null && previewBody.trim() === "";
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

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border pl-1 pr-1.5">
        {!embedded && (
          <SimpleTooltip label="返回文件列表">
            <IconButton onClick={onClose} aria-label="返回文件列表">
              <ChevronLeft size={16} />
            </IconButton>
          </SimpleTooltip>
        )}
        <FileText size={13} className="shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {dirty && <span className="text-primary">● </span>}
          {name}
        </span>
        {saveState === "saving" && (
          <span className="shrink-0 text-xs text-muted-foreground">
            保存中…
          </span>
        )}
        {saveState === "saved" && !dirty && (
          <span className="shrink-0 text-xs text-muted-foreground">已保存</span>
        )}
        {canOpenExternal && (
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
        {mode === "edit" && !readOnly && !reviewing && !aiOpen && (
          <Button
            variant="neutral"
            className="shrink-0 border border-border"
            onClick={openRewrite}
            title="用 AI 改写选中的文本"
            icon={<Sparkles size={13} />}
          >
            AI 改写
          </Button>
        )}
        {mode === "edit" && !readOnly && !reviewing && (
          <Button
            className="shrink-0 disabled:opacity-50"
            disabled={!dirty || saveState === "saving"}
            onClick={() => void doSave()}
            icon={
              saveState === "saving" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Save size={13} />
              )
            }
          >
            保存
          </Button>
        )}
        {!reviewing && (
          <div className="flex shrink-0 items-center overflow-hidden rounded-lg border border-border">
            {(
              [
                { key: "edit", label: "编辑", Icon: PencilLine },
                { key: "preview", label: "预览", Icon: Eye },
              ] as const
            ).map(({ key, label, Icon }) => (
              <Button
                key={key}
                variant="ghost"
                onClick={() => switchMode(key)}
                className={cn(
                  "h-7 rounded-none px-2",
                  mode === key
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/60",
                )}
                icon={<Icon size={13} />}
              >
                {label}
              </Button>
            ))}
          </div>
        )}
      </div>

      {aiOpen && (
        <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/30 px-3 py-1.5">
          <Sparkles size={14} className="shrink-0 text-primary" />
          <input
            // biome-ignore lint/a11y/noAutofocus: 指令条按需展开，聚焦输入即其唯一用途
            autoFocus
            value={aiInstruction}
            onChange={(e) => setAiInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submitRewrite();
              } else if (e.key === "Escape") {
                e.preventDefault();
                setAiOpen(false);
              }
            }}
            placeholder="想怎么改这段？如：更正式、更简洁、改写成要点"
            disabled={aiBusy}
            className="h-7 min-w-0 flex-1 rounded-lg border border-input bg-background px-2 text-xs text-foreground outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
          />
          <Button
            className="shrink-0 disabled:opacity-50"
            disabled={aiBusy || !aiInstruction.trim()}
            onClick={() => void submitRewrite()}
            icon={
              aiBusy ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Sparkles size={13} />
              )
            }
          >
            改写
          </Button>
          <Button
            variant="neutral"
            className="shrink-0 disabled:opacity-50"
            disabled={aiBusy}
            onClick={() => setAiOpen(false)}
          >
            取消
          </Button>
        </div>
      )}

      {reviewing && (
        <div className="flex flex-wrap shrink-0 items-center gap-2 border-b border-primary/30 bg-primary/5 px-3 py-1.5 text-xs text-foreground">
          <Sparkles size={14} className="shrink-0 text-primary" />
          <span className="min-w-0 flex-1">
            AI 改写已就绪——用每块的 ✓ / ✗ 逐块采纳，或：
          </span>
          <Button
            className="shrink-0"
            onClick={() => finishReview(true)}
            icon={<Check size={13} />}
          >
            完成
          </Button>
          <Button
            variant="neutral"
            className="shrink-0 border border-border"
            onClick={() => finishReview(false)}
            icon={<X size={13} />}
          >
            全部放弃
          </Button>
        </div>
      )}

      {aiError && (
        <div className="flex shrink-0 items-center gap-2 border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
          <AlertTriangle size={14} className="shrink-0" />
          <span>{aiError}</span>
        </div>
      )}

      {readOnly && (
        <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
          <AlertTriangle size={14} className="shrink-0" />
          <span>此文件为 GBK 编码，暂不支持回写，已以只读方式打开。</span>
        </div>
      )}

      {conflict && (
        <div className="flex flex-wrap shrink-0 items-center gap-2 border-b border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-foreground">
          <AlertTriangle size={14} className="shrink-0 text-primary" />
          <span>磁盘上的文件已被改动，保存会覆盖磁盘版本。</span>
          <Button
            variant="ghost"
            onClick={load}
            className="h-auto px-0 py-0 underline-offset-2 hover:underline"
          >
            重新加载
          </Button>
          <Button
            variant="danger"
            onClick={() => void doSave(true)}
            className="h-auto px-0 py-0 underline-offset-2 hover:underline"
          >
            仍然覆盖
          </Button>
        </div>
      )}

      {saveError && !conflict && (
        <div
          className={`flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-xs ${noticeChipNeutral}`}
        >
          <AlertTriangle size={14} className="shrink-0 text-muted-foreground" />
          <span>{saveError}</span>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-hidden">
        {loadError ? (
          <CenterMsg title="无法打开" detail={loadError} />
        ) : content === null ? (
          <CenterMsg title="加载中…" />
        ) : mode === "edit" ? (
          <div className="flex h-full flex-col">
            {/* 工具栏只在可写时显示：只读（GBK）下文本变换会改文档却存不下，徒增困惑；
                AI 评审期也隐藏，避免文本变换命令在未定稿的 diff 上改文档 */}
            {!readOnly && !reviewing && (
              <SourceToolbar
                getView={() => editorRef.current?.getView() ?? null}
              />
            )}
            <div className="min-h-0 flex-1 overflow-hidden">
              <MarkdownSourceEditor
                key={editorKey}
                ref={editorRef}
                initialDoc={content}
                editable={!readOnly}
                onChange={(value) => {
                  latestMdRef.current = value;
                  setDirty(true);
                  setSaveState("idle");
                  if (aiError) setAiError(null);
                  if (autosaveTimerRef.current) {
                    clearTimeout(autosaveTimerRef.current);
                  }
                  // 防抖自动保存：冲突未决时暂停等裁决；AI 改写评审期间暂停（别把未定稿 diff 写盘）
                  if (!conflict && !readOnly && !aiActiveRef.current) {
                    autosaveTimerRef.current = setTimeout(
                      () => void doSave(),
                      AUTOSAVE_DEBOUNCE_MS,
                    );
                  }
                }}
                onSave={() => void doSave()}
                className="h-full w-full"
              />
            </div>
          </div>
        ) : memoryPreviewEmpty ? (
          <div className="flex h-full items-center justify-center px-6">
            <p className="max-w-sm text-center text-sm text-muted-foreground">
              {MEMORY_PREVIEW_EMPTY_HINT}
            </p>
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <div className="mx-auto max-w-3xl px-6 py-6">
              <Markdown
                content={
                  isMemorySource
                    ? stripRetiredUserMemoryChrome(content)
                    : content
                }
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CenterMsg({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 px-4 text-center">
      <span className="text-sm text-muted-foreground">{title}</span>
      {detail && (
        <span className="text-xs text-muted-foreground">{detail}</span>
      )}
    </div>
  );
}
