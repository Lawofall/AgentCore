import { Button, IconButton as UiIconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import type { FileNode } from "@/lib/fileSource";
import { formatMessageTime } from "@/lib/format";
import { Loader2 } from "lucide-react";

export { EmptyHint } from "@/components/ui";

/**
 * Shared presentational primitives for the file UIs (文件中枢统一) — the tree /
 * preview / snapshot surfaces of both the Files page and the conversation
 * workspace panel. `EmptyHint` 是 L2，此处转出。
 */

export function IconButton({
  title,
  onClick,
  spinning,
  disabled,
  children,
}: {
  title: string;
  onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
  spinning?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <SimpleTooltip label={title}>
      <UiIconButton
        onClick={onClick}
        disabled={spinning || disabled}
        aria-label={title}
      >
        {spinning ? <Loader2 size={14} className="animate-spin" /> : children}
      </UiIconButton>
    </SimpleTooltip>
  );
}

export function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center">{children}</div>
  );
}

export function InlineError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-xs text-muted-foreground">加载失败</p>
      <Button variant="neutral" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}

/**
 * 「这一层还没列全」的行内提示。
 *
 * 后端列举有条目上限；命中时**必须**说出来。少列几十个文件而界面看起来完好，用户读到的
 * 是「我的文件没了」——诚实优先于容量是本项目一贯标准，故宁可挂一行提示也不静默截断。
 * 只陈述事实、不指路「去搜索」：树内筛选只匹配已加载的层，指过去会是第二次撒谎。
 */
export function TruncatedNotice({
  indent,
  shown,
}: {
  indent: number;
  shown: number;
}) {
  return (
    <li className="py-1 pr-2" style={{ paddingLeft: indent }}>
      {/* Same band as the preview's「内容较大」notice — one visual language for
          "this view is bounded", so a capped level never reads as a file row. */}
      <span className="block rounded-lg bg-muted/40 px-2 py-1 text-xs text-muted-foreground">
        条目较多，仅显示前 {shown} 项，还有更多未显示。
      </span>
    </li>
  );
}

/** 文件行右侧的修改时间（缺 mtime 则返回 null，不占位）。 */
export function fileMetaLabel(node: { mtimeMs?: number | null }):
  | string
  | null {
  if (typeof node.mtimeMs !== "number") return null;
  const at = formatMessageTime(new Date(node.mtimeMs).toISOString());
  return at || null;
}

/**
 * 文件树行尾的修改时间。
 *
 * 只在源给出 mtime 时出现：合成源（记忆叶子等）与列不出 stat 的条目一律不占位，宁可少
 * 一列也不显示「未知」这类会被读成事实的占位符。文件名负责截断，这段 `shrink-0`
 * 保证窄栏里先挤名字、后挤元信息。
 */
export function FileRowMeta({ node }: { node: FileNode }) {
  const label = fileMetaLabel(node);
  if (!label) return null;
  return (
    <span className="shrink-0 tabular-nums text-xs text-muted-foreground/70">
      {label}
    </span>
  );
}
