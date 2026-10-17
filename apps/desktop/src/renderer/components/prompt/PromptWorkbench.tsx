/**
 * 工具箱提示词工作台（预览 Markdown / 编辑 CodeMirror；可写停敲自动存）。
 * 阅读切换和加载方式同一行；名称和一句话介绍各一行。有改动才出保存。
 */

import {
  MarkdownSourceEditor,
  type MarkdownSourceEditorHandle,
} from "@/components/markdown/MarkdownSourceEditor";
import { SourceToolbar } from "@/components/markdown/sourceToolbar";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Button, Input, SegmentedControl } from "@/components/ui";
import { cn } from "@/lib/utils";
import { Loader2, Save } from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

export type PromptSaveState = "idle" | "saving" | "saved" | "error";
export type PromptApplyMode = "always" | "on_demand" | "paths";

const APPLY_MODE_ITEMS = [
  { value: "always", label: "常驻" },
  { value: "on_demand", label: "按需" },
  { value: "paths", label: "碰到文件" },
] as const;

const TITLE_LABEL = "名称";
const CATALOG_LINE_LABEL = "一句话介绍";
const CATALOG_LINE_PLACEHOLDER = "干什么、什么时候该翻开";
const PATH_LINE_PLACEHOLDER = "碰到这类文件就遵守的那一句";
const PATHS_LABEL = "路径";
const PATHS_PLACEHOLDER = "**/*.tsx、src/*.py";
const AUTOSAVE_DEBOUNCE_MS = 1500;

const FIELD_LABEL_CLASS =
  "w-20 shrink-0 whitespace-nowrap text-xs text-muted-foreground";
const BODY_PLACEHOLDER = "从这里写正文";

export interface PromptWorkbenchDraft {
  title: string;
  trigger: string;
  body: string;
  applyMode?: PromptApplyMode;
  paths?: string;
}

export function PromptWorkbench({
  title,
  titleEditable = false,
  badges,
  applyMode,
  onApplyModeChange,
  initialPaths = "",
  initialTrigger,
  triggerEnabled = false,
  initialBody,
  bodyLoading = false,
  readOnly = false,
  extraActions,
  leading,
  previewing = false,
  testId,
  onSave,
}: {
  title: string;
  titleEditable?: boolean;
  badges?: ReactNode;
  /** `undefined` hides the 常驻 | 按需 | 碰到文件 switch. */
  applyMode?: PromptApplyMode;
  onApplyModeChange?: (mode: PromptApplyMode) => void;
  initialPaths?: string;
  initialTrigger?: string;
  /** Show the catalog line (even when the seed is empty). */
  triggerEnabled?: boolean;
  initialBody: string;
  bodyLoading?: boolean;
  readOnly?: boolean;
  extraActions?: ReactNode;
  /** Left of the save row (e.g. 预览 | 编辑). */
  leading?: ReactNode;
  previewing?: boolean;
  testId?: string;
  onSave?: (draft: PromptWorkbenchDraft) => Promise<boolean>;
}) {
  const titleId = useId();
  const catalogLineId = useId();
  const [titleValue, setTitleValue] = useState(title);
  const [trigger, setTrigger] = useState(initialTrigger ?? "");
  const [paths, setPaths] = useState(initialPaths);
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
    ...(applyMode !== undefined ? { applyMode, paths } : {}),
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

  const showCatalogLine = applyMode !== "always" && triggerEnabled;
  const catalogPlaceholder =
    applyMode === "paths" ? PATH_LINE_PLACEHOLDER : CATALOG_LINE_PLACEHOLDER;
  const showFields =
    !previewing && (titleEditable || showCatalogLine || applyMode === "paths");
  const triggerText = trigger.trim();
  const showSave = Boolean(
    !readOnly &&
      onSave &&
      (dirty || saveState === "saving" || saveState === "error"),
  );
  const showBodyHint = !readOnly && !previewing && !body.trim();

  const modeSwitch =
    applyMode !== undefined ? (
      <SegmentedControl
        aria-label="加载方式"
        value={applyMode}
        onChange={(next) => {
          if (readOnly) return;
          onApplyModeChange?.(next);
          markDirty({ applyMode: next });
        }}
        items={APPLY_MODE_ITEMS}
        className="w-fit"
      />
    ) : null;

  const cover = showFields ? (
    <div className="mt-2 flex flex-col gap-2">
      {titleEditable ? (
        <label htmlFor={titleId} className="flex min-w-0 items-center gap-2">
          <span className={FIELD_LABEL_CLASS}>{TITLE_LABEL}</span>
          <Input
            id={titleId}
            aria-label={TITLE_LABEL}
            value={titleValue}
            onChange={(event) => {
              const next = event.target.value;
              setTitleValue(next);
              markDirty({ title: next });
            }}
            disabled={readOnly}
            className="min-w-0 flex-1"
          />
        </label>
      ) : null}
      {showCatalogLine ? (
        readOnly ? (
          <div className="flex min-w-0 items-center gap-2">
            <span className={FIELD_LABEL_CLASS}>{CATALOG_LINE_LABEL}</span>
            <span
              className="min-w-0 truncate text-sm text-foreground"
              title={triggerText || undefined}
            >
              {triggerText || catalogPlaceholder}
            </span>
          </div>
        ) : (
          <label
            htmlFor={catalogLineId}
            className="flex min-w-0 items-center gap-2"
          >
            <span className={FIELD_LABEL_CLASS}>{CATALOG_LINE_LABEL}</span>
            <Input
              id={catalogLineId}
              aria-label={CATALOG_LINE_LABEL}
              placeholder={catalogPlaceholder}
              value={trigger}
              onChange={(event) => {
                const next = event.target.value;
                setTrigger(next);
                markDirty({ trigger: next });
              }}
              className="min-w-0 flex-1"
            />
          </label>
        )
      ) : null}
      {applyMode === "paths" ? (
        <label
          htmlFor={`${catalogLineId}-paths`}
          className="flex min-w-0 items-center gap-2"
        >
          <span className={FIELD_LABEL_CLASS}>{PATHS_LABEL}</span>
          <Input
            id={`${catalogLineId}-paths`}
            aria-label={PATHS_LABEL}
            placeholder={PATHS_PLACEHOLDER}
            value={paths}
            disabled={readOnly}
            onChange={(event) => {
              const next = event.target.value;
              setPaths(next);
              markDirty({ paths: next });
            }}
            className="min-w-0 flex-1"
          />
        </label>
      ) : null}
    </div>
  ) : null;

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      data-testid={testId}
    >
      <div
        className={cn(
          "shrink-0 border-b border-border px-5 py-2",
          showFields && "bg-muted/40",
        )}
      >
        <div className="flex items-center gap-2">
          {leading}
          {badges}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {modeSwitch}
            {saveState === "saving" ? (
              <span className="text-muted-foreground text-xs">保存中…</span>
            ) : null}
            {saveState === "saved" && !dirty ? (
              <span className="text-muted-foreground text-xs">已保存</span>
            ) : null}
            {saveState === "error" ? (
              <span className="text-muted-foreground text-xs">没保存上</span>
            ) : null}
            {extraActions}
            {showSave ? (
              <Button
                className="shrink-0"
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
          </div>
        </div>
        {cover}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {bodyLoading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center text-muted-foreground text-sm">
            加载中…
          </div>
        ) : previewing ? (
          <div className="min-h-0 flex-1 overflow-auto px-6 py-4">
            {body.trim() ? (
              <PromptDocument
                text={body}
                compact={false}
                framed={false}
                maxHeightClass="max-h-none"
              />
            ) : (
              <p className="flex h-full min-h-[12rem] items-center justify-center text-muted-foreground text-sm">
                还没有正文
              </p>
            )}
          </div>
        ) : (
          <div className="flex min-h-[16rem] min-w-0 flex-1 flex-col overflow-hidden">
            {!readOnly ? (
              <SourceToolbar
                className="px-4"
                getView={() => editorRef.current?.getView() ?? null}
              />
            ) : null}
            <div className="relative min-h-0 flex-1 overflow-hidden">
              {showBodyHint ? (
                <p className="pointer-events-none absolute top-6 left-6 font-mono text-sm leading-relaxed text-muted-foreground">
                  {BODY_PLACEHOLDER}
                </p>
              ) : null}
              <MarkdownSourceEditor
                ref={editorRef}
                initialDoc={body}
                editable={!readOnly}
                onChange={(value) => {
                  setBody(value);
                  markDirty({ body: value });
                }}
                onSave={() => void doSave()}
                className="relative h-full w-full"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
