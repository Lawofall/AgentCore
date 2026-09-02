import { Markdown } from "@/components/chat/Markdown";
import {
  type CatalogItem,
  FIDELITY_META,
  buildReceivedContextCatalog,
  defaultCatalogItemId,
  flattenCatalog,
} from "@/components/chat/receivedContextCatalog";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Badge, Button, SectionLabel } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatCompact } from "@/lib/format";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { cn } from "@/lib/utils";
import type { ContextBlockWire } from "@/types/events";
import { CornerDownRight } from "lucide-react";
import { useMemo, useState } from "react";

/**
 * 收到的上下文 — CEO 弹窗与队员右坞共用的简报阅读器壳。
 * 宽屏双栏、窄屏单列；目录只出现在弹窗里。
 */
function ReceivedContextReader({
  blocks,
  layout,
  onNavigate,
  preferMaterial = false,
  initialSelectedId,
}: {
  blocks: ContextBlockWire[];
  layout: "split" | "stack";
  onNavigate?: (runId: string) => void;
  preferMaterial?: boolean;
  initialSelectedId?: string | null;
}) {
  const { isNarrow } = useNarrowLayoutState();
  const groups = useMemo(
    () => buildReceivedContextCatalog(blocks, { includeSystem: !isNarrow }),
    [blocks, isNarrow],
  );
  const items = useMemo(() => flattenCatalog(groups), [groups]);
  const fallbackId = useMemo(
    () => defaultCatalogItemId(groups, { preferMaterial }),
    [groups, preferMaterial],
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    initialSelectedId ?? fallbackId,
  );
  const selected =
    items.find((i) => i.id === selectedId) ??
    items.find((i) => i.id === fallbackId) ??
    items[0];

  if (items.length === 0 || selected == null) return null;

  return (
    <div
      className={cn(
        "flex min-h-0 flex-1",
        layout === "split" && "flex-row",
        layout === "stack" && "flex-col",
      )}
    >
      <nav
        aria-label="上下文目录"
        className={cn(
          "overflow-y-auto",
          layout === "split" &&
            "w-44 shrink-0 border-r border-border px-2 py-2",
          layout === "stack" && "max-h-36 shrink-0 border-b border-border pb-2",
        )}
      >
        {groups.map((group) => (
          <div key={group.id} className="mb-2 last:mb-0">
            {group.items.length > 1 ? (
              <SectionLabel className="px-2 py-1">{group.label}</SectionLabel>
            ) : null}
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const isCurrent = item.id === selected.id;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      aria-current={isCurrent ? "true" : undefined}
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        "flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                        isCurrent && "bg-accent text-accent-foreground",
                      )}
                    >
                      <span className="min-w-0 truncate">{item.label}</span>
                      <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
                        {formatCompact(item.chars)} 字
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-3 py-2">
        <ReaderBody item={selected} onNavigate={onNavigate} />
      </div>
    </div>
  );
}

function ReaderBody({
  item,
  onNavigate,
}: {
  item: CatalogItem;
  onNavigate?: (runId: string) => void;
}) {
  const canJump = Boolean(onNavigate && item.source_run_id);
  const fidelityLabel = item.fidelity
    ? (FIDELITY_META[item.fidelity] ?? item.fidelity)
    : "";
  const showProvenance =
    Boolean(item.source_role) || Boolean(item.fidelity) || item.truncated;

  return (
    <div className="space-y-2">
      {showProvenance && (
        <div className="flex flex-wrap items-center gap-1.5">
          {item.source_role && canJump ? (
            <Button
              variant="ghost"
              onClick={() => onNavigate?.(item.source_run_id)}
              title="跳到来源节点"
              className="h-auto gap-1 px-1.5 py-0.5 text-muted-foreground"
            >
              <span>来自 {item.source_role}</span>
              <CornerDownRight size={14} className="shrink-0" />
            </Button>
          ) : null}
          {item.source_role && !canJump ? (
            <Badge tone="muted">来自 {item.source_role}</Badge>
          ) : null}
          {fidelityLabel ? <Badge tone="muted">{fidelityLabel}</Badge> : null}
          {item.truncated ? <Badge tone="muted">已截断</Badge> : null}
        </div>
      )}
      <div data-testid="received-context-body">
        {item.channel === "system" ? (
          <PromptDocument
            text={item.body}
            maxHeightClass="max-h-none"
            compact={false}
          />
        ) : (
          <Markdown content={item.body} />
        )}
      </div>
      {/* team_result body already inlines 文件产出; don't paint `files` twice. */}
      {item.files.length > 0 && item.channel !== "team_result" ? (
        <div className="space-y-0.5" data-testid="received-context-files">
          {item.files.map((f) => (
            <div
              key={f}
              className="flex items-center gap-1.5 text-xs text-muted-foreground"
            >
              <CornerDownRight size={14} className="shrink-0" />
              <span className="truncate font-mono">{f}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 队员右坞：一条入口，点开与 CEO 同一弹窗。
 */
export function ReceivedContextSection({
  blocks,
  onNavigate,
}: {
  blocks: ContextBlockWire[];
  onNavigate?: (runId: string) => void;
}) {
  const { isNarrow } = useNarrowLayoutState();
  const itemCount = useMemo(
    () =>
      flattenCatalog(
        buildReceivedContextCatalog(blocks, { includeSystem: !isNarrow }),
      ).length,
    [blocks, isNarrow],
  );
  const [open, setOpen] = useState(false);
  if (itemCount === 0) return null;
  return (
    <section className="mb-4 last:mb-0">
      <Button
        variant="ghost"
        onClick={() => setOpen(true)}
        className="h-auto w-full justify-start gap-1.5 px-0 py-0 hover:bg-transparent"
      >
        <span className="flex w-full items-center gap-1.5">
          <span className="flex-1 text-left text-xs font-medium text-muted-foreground">
            收到的上下文
          </span>
          <span className="text-xs tabular-nums text-muted-foreground">
            {itemCount} 段
          </span>
        </span>
      </Button>
      <ReceivedContextDialog
        blocks={blocks}
        open={open}
        onOpenChange={setOpen}
        preferMaterial
        onNavigate={onNavigate}
      />
    </section>
  );
}

/**
 * CEO 气泡 / 队员坞共用弹窗。宽屏双栏、固定框（max-w-2xl × min(32rem,70vh)）；
 * 窄屏改单列且不展示常驻指令。
 */
export function ReceivedContextDialog({
  blocks,
  open,
  onOpenChange,
  initialSelectedId,
  preferMaterial = false,
  onNavigate,
}: {
  blocks: ContextBlockWire[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialSelectedId?: string | null;
  preferMaterial?: boolean;
  onNavigate?: (runId: string) => void;
}) {
  const { isNarrow } = useNarrowLayoutState();
  if (blocks.length === 0) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(32rem,70vh)] max-w-2xl flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle>收到的上下文</DialogTitle>
          <DialogDescription>
            本回合 AI 实际读到的上下文，与喂给模型的逐字一致。
          </DialogDescription>
        </DialogHeader>
        <ReceivedContextReader
          key={initialSelectedId ?? "default"}
          blocks={blocks}
          layout={isNarrow ? "stack" : "split"}
          initialSelectedId={initialSelectedId}
          preferMaterial={preferMaterial}
          onNavigate={onNavigate}
        />
      </DialogContent>
    </Dialog>
  );
}
