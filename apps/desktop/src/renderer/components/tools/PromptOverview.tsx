import { InlineInput } from "@/components/files/FileTreeInline";
import { ASSEMBLY_CARD_GRID_CLASS, Badge, CatalogTile } from "@/components/ui";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import type {
  PromptCatalogItem,
  PromptRail,
  PromptRailFolder,
} from "@/lib/promptCatalog";
import { isPromptDrag } from "@/lib/promptCatalogDrag";
import {
  PROMPT_SHELF_AFFORDANCE,
  type PromptShelfChip,
  promptItemShelfCopy,
  promptMineShelfOpts,
} from "@/lib/promptShelfTile";
import { buildAlwaysRows } from "@/lib/promptSizes";
import { cn } from "@/lib/utils";
import type { SkillStoreListing } from "@/services/skillStore";
import { Pencil, Trash2 } from "lucide-react";
import type { DragEvent, MouseEvent, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

const EMPTY_PICKED: ReadonlySet<string> = new Set();

export type PromptDropDest =
  | { kind: "root" }
  | { kind: "folder"; folder: PromptRailFolder };

/** Assembly flag for the three factory catalog rows. The rows stay visible when off. */
export type FactoryCatalogControl = {
  present: boolean;
  canToggle: boolean;
  pending?: boolean;
  onPresentChange: (present: boolean) => void;
};

function countMeta(count: number): string | undefined {
  return count > 0 ? `${count} 条` : undefined;
}

/** Card copy stops at the first sentence. The false-friend clause stays in the dialog. */
function firstSentence(text: string): string {
  const trimmed = text.trim();
  const cut = trimmed.indexOf("。");
  if (cut === -1) return trimmed;
  return trimmed.slice(0, cut + 1);
}

function factoryRowCopy(item: Extract<PromptCatalogItem, { kind: "skill" }>): {
  title: string;
  description: string;
} {
  const summary = item.skill.summary.trim() || item.label;
  const blurb = item.skill.blurb?.trim() ?? "";
  const when = firstSentence(summary);
  if (!blurb || blurb === summary) return { title: when, description: "" };
  return { title: blurb, description: when };
}

function matchQuery(
  query: string,
  ...parts: Array<string | undefined>
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return parts.some((part) => part?.toLowerCase().includes(q));
}

const ALWAYS_DROP = "松手后下一回合会带上";
const UNFILE_DROP = "松手后不归夹";

export function PromptOverview({
  rail,
  selectedId,
  pickedIds,
  dropDest,
  otherFolder,
  busy: _busy,
  listings = [],
  installedListings = [],
  renamingFolderId = null,
  query = "",
  suppressMiss = false,
  onMissChange,
  factoryControl,
  dragging = false,
  openFolderId = null,
  onOpenFolder = () => {},
  onCloseFolder = () => {},
  onOpenItem,
  onCreateMine: _onCreateMine,
  onSubmitRenameFolder,
  onCancelRenameFolder,
  onRenameFolder,
  onDeleteFolder,
  onAcceptAlwaysDrag,
  onDropAlways,
  onAcceptFolderDrag,
  onDropFolder,
  onRejectDrag,
  renderMineTile,
}: {
  rail: PromptRail;
  selectedId: string | null;
  /** 我的 / 市场多选高亮（catalog id）。与 selectedId（当前读卡）分开。 */
  pickedIds?: ReadonlySet<string>;
  dropDest: PromptDropDest | null;
  otherFolder: PromptRailFolder;
  busy: boolean;
  listings?: SkillStoreListing[];
  installedListings?: SkillStoreListing[];
  renamingFolderId?: string | null;
  query?: string;
  /** Page-level search draws the miss line. This overview stays quiet. */
  suppressMiss?: boolean;
  onMissChange?: (miss: boolean) => void;
  factoryControl?: FactoryCatalogControl;
  /** True while a prompt card is being dragged. Drop strips exist only then. */
  dragging?: boolean;
  /** Named folder the person has opened. Null is the root shelf. */
  openFolderId?: string | null;
  onOpenFolder?: (id: string) => void;
  onCloseFolder?: () => void;
  onOpenItem: (
    id: string,
    event?: Pick<MouseEvent, "ctrlKey" | "metaKey" | "shiftKey">,
  ) => void;
  onCreateMine: () => void;
  onSubmitRenameFolder: (id: string, name: string) => void;
  onCancelRenameFolder: () => void;
  onRenameFolder?: (folder: PromptRailFolder) => void;
  onDeleteFolder?: (folder: PromptRailFolder) => void;
  onAcceptAlwaysDrag: (event: DragEvent) => void;
  onDropAlways: (event: DragEvent) => void;
  onAcceptFolderDrag: (event: DragEvent, folder: PromptRailFolder) => void;
  onDropFolder: (event: DragEvent, folder: PromptRailFolder) => void;
  onRejectDrag: (event: DragEvent) => void;
  renderMineTile?: (row: {
    item: PromptCatalogItem;
    children: ReactNode;
  }) => ReactNode;
}) {
  const picked = pickedIds ?? EMPTY_PICKED;
  const q = query.trim();
  const [hoverDrag, setHoverDrag] = useState(false);
  const showStrips = dragging || hoverDrag;
  const alwaysRows = useMemo(
    () => buildAlwaysRows(rail).filter((row) => row.item.kind === "mine"),
    [rail],
  );
  const visibleAlways = alwaysRows.filter((row) => {
    const copy = promptItemShelfCopy(row.item, { alwaysChars: row.chars });
    return matchQuery(q, copy.title, copy.description, row.label);
  });
  const pathItems = (rail.pathMine ?? []).filter((item) => {
    const copy = promptItemShelfCopy(item);
    return matchQuery(q, copy.title, copy.description, item.label);
  });
  const showPaths = pathItems.length > 0;
  const itemMatches = (item: PromptCatalogItem) => {
    const copy = promptItemShelfCopy(item);
    return matchQuery(q, copy.title, copy.description, item.label);
  };
  const looseItems = (
    rail.folders.find((folder) => folder.source === "other")?.items ?? []
  ).filter(itemMatches);
  const namedFolders = rail.folders.filter(
    (folder) => folder.source !== "other",
  );
  const openFolder =
    namedFolders.find((folder) => folder.id === openFolderId) ?? null;
  /** Search leaves the folder and lays matching cards on the root shelf. */
  const drilled = Boolean(openFolder) && !q;
  const filedOnRoot = q
    ? namedFolders.flatMap((folder) => folder.items.filter(itemMatches))
    : [];
  const folderTiles = namedFolders.filter(
    (folder) => !q || matchQuery(q, folder.name),
  );
  const constitution = rail.constitution.filter((item) => {
    if (item.kind === "shared" && item.text.trim() === "") return false;
    const copy = promptItemShelfCopy(item);
    return matchQuery(q, copy.title, copy.description, item.label);
  });
  const showConstitution = constitution.length > 0;
  const factoryRows = rail.official.flatMap((item) => {
    if (item.kind !== "skill") return [];
    const copy = factoryRowCopy(item);
    return matchQuery(q, copy.title, copy.description, item.label)
      ? [{ item, copy }]
      : [];
  });
  const showFactory = factoryRows.length > 0;
  const promptMiss =
    Boolean(q) &&
    visibleAlways.length === 0 &&
    !showPaths &&
    !showConstitution &&
    !showFactory &&
    looseItems.length === 0 &&
    filedOnRoot.length === 0 &&
    folderTiles.length === 0;
  useEffect(() => {
    onMissChange?.(promptMiss);
  }, [onMissChange, promptMiss]);
  const emptySearch = promptMiss && !suppressMiss;
  const looseHot =
    dropDest?.kind === "folder" && dropDest.folder.id === otherFolder.id;
  const alwaysHot = dropDest?.kind === "root";
  const factoryDim = Boolean(factoryControl && !factoryControl.present);
  const hasGrid =
    showFactory ||
    visibleAlways.length > 0 ||
    looseItems.length > 0 ||
    filedOnRoot.length > 0 ||
    folderTiles.length > 0;

  function mineCard(item: PromptCatalogItem, alwaysChars?: number) {
    return (
      <ItemCard
        key={item.id}
        item={item}
        alwaysChars={alwaysChars}
        selected={selectedId === item.id}
        picked={picked.has(item.id)}
        listings={listings}
        installedListings={installedListings}
        onOpen={(event) => onOpenItem(item.id, event)}
        renderMineTile={renderMineTile}
      />
    );
  }

  const dropStrips = showStrips ? (
    <div className="flex flex-col gap-2">
      <div
        data-testid="prompt-rail-always"
        data-prompt-drop="root"
        onDragOver={(event) => {
          event.stopPropagation();
          onAcceptAlwaysDrag(event);
        }}
        onDrop={(event) => {
          event.stopPropagation();
          setHoverDrag(false);
          onDropAlways(event);
        }}
      >
        <DropWell highlighted={alwaysHot}>{ALWAYS_DROP}</DropWell>
      </div>
      {drilled ? (
        <div
          data-testid="prompt-rail-unfile"
          onDragOver={(event) => {
            event.stopPropagation();
            onAcceptFolderDrag(event, otherFolder);
          }}
          onDrop={(event) => {
            event.stopPropagation();
            setHoverDrag(false);
            onDropFolder(event, otherFolder);
          }}
        >
          <DropWell
            highlighted={
              dropDest?.kind === "folder" &&
              dropDest.folder.id === otherFolder.id
            }
          >
            {UNFILE_DROP}
          </DropWell>
        </div>
      ) : null}
    </div>
  ) : null;

  return (
    <div
      className="flex w-full flex-col gap-8"
      data-testid="prompt-overview"
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node)) return;
        setHoverDrag(false);
      }}
    >
      {showPaths ? (
        <section
          data-testid="prompt-rail-paths"
          className="min-w-0"
          onDragOver={onRejectDrag}
        >
          <RailHeading meta={countMeta(pathItems.length)}>碰到文件</RailHeading>
          <div className={cn("mt-3", ASSEMBLY_CARD_GRID_CLASS)}>
            {pathItems.map((item) => (
              <ItemCard
                key={item.id}
                item={item}
                selected={selectedId === item.id}
                picked={picked.has(item.id)}
                listings={listings}
                installedListings={installedListings}
                onOpen={(event) => onOpenItem(item.id, event)}
                renderMineTile={renderMineTile}
              />
            ))}
          </div>
        </section>
      ) : null}

      {showConstitution ? (
        <section
          data-testid="prompt-rail-constitution"
          className="min-w-0"
          onDragOver={onRejectDrag}
          onDrop={onRejectDrag}
        >
          <RailHeading meta={countMeta(constitution.length)}>准则</RailHeading>
          <div className={cn("mt-3", ASSEMBLY_CARD_GRID_CLASS)}>
            {constitution.map((item) => {
              const copy = promptItemShelfCopy(item);
              return (
                <CatalogTile
                  key={item.id}
                  density="compact"
                  title={copy.title}
                  description={copy.description}
                  onClick={(event) => onOpenItem(item.id, event)}
                  className={
                    selectedId === item.id
                      ? "border-foreground/20 bg-muted/50"
                      : undefined
                  }
                />
              );
            })}
          </div>
        </section>
      ) : null}

      {drilled && openFolder ? (
        <FolderView
          folder={openFolder}
          dropDest={dropDest}
          renamingFolderId={renamingFolderId}
          dropStrips={dropStrips}
          onCloseFolder={onCloseFolder}
          onSubmitRenameFolder={onSubmitRenameFolder}
          onCancelRenameFolder={onCancelRenameFolder}
          onRenameFolder={onRenameFolder}
          onDeleteFolder={onDeleteFolder}
          onAcceptFolderDrag={onAcceptFolderDrag}
          onDropFolder={onDropFolder}
          onHoverDrag={(event) => {
            if (isPromptDrag(Array.from(event.dataTransfer.types))) {
              setHoverDrag(true);
            }
          }}
          onHoverDragEnd={() => setHoverDrag(false)}
        >
          {openFolder.items.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              {PROMPT_SHELF_AFFORDANCE.dropHere.title}
            </p>
          ) : (
            <div className={cn("mt-3", ASSEMBLY_CARD_GRID_CLASS)}>
              {openFolder.items.map((item) => mineCard(item))}
            </div>
          )}
        </FolderView>
      ) : (
        <section
          data-testid="prompt-rail-shelf"
          data-prompt-drop="folder"
          className={cn("min-w-0", looseHot && "rounded-xl ring-2 ring-ring")}
          onDragOver={(event) => {
            if (isPromptDrag(Array.from(event.dataTransfer.types))) {
              setHoverDrag(true);
            }
            onAcceptFolderDrag(event, otherFolder);
          }}
          onDrop={(event) => {
            setHoverDrag(false);
            onDropFolder(event, otherFolder);
          }}
        >
          {dropStrips}
          {hasGrid ? (
            <div className={cn(showStrips && "mt-3", ASSEMBLY_CARD_GRID_CLASS)}>
              {showFactory ? (
                <div className="contents" data-testid="prompt-rail-factory">
                  {factoryRows.map(({ item, copy }) => (
                    <CatalogTile
                      key={item.id}
                      density="compact"
                      accessoryPlacement="description"
                      title={copy.title}
                      description={copy.description}
                      accessory={
                        <Badge tone="muted" pill>
                          官方
                        </Badge>
                      }
                      onClick={(event) => onOpenItem(item.id, event)}
                      className={cn(
                        factoryDim && "opacity-50",
                        selectedId === item.id &&
                          "border-foreground/20 bg-muted/50",
                      )}
                    />
                  ))}
                </div>
              ) : null}
              {visibleAlways.map((row) => mineCard(row.item, row.chars))}
              {looseItems.map((item) => mineCard(item))}
              {filedOnRoot.map((item) => mineCard(item))}
              {folderTiles.map((folder) => (
                <FolderTile
                  key={folder.id}
                  folder={folder}
                  highlighted={
                    dropDest?.kind === "folder" &&
                    dropDest.folder.id === folder.id
                  }
                  renaming={
                    Boolean(renamingFolderId) &&
                    renamingFolderId === folder.documentId
                  }
                  onOpen={() => onOpenFolder(folder.id)}
                  onSubmitRename={onSubmitRenameFolder}
                  onCancelRename={onCancelRenameFolder}
                  onRename={onRenameFolder}
                  onDelete={onDeleteFolder}
                  onDragOver={(event) => {
                    event.stopPropagation();
                    onAcceptFolderDrag(event, folder);
                  }}
                  onDrop={(event) => {
                    event.stopPropagation();
                    onDropFolder(event, folder);
                  }}
                />
              ))}
            </div>
          ) : null}
        </section>
      )}

      {emptySearch ? (
        <p className="text-sm text-muted-foreground">没有匹配「{q}」的条目。</p>
      ) : null}
    </div>
  );
}

function FolderView({
  folder,
  dropDest,
  renamingFolderId,
  dropStrips,
  onCloseFolder,
  onSubmitRenameFolder,
  onCancelRenameFolder,
  onRenameFolder,
  onDeleteFolder,
  onAcceptFolderDrag,
  onDropFolder,
  onHoverDrag,
  onHoverDragEnd,
  children,
}: {
  folder: PromptRailFolder;
  dropDest: PromptDropDest | null;
  renamingFolderId: string | null;
  dropStrips: ReactNode;
  onCloseFolder: () => void;
  onSubmitRenameFolder: (id: string, name: string) => void;
  onCancelRenameFolder: () => void;
  onRenameFolder?: (folder: PromptRailFolder) => void;
  onDeleteFolder?: (folder: PromptRailFolder) => void;
  onAcceptFolderDrag: (event: DragEvent, folder: PromptRailFolder) => void;
  onDropFolder: (event: DragEvent, folder: PromptRailFolder) => void;
  onHoverDrag: (event: DragEvent) => void;
  onHoverDragEnd: () => void;
  children: ReactNode;
}) {
  const highlighted =
    dropDest?.kind === "folder" && dropDest.folder.id === folder.id;
  const renaming =
    Boolean(renamingFolderId) && renamingFolderId === folder.documentId;
  return (
    <section
      data-testid="prompt-folder-open"
      data-prompt-folder={folder.id}
      className={cn("min-w-0", highlighted && "rounded-xl ring-2 ring-ring")}
      onDragOver={(event) => {
        onHoverDrag(event);
        onAcceptFolderDrag(event, folder);
      }}
      onDrop={(event) => {
        onHoverDragEnd();
        onDropFolder(event, folder);
      }}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          aria-label="返回"
          className="rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onCloseFolder}
        >
          返回
        </button>
        {renaming && folder.documentId ? (
          <InlineInput
            initial={folder.name}
            ariaLabel="夹名称"
            commitOnBlur
            onSubmit={(value) =>
              onSubmitRenameFolder(folder.documentId as string, value)
            }
            onCancel={onCancelRenameFolder}
          />
        ) : (
          <FolderMenu
            folder={folder}
            onRename={onRenameFolder}
            onDelete={onDeleteFolder}
          >
            <h3 className="text-sm font-medium text-foreground">
              {folder.name}
            </h3>
          </FolderMenu>
        )}
        {folder.items.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {folder.items.length} 条
          </span>
        ) : null}
      </div>
      {dropStrips ? <div className="mt-3">{dropStrips}</div> : null}
      {children}
    </section>
  );
}

function FolderTile({
  folder,
  highlighted,
  renaming,
  onOpen,
  onSubmitRename,
  onCancelRename,
  onRename,
  onDelete,
  onDragOver,
  onDrop,
}: {
  folder: PromptRailFolder;
  highlighted: boolean;
  renaming: boolean;
  onOpen: () => void;
  onSubmitRename: (id: string, name: string) => void;
  onCancelRename: () => void;
  onRename?: (folder: PromptRailFolder) => void;
  onDelete?: (folder: PromptRailFolder) => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: (event: DragEvent) => void;
}) {
  if (renaming && folder.documentId) {
    return (
      <div
        data-prompt-folder={folder.id}
        className="flex min-h-10 min-w-0 items-center rounded-xl border border-border px-3"
        onDragOver={onDragOver}
        onDrop={onDrop}
      >
        <InlineInput
          initial={folder.name}
          ariaLabel="夹名称"
          commitOnBlur
          onSubmit={(value) =>
            onSubmitRename(folder.documentId as string, value)
          }
          onCancel={onCancelRename}
        />
      </div>
    );
  }
  return (
    <FolderMenu folder={folder} onRename={onRename} onDelete={onDelete}>
      <div
        data-prompt-folder={folder.id}
        className="min-w-0"
        onDragOver={onDragOver}
        onDrop={onDrop}
      >
        <CatalogTile
          density="compact"
          title={folder.name}
          accessory={
            folder.items.length > 0 ? (
              <span className="text-xs text-muted-foreground">
                {folder.items.length} 条
              </span>
            ) : undefined
          }
          onClick={onOpen}
          className={highlighted ? "ring-2 ring-ring" : undefined}
        />
      </div>
    </FolderMenu>
  );
}

function FolderMenu({
  folder,
  onRename,
  onDelete,
  children,
}: {
  folder: PromptRailFolder;
  onRename?: (folder: PromptRailFolder) => void;
  onDelete?: (folder: PromptRailFolder) => void;
  children: ReactNode;
}) {
  if (
    !onRename ||
    !onDelete ||
    folder.source !== "user" ||
    !folder.documentId
  ) {
    return children;
  }
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent>
        <ContextMenuItem onSelect={() => onRename(folder)}>
          <Pencil size={14} className="shrink-0" />
          重命名
        </ContextMenuItem>
        <ContextMenuItem variant="danger" onSelect={() => onDelete(folder)}>
          <Trash2 size={14} className="shrink-0" />
          删除
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}

function DropWell({
  children,
  highlighted,
  className,
}: {
  children: ReactNode;
  highlighted?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-11 items-center rounded-xl border border-dashed border-border px-3 text-left text-sm text-muted-foreground",
        highlighted && "ring-2 ring-ring",
        className,
      )}
    >
      {children}
    </div>
  );
}

function RailHeading({
  children,
  meta,
}: {
  children: ReactNode;
  meta?: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <h2 className="text-sm font-semibold text-foreground">{children}</h2>
      {meta ? (
        <span className="text-xs text-muted-foreground">{meta}</span>
      ) : null}
    </div>
  );
}

function ItemCard({
  item,
  alwaysChars,
  selected,
  picked,
  listings,
  installedListings,
  onOpen,
  renderMineTile,
}: {
  item: PromptCatalogItem;
  alwaysChars?: number;
  selected: boolean;
  picked?: boolean;
  listings: SkillStoreListing[];
  installedListings: SkillStoreListing[];
  onOpen: (event: MouseEvent<HTMLButtonElement>) => void;
  renderMineTile?: (row: {
    item: PromptCatalogItem;
    children: ReactNode;
  }) => ReactNode;
}) {
  const mineOpts =
    item.kind === "mine"
      ? promptMineShelfOpts(item, listings, installedListings)
      : {};
  const copy = promptItemShelfCopy(item, { ...mineOpts, alwaysChars });
  const card = (
    <CatalogTile
      density="compact"
      title={copy.title}
      description={copy.description}
      accessory={
        copy.accessory.length ? (
          <>{shelfBadgeList(copy.accessory)}</>
        ) : undefined
      }
      onClick={onOpen}
      className={
        picked || selected ? "border-foreground/20 bg-muted/50" : undefined
      }
    />
  );
  const wrapped = renderMineTile
    ? renderMineTile({ item, children: card })
    : card;
  return (
    <div
      data-testid={`prompt-tile-${item.id}`}
      data-prompt-tile={item.id}
      className="min-w-0"
    >
      {wrapped}
    </div>
  );
}

function shelfBadgeList(chips: PromptShelfChip[]) {
  return chips.map((chip) => (
    <Badge key={chip.label} tone={chip.tone ?? "muted"} pill>
      {chip.label}
    </Badge>
  ));
}
