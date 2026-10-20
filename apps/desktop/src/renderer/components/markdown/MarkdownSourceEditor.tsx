/**
 * 源无关的 Markdown 源码编辑器（CodeMirror6 内核）。
 *
 * 「文本进、文本出」：挂载时灌入初始 markdown，渲成带语法高亮的源码编辑视图，通过 ref
 * 暴露 `getValue()` 供宿主保存时取最新正文。**字节忠实**——绝不隐式 reflow，人存的字节即
 * AI 读的字节，故无「富文本无损往返」的债。数据落点（本地 fs / 云端）由宿主经 FileSource 决定。
 *
 * 范围：源码编辑 + 语法高亮 + 软换行 + `Ctrl/Cmd+S` 保存 + 内联实时预览（mermaid/公式/表格
 * 就地渲染，光标进块自动还原源码，见 {@link livePreview}）+ AI 选区改写：句柄暴露「取选区
 * 上下文 / 启动·结束评审」，改写经 `@codemirror/merge` 的 {@link unifiedMergeView} 内联
 * 逐块 ✓/✗ 评审（挂在 `mergeComp` Compartment 上，进/出评审态靠 reconfigure 切换）。
 */

import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { unifiedMergeView } from "@codemirror/merge";
import { Compartment, EditorState, Prec } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  dropCursor,
  keymap,
} from "@codemirror/view";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import {
  type RewriteTarget,
  type SelectionContext,
  isRewriteTargetIntact,
  sliceSelectionContext,
} from "./aiRewrite";
import { markdownEditorTheme } from "./cmTheme";
import { livePreview } from "./livePreview";

export type { RewriteTarget } from "./aiRewrite";

export interface MarkdownSourceEditorHandle {
  /** 当前编辑器正文。 */
  getValue: () => string;
  /** 底层 CodeMirror 视图（后续工具栏 / AI diff 用）。 */
  getView: () => EditorView | null;
  /** 当前主选区及前后文；无选区返回 `null`。 */
  getSelectionContext: (ctxChars?: number) => SelectionContext | null;
  /**
   * 用改写文本替换目标选区并进入内联 diff 评审态。返回是否成功——选区在期间被改动
   * （原文不匹配）则拒绝并返回 `false`，绝不改到错误位置。
   */
  startRewriteReview: (target: RewriteTarget, rewritten: string) => boolean;
  /** 结束评审：`accept` 保留当前（逐块决策后的）正文；否则整体还原到改写前。 */
  endRewriteReview: (accept: boolean) => void;
}

interface MarkdownSourceEditorProps {
  /** 初始 markdown 正文。挂载时一次性灌入；切换文件请用 key 重挂。 */
  initialDoc: string;
  editable?: boolean;
  /** 正文变更时触发（脏标记 + 防抖预览 / 自动保存由宿主接）。 */
  onChange?: (value: string) => void;
  /** `Ctrl/Cmd+S` 触发。 */
  onSave?: () => void;
  className?: string;
}

export const MarkdownSourceEditor = forwardRef<
  MarkdownSourceEditorHandle,
  MarkdownSourceEditorProps
>(function MarkdownSourceEditor(
  { initialDoc, editable = true, onChange, onSave, className },
  ref,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  // 回调走 ref：避免进 useEffect 依赖导致重建编辑器，又不吃旧闭包。
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;
  const editableComp = useRef(new Compartment());
  // AI 改写评审：内联 diff 挂这个 Compartment（空 = 非评审态）；reviewOriginalRef 存改写前
  // 整篇正文，供「放弃」整体还原（而非逐块）。
  const mergeComp = useRef(new Compartment());
  const reviewOriginalRef = useRef("");
  // 实时预览挂 Compartment：评审期临时关掉，让 merge diff 显示在干净源码上，避免 widget
  // 与 diff 装饰互相打架；评审结束再恢复。
  const livePreviewComp = useRef(new Compartment());

  useImperativeHandle(
    ref,
    () => ({
      getValue: () => viewRef.current?.state.doc.toString() ?? initialDoc,
      getView: () => viewRef.current,
      getSelectionContext: (ctxChars) => {
        const view = viewRef.current;
        return view ? sliceSelectionContext(view.state, ctxChars) : null;
      },
      startRewriteReview: ({ from, to, selection }, rewritten) => {
        const view = viewRef.current;
        if (!view) return false;
        // 选区在调用期间可能被并行编辑改动：越界或原文不匹配则拒绝，绝不改错位置。
        if (!isRewriteTargetIntact(view.state, { from, to, selection }))
          return false;
        const original = view.state.doc.toString();
        reviewOriginalRef.current = original;
        // 一次事务内：开启 merge（original=改写前整篇）+ 关实时预览 + 替换选区为改写文本
        // → diff 仅落在选区，且显示在干净源码上。
        view.dispatch({
          effects: [
            mergeComp.current.reconfigure(unifiedMergeView({ original })),
            livePreviewComp.current.reconfigure([]),
          ],
          changes: { from, to, insert: rewritten },
          selection: { anchor: from + rewritten.length },
        });
        return true;
      },
      endRewriteReview: (accept) => {
        const view = viewRef.current;
        if (!view) return;
        // 放弃：整篇还原到改写前（逐块手动决策也一并丢弃）。接受：保留当前正文。
        if (!accept && reviewOriginalRef.current) {
          view.dispatch({
            changes: {
              from: 0,
              to: view.state.doc.length,
              insert: reviewOriginalRef.current,
            },
          });
        }
        // 退出评审：撤 merge diff，恢复实时预览。
        view.dispatch({
          effects: [
            mergeComp.current.reconfigure([]),
            livePreviewComp.current.reconfigure(livePreview()),
          ],
        });
        reviewOriginalRef.current = "";
      },
    }),
    [initialDoc],
  );

  // 挂载一次性建视图；切文件由父层用 key 重挂，故初始正文在此捕获即可。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 仅挂载时建视图；initialDoc/editable 的后续变化分别由 key 重挂与下方 reconfigure 处理，不应触发重建。
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      doc: initialDoc,
      parent: host,
      extensions: [
        history(),
        drawSelection(),
        dropCursor(),
        EditorState.allowMultipleSelections.of(true),
        EditorView.lineWrapping,
        markdown({ base: markdownLanguage }),
        livePreviewComp.current.of(livePreview()),
        markdownEditorTheme,
        // Ctrl/Cmd+S 最高优先级，压过默认按键
        Prec.highest(
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => {
                onSaveRef.current?.();
                return true;
              },
            },
          ]),
        ),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        editableComp.current.of(EditorView.editable.of(editable)),
        mergeComp.current.of([]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current?.(u.state.doc.toString());
        }),
      ],
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  // editable 切换不重建视图，热重配置即可。
  useEffect(() => {
    viewRef.current?.dispatch({
      effects: editableComp.current.reconfigure(
        EditorView.editable.of(editable),
      ),
    });
  }, [editable]);

  return <div ref={hostRef} className={className} />;
});
