/**
 * 源码编辑器工具栏：把按钮映射到 {@link sourceCommands} 的 Markdown 文本变换。
 *
 * 按钮一律 `onMouseDown` preventDefault——点工具栏不抢编辑器焦点/选区，命令才能作用于
 * 当前选区。AI 改写不在此（其触发按钮由宿主放在头部，见 `MarkdownFileEditor`）。
 */

import { IconButton } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { EditorView } from "@codemirror/view";
import {
  Bold,
  Code,
  Code2,
  Heading1,
  Heading2,
  Heading3,
  Italic,
  Link2,
  List,
  ListOrdered,
  ListTodo,
  Minus,
  Quote,
  Strikethrough,
  Table2,
} from "lucide-react";
import type { ReactNode } from "react";
import * as cmd from "./sourceCommands";

export function SourceToolbar({
  getView,
  className,
}: {
  getView: () => EditorView | null;
  className?: string;
}) {
  const run = (fn: (v: EditorView) => void) => () => {
    const view = getView();
    if (view) fn(view);
  };

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-0.5 border-b border-border px-2 py-1.5",
        className,
      )}
    >
      <Btn title="标题 1" onClick={run((v) => cmd.setHeading(v, 1))}>
        <Heading1 className="size-4" />
      </Btn>
      <Btn title="标题 2" onClick={run((v) => cmd.setHeading(v, 2))}>
        <Heading2 className="size-4" />
      </Btn>
      <Btn title="标题 3" onClick={run((v) => cmd.setHeading(v, 3))}>
        <Heading3 className="size-4" />
      </Btn>

      <Sep />

      <Btn title="加粗" onClick={run((v) => cmd.wrapInline(v, "**"))}>
        <Bold className="size-4" />
      </Btn>
      <Btn title="斜体" onClick={run((v) => cmd.wrapInline(v, "*"))}>
        <Italic className="size-4" />
      </Btn>
      <Btn title="删除线" onClick={run((v) => cmd.wrapInline(v, "~~"))}>
        <Strikethrough className="size-4" />
      </Btn>
      <Btn title="行内代码" onClick={run((v) => cmd.wrapInline(v, "`"))}>
        <Code className="size-4" />
      </Btn>
      <Btn title="链接" onClick={run(cmd.insertLink)}>
        <Link2 className="size-4" />
      </Btn>

      <Sep />

      <Btn title="无序列表" onClick={run(cmd.toggleBulletList)}>
        <List className="size-4" />
      </Btn>
      <Btn title="有序列表" onClick={run(cmd.toggleOrderedList)}>
        <ListOrdered className="size-4" />
      </Btn>
      <Btn title="任务清单" onClick={run(cmd.toggleTaskList)}>
        <ListTodo className="size-4" />
      </Btn>
      <Btn title="引用" onClick={run(cmd.toggleQuote)}>
        <Quote className="size-4" />
      </Btn>
      <Btn title="代码块" onClick={run(cmd.insertCodeBlock)}>
        <Code2 className="size-4" />
      </Btn>

      <Sep />

      <Btn title="插入表格" onClick={run(cmd.insertTable)}>
        <Table2 className="size-4" />
      </Btn>
      <Btn title="分割线" onClick={run(cmd.insertHr)}>
        <Minus className="size-4" />
      </Btn>
    </div>
  );
}

function Btn({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <IconButton
      title={title}
      aria-label={title}
      // mousedown.preventDefault 保住编辑器选区，避免点击工具栏丢失光标
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {children}
    </IconButton>
  );
}

function Sep() {
  return <div className="mx-1 h-5 w-px bg-border" />;
}
