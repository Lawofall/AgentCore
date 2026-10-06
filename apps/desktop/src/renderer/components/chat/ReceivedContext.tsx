import { HistoryTranscriptView } from "@/components/chat/HistoryTranscriptView";
import { Markdown } from "@/components/chat/Markdown";
import {
  type CatalogItem,
  type OpeningSection,
  RECEIVED_CONTEXT_ALL_ID,
  buildReceivedContextCatalog,
  defaultCatalogItemId,
  flattenCatalog,
  openingContextView,
} from "@/components/chat/receivedContextCatalog";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Badge, SectionLabel } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { formatCompact } from "@/lib/format";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { cn } from "@/lib/utils";
import type { ContextBlockWire, ProcessStep } from "@/types/events";
import { CornerDownRight } from "lucide-react";
import { useMemo, useState } from "react";

const EMPTY_PROCESS: ProcessStep[] = [];

/**
 * 收到的上下文 — CEO 弹窗与队员右坞共用的简报阅读器壳。
 * 宽屏双栏、窄屏单列；目录只出现在弹窗里。顶行「全部」按块原序连读开场投影。
 */
function ReceivedContextReader({
  blocks,
  process,
  layout,
  preferMaterial = false,
  initialSelectedId,
}: {
  blocks: ContextBlockWire[];
  process: readonly ProcessStep[];
  layout: "split" | "stack";
  preferMaterial?: boolean;
  initialSelectedId?: string | null;
}) {
  const { isNarrow } = useNarrowLayoutState();
  const groups = useMemo(
    () =>
      buildReceivedContextCatalog(blocks, {
        includeSystem: !isNarrow,
        process,
      }),
    [blocks, isNarrow, process],
  );
  const items = useMemo(() => flattenCatalog(groups), [groups]);
  const opening = useMemo(
    () => openingContextView(blocks, { includeSystem: !isNarrow }),
    [blocks, isNarrow],
  );
  const fallbackId = useMemo(
    () => defaultCatalogItemId(groups, { preferMaterial }),
    [groups, preferMaterial],
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    initialSelectedId ?? fallbackId,
  );
  const showingAll =
    selectedId === RECEIVED_CONTEXT_ALL_ID && opening.sections.length > 0;
  const selected =
    items.find((i) => i.id === selectedId) ??
    items.find((i) => i.id === fallbackId) ??
    items[0];

  if (!showingAll && selected == null) return null;

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
        {opening.sections.length > 0 ? (
          <ul className="mb-2 flex flex-col gap-0.5">
            <li>
              <CatalogRow
                label="全部"
                chars={opening.chars}
                current={showingAll}
                onSelect={() => setSelectedId(RECEIVED_CONTEXT_ALL_ID)}
              />
            </li>
          </ul>
        ) : null}
        {groups.map((group) => (
          <div key={group.id} className="mb-2 last:mb-0">
            {group.items.length > 1 ? (
              <SectionLabel className="px-2 py-1">{group.label}</SectionLabel>
            ) : null}
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <li key={item.id}>
                  <CatalogRow
                    label={item.label}
                    chars={item.chars}
                    current={!showingAll && item.id === selected?.id}
                    onSelect={() => setSelectedId(item.id)}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-3 py-2">
        {showingAll ? (
          <OpeningContextBody sections={opening.sections} />
        ) : selected != null ? (
          <ReaderBody item={selected} />
        ) : null}
      </div>
    </div>
  );
}

function CatalogRow({
  label,
  chars,
  current,
  onSelect,
}: {
  label: string;
  chars: number;
  current: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={current ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
        current && "bg-accent text-accent-foreground",
      )}
    >
      <span className="min-w-0 truncate">{label}</span>
      <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
        {formatCompact(chars)} 字
      </span>
    </button>
  );
}

function ReaderBody({ item }: { item: CatalogItem }) {
  return (
    <ContextCopy
      channel={item.channel}
      tag={item.tag}
      absent={item.absent}
      body={item.body}
      truncated={item.truncated}
      fidelity={item.fidelity}
      files={item.files}
    />
  );
}

function OpeningContextBody({ sections }: { sections: OpeningSection[] }) {
  return (
    <div className="space-y-4" data-testid="received-context-all">
      {sections.map((section, index) => (
        <section
          key={`${section.channel}-${index}`}
          className="space-y-2"
          data-testid="received-context-all-section"
        >
          <h2 className="break-words text-sm font-medium text-foreground">
            {section.heading}
          </h2>
          <ContextCopy
            channel={section.channel}
            tag={null}
            absent={false}
            body={section.body}
            truncated={section.truncated}
            fidelity={section.fidelity}
            files={section.files}
          />
        </section>
      ))}
    </div>
  );
}

function ContextCopy({
  channel,
  tag,
  absent,
  body,
  truncated,
  fidelity,
  files,
}: {
  channel: string;
  tag: string | null;
  absent: boolean;
  body: string;
  truncated: boolean;
  fidelity: string;
  files: string[];
}) {
  // pointer 是落盘策略不是预算截断；旧 journal 仍可能 stamp truncated。
  const showTruncated = truncated && fidelity !== "pointer";
  const factoryPrompt = channel === "system" && tag == null;

  return (
    <div className="space-y-2">
      {showTruncated ? <Badge tone="muted">已截断</Badge> : null}
      <div data-testid="received-context-body">
        {absent ? (
          <p
            data-testid="received-context-absent"
            className="text-sm text-muted-foreground"
          >
            {body}
          </p>
        ) : factoryPrompt ? (
          <PromptDocument
            text={body}
            maxHeightClass="max-h-none"
            compact={false}
          />
        ) : channel === "history" ? (
          <HistoryTranscriptView body={body} />
        ) : (
          <Markdown content={body} />
        )}
      </div>
      {/* team_result body already inlines 文件产出; don't paint `files` twice. */}
      {files.length > 0 && channel !== "team_result" ? (
        <div className="space-y-0.5" data-testid="received-context-files">
          {files.map((f) => (
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
 * 队员右坞：标题行一条入口，点开与 CEO 同一弹窗。无内容则不出现。
 */
export function ReceivedContextSection({
  blocks,
  process,
}: {
  blocks: ContextBlockWire[];
  process?: readonly ProcessStep[];
}) {
  const { isNarrow } = useNarrowLayoutState();
  const steps = process ?? EMPTY_PROCESS;
  const itemCount = useMemo(
    () =>
      flattenCatalog(
        buildReceivedContextCatalog(blocks, {
          includeSystem: !isNarrow,
          process: steps,
        }),
      ).length,
    [blocks, isNarrow, steps],
  );
  const [open, setOpen] = useState(false);
  if (itemCount === 0) return null;
  return (
    <>
      <SimpleTooltip label="看这位队员实际拿到的内容">
        <Badge
          as="button"
          tone="muted"
          pill
          className="font-medium"
          onClick={() => setOpen(true)}
        >
          上下文
        </Badge>
      </SimpleTooltip>
      <ReceivedContextDialog
        blocks={blocks}
        process={steps}
        open={open}
        onOpenChange={setOpen}
        preferMaterial
      />
    </>
  );
}

/**
 * CEO 气泡 / 队员坞共用弹窗。宽屏双栏、固定框（size 2xl × min(32rem,70vh)）；
 * 窄屏改单列且不展示系统切片（「全部」同样不含系统块）。
 */
export function ReceivedContextDialog({
  blocks,
  process,
  open,
  onOpenChange,
  initialSelectedId,
  preferMaterial = false,
}: {
  blocks: ContextBlockWire[];
  process?: readonly ProcessStep[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialSelectedId?: string | null;
  preferMaterial?: boolean;
}) {
  const { isNarrow } = useNarrowLayoutState();
  const steps = process ?? EMPTY_PROCESS;
  if (blocks.length === 0 && steps.length === 0) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="2xl"
        className="flex h-[min(32rem,70vh)] flex-col"
        aria-describedby={undefined}
      >
        <DialogHeader className="shrink-0">
          <DialogTitle>收到的上下文</DialogTitle>
        </DialogHeader>
        <ReceivedContextReader
          key={initialSelectedId ?? "default"}
          blocks={blocks}
          process={steps}
          layout={isNarrow ? "stack" : "split"}
          initialSelectedId={initialSelectedId}
          preferMaterial={preferMaterial}
        />
      </DialogContent>
    </Dialog>
  );
}
