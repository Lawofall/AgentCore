/** Centered ghost line at the rolling-compaction fold (权限切换同款，无卡、无摘要正文). */
export const CONTEXT_COMPACTED_DIVIDER_HINT =
  "以上内容已收入摘要，原文仍可上翻";

export function CompactionDivider() {
  return (
    <div
      data-testid="context-compacted-divider"
      className="flex items-center gap-2 text-xs text-muted-foreground"
    >
      <span className="h-px flex-1 bg-border" />
      <span className="shrink-0 px-1 text-center">
        {CONTEXT_COMPACTED_DIVIDER_HINT}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
