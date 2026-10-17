import { PromptDocument } from "@/components/prompt/PromptDocument";
import {
  type PromptApplyMode,
  PromptWorkbench,
} from "@/components/prompt/PromptWorkbench";
import { PublishSkillDialog } from "@/components/tools/PublishSkillDialog";
import { ToolInspector } from "@/components/tools/ToolInspector";
import { Badge, Button, SegmentedControl } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { PromptCatalogItem } from "@/lib/promptCatalog";
import {
  promptItemShelfCopy,
  promptMineShelfOpts,
  promptReadHeaderChips,
} from "@/lib/promptShelfTile";
import { publishBlockReason } from "@/lib/skillStoreCopy";
import { cn } from "@/lib/utils";
import { getDocument } from "@/services/documents";
import {
  type SkillCatalog,
  parsePathPatterns,
  skillBodyFromContent,
} from "@/services/skillCatalog";
import type { SkillStoreGroup, SkillStoreListing } from "@/services/skillStore";
import { useEffect, useState } from "react";

type MineView = "preview" | "edit";

export function PromptReadDialog({
  open,
  item,
  overlay,
  listings,
  installedListings,
  busy,
  showToolsHint,
  toolsHint,
  toolCallingNames,
  onOpenChange,
  onSaveMine,
  onPublishMine,
  onUnpublishMine,
}: {
  open: boolean;
  item: PromptCatalogItem | null;
  overlay: SkillCatalog;
  listings: SkillStoreListing[];
  installedListings: SkillStoreListing[];
  busy: boolean;
  showToolsHint: boolean;
  toolsHint: string;
  toolCallingNames: ReadonlySet<string>;
  onOpenChange: (open: boolean) => void;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: {
      name: string;
      description: string;
      body: string;
      applyMode: PromptApplyMode;
      paths: string;
    },
  ) => Promise<boolean>;
  onPublishMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    group: SkillStoreGroup,
  ) => void;
  onUnpublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  const [publishOpen, setPublishOpen] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: item id is an intentional re-run key
  useEffect(() => {
    setPublishOpen(false);
  }, [item && "id" in item ? item.id : null]);

  const showItem = item != null;
  const mineItem =
    item?.kind === "mine"
      ? (item as Extract<PromptCatalogItem, { kind: "mine" }>)
      : null;
  const fromMarket = Boolean(
    mineItem?.mineId &&
      installedListings.some(
        (row) => row.installDocumentId === mineItem.mineId,
      ),
  );
  const listing = mineItem
    ? (listings.find((row) => row.documentId === mineItem.mineId) ?? null)
    : null;
  const header = readHeader({
    showItem,
    item,
    listings,
    installedListings,
  });
  const canPublish =
    Boolean(mineItem) &&
    overlay.writable &&
    !fromMarket &&
    mineItem?.applyMode === "on_demand" &&
    listing?.status !== "taken_down";
  const canUnpublish =
    Boolean(mineItem) &&
    overlay.writable &&
    !fromMarket &&
    listing?.status === "published";
  const showPublish = Boolean(mineItem) && showItem;

  const surface = (
    <div className="flex min-h-0 flex-1 flex-col">
      <DialogHeader className="shrink-0 pt-3 pb-2">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <DialogTitle className="min-w-0 truncate">
              {header.title}
            </DialogTitle>
            {header.chips.map((chip) => (
              <Badge key={chip.label} tone={chip.tone ?? "muted"} pill>
                {chip.label}
              </Badge>
            ))}
            {canPublish || canUnpublish ? (
              <div className="ml-auto flex shrink-0 items-center gap-2">
                {canPublish ? (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy}
                    onClick={() => setPublishOpen(true)}
                  >
                    上架
                  </Button>
                ) : null}
                {canUnpublish ? (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy}
                    onClick={() => mineItem && onUnpublishMine(mineItem)}
                  >
                    下架
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
        <DialogDescription className="sr-only">
          {header.description}
        </DialogDescription>
      </DialogHeader>
      <DialogBody
        className={cn(
          "flex min-h-0 flex-1 flex-col",
          mineItem ? "overflow-hidden px-0 pb-0" : "pb-5",
        )}
      >
        {showItem && item ? (
          <ReadBody
            item={item}
            overlay={overlay}
            showToolsHint={showToolsHint}
            toolsHint={toolsHint}
            toolCallingNames={toolCallingNames}
            onSaveMine={onSaveMine}
          />
        ) : null}
      </DialogBody>
      {showPublish && mineItem ? (
        <PublishSkillDialog
          open={publishOpen}
          busy={busy}
          initialGroup={listing?.group ?? null}
          blockReason={publishBlockReason(
            mineItem.description,
            mineItem.content,
          )}
          onOpenChange={setPublishOpen}
          onConfirm={(group) => {
            setPublishOpen(false);
            onPublishMine(mineItem, group);
          }}
        />
      ) : null}
    </div>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && publishOpen) return;
        onOpenChange(next);
      }}
    >
      <DialogContent
        size={mineItem ? "2xl" : "lg"}
        data-testid="prompt-read-dialog"
        className={cn(
          "flex flex-col",
          mineItem
            ? "h-[min(80vh,40rem)] max-h-[min(80vh,40rem)]"
            : "max-h-[min(80vh,36rem)]",
        )}
        onPointerDownOutside={(event) => {
          if (publishOpen) event.preventDefault();
        }}
        onFocusOutside={(event) => {
          if (publishOpen) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (publishOpen) event.preventDefault();
        }}
      >
        {surface}
      </DialogContent>
    </Dialog>
  );
}

function readHeader({
  showItem,
  item,
  listings,
  installedListings,
}: {
  showItem: boolean;
  item: PromptCatalogItem | null;
  listings: SkillStoreListing[];
  installedListings: SkillStoreListing[];
}): {
  title: string;
  chips: ReturnType<typeof promptReadHeaderChips>;
  description: string;
} {
  if (showItem && item) {
    const mineOpts =
      item.kind === "mine"
        ? promptMineShelfOpts(item, listings, installedListings)
        : {};
    const copy = promptItemShelfCopy(item, mineOpts);
    return {
      title: copy.title,
      chips: promptReadHeaderChips(copy, item),
      description: copy.description || copy.title,
    };
  }
  return {
    title: "提示词",
    chips: [],
    description: "提示词",
  };
}

function ReadBody({
  item,
  overlay,
  showToolsHint,
  toolsHint,
  toolCallingNames,
  onSaveMine,
}: {
  item: PromptCatalogItem;
  overlay: SkillCatalog;
  showToolsHint: boolean;
  toolsHint: string;
  toolCallingNames: ReadonlySet<string>;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: {
      name: string;
      description: string;
      body: string;
      applyMode: PromptApplyMode;
      paths: string;
    },
  ) => Promise<boolean>;
}) {
  if (item.kind === "tool") {
    return (
      <ToolInspector
        key={item.id}
        tool={item.tool}
        hideChrome
        capabilityHint={
          showToolsHint && toolCallingNames.has(item.tool.name)
            ? toolsHint
            : undefined
        }
      />
    );
  }

  if (item.kind === "shared") {
    return (
      <PromptDocument
        text={item.text}
        compact={false}
        framed={false}
        maxHeightClass="max-h-none"
      />
    );
  }

  if (item.kind === "skill") {
    return (
      <div data-testid="factory-skill-editor">
        <PromptDocument
          text={item.skill.body}
          compact={false}
          framed={false}
          maxHeightClass="max-h-none"
        />
      </div>
    );
  }

  if (item.kind === "mine") {
    return (
      <MineSkillEditor
        key={item.id}
        item={item}
        writable={overlay.writable}
        onSave={onSaveMine}
      />
    );
  }

  return null;
}

function initialMineView(content: string): MineView {
  return skillBodyFromContent(content).trim() ? "preview" : "edit";
}

function MineSkillEditor({
  item,
  writable,
  onSave,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  writable: boolean;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: {
      name: string;
      description: string;
      body: string;
      applyMode: PromptApplyMode;
      paths: string;
    },
  ) => Promise<boolean>;
}) {
  const [applyMode, setApplyMode] = useState<PromptApplyMode>(item.applyMode);
  const [paths, setPaths] = useState(() => parsePathPatterns(item.content));
  useEffect(() => {
    setApplyMode(item.applyMode);
    setPaths(parsePathPatterns(item.content));
  }, [item.applyMode, item.content]);
  const pendingBody = Boolean(item.mineId) && !item.content;
  const [view, setView] = useState<MineView>(() =>
    pendingBody ? "preview" : initialMineView(item.content),
  );
  const [body, setBody] = useState(() => skillBodyFromContent(item.content));
  const [version, setVersion] = useState(item.version);
  const [loading, setLoading] = useState(Boolean(item.mineId) && !item.content);

  useEffect(() => {
    if (!item.mineId || item.content) {
      setBody(skillBodyFromContent(item.content));
      setVersion(item.version);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void getDocument(item.mineId)
      .then((doc) => {
        if (cancelled) return;
        setBody(skillBodyFromContent(doc.content));
        setVersion(doc.version);
        setLoading(false);
        setView((current) =>
          current === "edit" ? current : initialMineView(doc.content),
        );
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item.mineId, item.content, item.version]);

  return (
    <PromptWorkbench
      key={loading ? `${item.id}-loading` : item.id}
      testId="mine-skill-editor"
      title={item.label}
      titleEditable
      badges={null}
      leading={
        <SegmentedControl
          aria-label="阅读方式"
          value={view}
          onChange={setView}
          items={[
            { value: "preview", label: "预览" },
            { value: "edit", label: "编辑" },
          ]}
          className="w-auto"
        />
      }
      previewing={view === "preview"}
      applyMode={applyMode}
      onApplyModeChange={setApplyMode}
      initialPaths={paths}
      initialTrigger={item.description}
      triggerEnabled={applyMode !== "always"}
      initialBody={body}
      bodyLoading={loading}
      readOnly={!writable}
      onSave={
        writable
          ? (draft) =>
              onSave(
                { ...item, version },
                {
                  name: draft.title,
                  description:
                    (draft.applyMode ?? applyMode) === "always"
                      ? item.description
                      : draft.trigger,
                  body: draft.body,
                  applyMode: draft.applyMode ?? applyMode,
                  paths: draft.paths ?? paths,
                },
              )
          : undefined
      }
    />
  );
}
