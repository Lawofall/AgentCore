import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import type { FileSource } from "@/lib/fileSource";
import { useNarrowLayout } from "@/lib/useNarrowLayout";
import { useState } from "react";
import { WorkspaceImage } from "./WorkspaceImage";
import { type ComparePaneSpec, parseCompareFence } from "./parseCompareFence";

/**
 * ```compare 围栏渲染：桌面两栏并排，窄屏叠放并可切换查看。
 * 只展示经 {@link parseCompareFence} 校验过的工作区图片；解析失败回退源码块。
 */
export function CompareFence({
  body,
  conversationId = null,
  fileSource: explicitFileSource = null,
}: {
  body: string;
  conversationId?: string | null;
  /** 显式工作区源优先于 conversationId 经 hook 解析的源。 */
  fileSource?: FileSource | null;
}) {
  const panes = parseCompareFence(body);
  const isNarrow = useNarrowLayout();
  const hookSource = useConversationFileSource(conversationId);
  const fileSource = explicitFileSource ?? hookSource;
  const [active, setActive] = useState(0);

  if (!panes) {
    return (
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/30 p-3 text-xs">
        <code>{body}</code>
      </pre>
    );
  }

  if (isNarrow) {
    const idx = Math.min(active, panes.length - 1);
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-muted/20">
        <div className="flex flex-wrap items-center gap-1 border-b border-border px-2 py-1.5">
          {panes.map((pane, i) => (
            <PaneTab
              key={`${pane.path}:${i}`}
              pane={pane}
              index={i}
              active={idx === i}
              onSelect={() => setActive(i)}
            />
          ))}
        </div>
        <PaneBody pane={panes[idx]} source={fileSource} className="p-3" />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-muted/20">
      <div className="grid grid-cols-2 gap-3 p-3">
        {panes.map((pane, i) => (
          <div key={`${pane.path}:${i}`} className="min-w-0">
            <PaneHeader pane={pane} index={i} className="mb-2" />
            <PaneBody pane={pane} source={fileSource} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PaneTab({
  pane,
  index,
  active,
  onSelect,
}: {
  pane: ComparePaneSpec;
  index: number;
  active: boolean;
  onSelect: () => void;
}) {
  const slot = pane.slot ?? String.fromCharCode(65 + index);
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`rounded-lg px-2 py-0.5 text-xs font-medium transition-colors ${
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {slot} · {pane.label}
    </button>
  );
}

function PaneHeader({
  pane,
  index,
  className = "",
}: {
  pane: ComparePaneSpec;
  index?: number;
  className?: string;
}) {
  const slot =
    pane.slot ?? (index != null ? String.fromCharCode(65 + index) : undefined);
  return (
    <div className={`flex items-center gap-1.5 text-xs ${className}`}>
      {slot && (
        <span className="rounded bg-primary px-1 font-semibold text-primary-foreground">
          {slot}
        </span>
      )}
      <span className="truncate font-medium text-foreground">{pane.label}</span>
    </div>
  );
}

function PaneBody({
  pane,
  source,
  className = "",
}: {
  pane: ComparePaneSpec;
  source: FileSource | null;
  className?: string;
}) {
  return (
    <div className={className}>
      <WorkspaceImage source={source} path={pane.path} alt={pane.label} />
    </div>
  );
}
