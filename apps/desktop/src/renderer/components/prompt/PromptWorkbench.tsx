/**
 * 工具箱提示词右栏：与文件页同一套文档工作台（源码编辑 / 预览 / 停敲自动存）。
 * 顶栏只放动作。名字 + 目录那一句是封面，始终可见；编辑 / 预览只切换正文。
 */

import {
  MarkdownSourceEditor,
  type MarkdownSourceEditorHandle,
} from "@/components/markdown/MarkdownSourceEditor";
import { SourceToolbar } from "@/components/markdown/sourceToolbar";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Button, Input, SegmentedControl } from "@/components/ui";
import { cn } from "@/lib/utils";
import { Eye, Loader2, PencilLine, Save } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

export type PromptSaveState = "idle" | "saving" | "saved" | "error";
export type PromptApplyMode = "always" | "on_demand";
type ViewMode = "edit" | "preview";

const APPLY_MODE_ITEMS = [
  { value: "always", label: "常驻" },
  { value: "on_demand", label: "按需" },
] as const;

const CATALOG_LINE_LABEL = "一句话介绍";
const CATALOG_LINE_PLACEHOLDER = "用一句话说这是什么";
const AUTOSAVE_DEBOUNCE_MS = 1500;

const TITLE_FIELD_CLASS =
  "h-auto min-h-8 w-full border-0 bg-transparent px-0 font-medium text-foreground text-xl leading-snug focus:border-transparent focus-visible:ring-0";
const CATALOG_FIELD_CLASS =
  "h-auto min-h-8 w-full border-0 bg-transparent px-0 text-muted-foreground focus:border-transparent focus-visible:ring-0";

export interface PromptWorkbenchDraft {
  title: string;
  trigger: string;
  body: string;
}

export function PromptWorkbench({
  title,
  titleEditable = false,
  badges,
  hint,
  applyMode,
  onApplyModeChange,
  initialTrigger,
  triggerEnabled = false,
  initialBody,
  bodyLoading = false,
  readOnly = false,
  hideHeading,
  extraActions,
  testId,
  onSave,
}: {
  title: string;
  titleEditable?: boolean;
  badges?: ReactNode;
  hint?: ReactNode;
  /** `undefined` hides the 常驻 | 按需 switch. */
  applyMode?: PromptApplyMode;
  onApplyModeChange?: (mode: PromptApplyMode) => void;
  initialTrigger?: string;
  /** Show the catalog line (even when the seed is empty). */
  triggerEnabled?: boolean;
  initialBody: string;
  bodyLoading?: boolean;
  readOnly?: boolean;
  hideHeading?: string;
  extraActions?: ReactNode;
  testId?: string;
  onSave?: (draft: PromptWorkbenchDraft) => Promise<boolean>;
}) {
  const catalogLineId = useId();
  const [mode, setMode] = useState<ViewMode>("preview");
  const [titleValue, setTitleValue] = useState(title);
  const [trigger, setTrigger] = useState(initialTrigger ?? "");
  const [body, setBody] = useState(initialBody);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<PromptSaveState>("idle");
  const editorRef = useRef<MarkdownSourceEditorHandle>(null);
  const latestRef = useRef<PromptWorkbenchDraft>({
    title,
    trigger: initialTrigger ?? "",
    body: initialBody,
  });
  const dirtyRef = useRef(false);
  const savingRef = useRef(false);
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushRef = useRef<() => void>(() => {});

  latestRef.current = {
    title: titleValue,
    trigger,
    body,
  };
  dirtyRef.current = dirty;

  const clearAutosave = useCallback(() => {
    if (autosaveTimerRef.current) {
      clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
  }, []);

  const doSave = useCallback(async () => {
    if (readOnly || !onSave || savingRef.current) return;
    const md = editorRef.current?.getValue() ?? latestRef.current.body;
    const draft: PromptWorkbenchDraft = { ...latestRef.current, body: md };
    latestRef.current = draft;
    setBody(md);
    if (!dirtyRef.current) return;
    clearAutosave();
    savingRef.current = true;
    setSaveState("saving");
    try {
      const ok = await onSave(draft);
      if (ok) {
        dirtyRef.current = false;
        setDirty(false);
        setSaveState("saved");
      } else {
        setSaveState("error");
      }
    } finally {
      savingRef.current = false;
    }
  }, [onSave, readOnly, clearAutosave]);

  const markDirty = useCallback(
    (patch: Partial<PromptWorkbenchDraft>) => {
      if (readOnly) return;
      latestRef.current = { ...latestRef.current, ...patch };
      dirtyRef.current = true;
      setDirty(true);
      setSaveState("idle");
      clearAutosave();
      autosaveTimerRef.current = setTimeout(() => {
        void doSave();
      }, AUTOSAVE_DEBOUNCE_MS);
    },
    [doSave, readOnly, clearAutosave],
  );

  useEffect(() => {
    if (saveState !== "saved") return;
    const id = setTimeout(
      () => setSaveState((s) => (s === "saved" ? "idle" : s)),
      2500,
    );
    return () => clearTimeout(id);
  }, [saveState]);

  useEffect(() => {
    flushRef.current = () => {
      if (savingRef.current || !dirtyRef.current || readOnly || !onSave) return;
      const md = editorRef.current?.getValue() ?? latestRef.current.body;
      void onSave({ ...latestRef.current, body: md });
    };
  }, [onSave, readOnly]);

  useEffect(
    () => () => {
      clearAutosave();
      flushRef.current();
    },
    [clearAutosave],
  );

  useEffect(() => {
    const onBeforeUnload = () => flushRef.current();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  const switchMode = (next: ViewMode) => {
    setMode((prev) => {
      if (prev === next) return prev;
      if (prev === "edit") {
        const md = editorRef.current?.getValue() ?? latestRef.current.body;
        latestRef.current = { ...latestRef.current, body: md };
        setBody(md);
      }
      return next;
    });
  };

  const showCatalogLine =
    applyMode === "always"
      ? false
      : triggerEnabled || initialTrigger !== undefined;
  const triggerText = trigger.trim();
  const hasBody = body.trim().length > 0;
  const editingBody = mode === "edit" && !readOnly;

  const cover = (
    <div className="mx-auto w-full max-w-3xl px-6 pt-5 pb-4">
      <div className="flex flex-wrap items-start gap-3">
        {titleEditable ? (
          <Input
            aria-label="名称"
            value={titleValue}
            onChange={(event) => {
              const next = event.target.value;
              setTitleValue(next);
              markDirty({ title: next });
            }}
            disabled={readOnly}
            className={cn(TITLE_FIELD_CLASS, "min-w-0 flex-1")}
          />
        ) : (
          <h2 className="min-w-0 flex-1 font-medium text-foreground text-xl leading-snug">
            {titleValue}
          </h2>
        )}
        {applyMode !== undefined ? (
          <SegmentedControl
            aria-label="加载方式"
            value={applyMode}
            onChange={(next) => onApplyModeChange?.(next)}
            items={APPLY_MODE_ITEMS}
            className="w-auto shrink-0"
          />
        ) : null}
      </div>
      {showCatalogLine ? (
        readOnly ? (
          <div className="mt-2 flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-muted-foreground text-xs">
              {CATALOG_LINE_LABEL}
            </span>
            <span className="min-w-0 text-muted-foreground text-sm">
              {triggerText || CATALOG_LINE_PLACEHOLDER}
            </span>
          </div>
        ) : (
          <label
            htmlFor={catalogLineId}
            className="mt-2 flex min-w-0 items-baseline gap-2"
          >
            <span className="shrink-0 text-muted-foreground text-xs">
              {CATALOG_LINE_LABEL}
            </span>
            <Input
              id={catalogLineId}
              aria-label={CATALOG_LINE_LABEL}
              placeholder={CATALOG_LINE_PLACEHOLDER}
              value={trigger}
              onChange={(event) => {
                const next = event.target.value;
                setTrigger(next);
                markDirty({ trigger: next });
              }}
              className={cn(CATALOG_FIELD_CLASS, "min-w-0 flex-1")}
            />
          </label>
        )
      ) : null}
    </div>
  );

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid={testId}
    >
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
        {dirty ? (
          <span className="shrink-0 text-primary text-xs">●</span>
        ) : null}
        {badges}
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {saveState === "saving" ? (
            <span className="text-muted-foreground text-xs">保存中…</span>
          ) : null}
          {saveState === "saved" && !dirty ? (
            <span className="text-muted-foreground text-xs">已保存</span>
          ) : null}
          {extraActions}
          {!readOnly && onSave ? (
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
          ) : null}
          {!readOnly ? (
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
          ) : null}
        </div>
      </div>

      {hint ? (
        <p className="shrink-0 border-b border-border px-3 py-1.5 text-muted-foreground text-xs">
          {hint}
        </p>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="shrink-0 border-b border-border">{cover}</div>
        {bodyLoading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center text-muted-foreground text-sm">
            加载中…
          </div>
        ) : editingBody ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <SourceToolbar
              getView={() => editorRef.current?.getView() ?? null}
            />
            <div className="min-h-0 flex-1 overflow-hidden">
              <MarkdownSourceEditor
                ref={editorRef}
                initialDoc={body}
                editable
                onChange={(value) => {
                  setBody(value);
                  markDirty({ body: value });
                }}
                onSave={() => void doSave()}
                className="h-full w-full"
              />
            </div>
          </div>
        ) : hasBody ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <div className="mx-auto max-w-3xl px-6 py-6">
              <PromptDocument
                text={body}
                compact={false}
                framed={false}
                maxHeightClass="max-h-none"
                hideHeading={hideHeading}
              />
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center px-6">
            <p className="text-center text-muted-foreground text-sm">
              还没有正文
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
