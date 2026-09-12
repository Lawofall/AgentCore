import { InlineInput } from "@/components/files/FileTreeInline";
import {
  FACE_META,
  FACE_ORDER,
  RESIDENT_LABEL,
} from "@/components/tools/catalogMeta";
import {
  Badge,
  Button,
  CATALOG_GRID_CLASS,
  CatalogTile,
} from "@/components/ui";
import { artifactColorVar, catalogCategoryColorVar } from "@/lib/catalogColors";
import type {
  PromptCatalogItem,
  PromptRail,
  PromptRailFolder,
} from "@/lib/promptCatalog";
import { buildAlwaysRows, formatAlwaysRowChars } from "@/lib/promptSizes";
import { cn } from "@/lib/utils";
import {
  BookOpen,
  FileText,
  FolderPlus,
  Plus,
  ScrollText,
  SlidersHorizontal,
  Unplug,
  User,
  UserRound,
  Wrench,
} from "lucide-react";
import type { DragEvent, ReactNode } from "react";
import { useMemo } from "react";

export type PromptDropDest =
  | { kind: "root" }
  | { kind: "folder"; folder: PromptRailFolder };

export type PromptOverviewConnector = {
  id: string;
  label: string;
  description: string;
  accessory?: ReactNode;
};

const RAIL_SHELL = "rounded-xl border border-border bg-card/60 p-4";

export function PromptOverview({
  rail,
  selectedId,
  dropDest,
  otherFolder,
  connectors,
  connectorError,
  showConnectors,
  creatingFolder,
  busy,
  installedCopyIds,
  onOpenItem,
  onOpenUpdates,
  onCreateMine,
  onStartCreateFolder,
  onSubmitCreateFolder,
  onCancelCreateFolder,
  onAddConnector,
  onAcceptAlwaysDrag,
  onDropAlways,
  onAcceptFolderDrag,
  onDropFolder,
  onRejectDrag,
  renderMineTile,
}: {
  rail: PromptRail;
  selectedId: string | null;
  dropDest: PromptDropDest | null;
  otherFolder: PromptRailFolder;
  connectors: PromptOverviewConnector[];
  connectorError: string | null;
  showConnectors: boolean;
  creatingFolder: boolean;
  busy: boolean;
  installedCopyIds: Set<string>;
  onOpenItem: (id: string) => void;
  onOpenUpdates: () => void;
  onCreateMine: () => void;
  onStartCreateFolder: () => void;
  onSubmitCreateFolder: (name: string) => void;
  onCancelCreateFolder: () => void;
  onAddConnector: (() => void) | null;
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
  const alwaysRows = useMemo(() => buildAlwaysRows(rail), [rail]);
  const constitutionRows = alwaysRows.filter(
    (row) => row.item.kind === "shared" || row.item.kind === "identity",
  );
  const memoryRows = alwaysRows.filter(
    (row) => row.item.kind === "mine" && row.item.memoryKind,
  );
  const alwaysMineRows = alwaysRows.filter(
    (row) => row.item.kind === "mine" && !row.item.memoryKind,
  );
  const toolGroups = useMemo(() => groupToolsByFace(rail.tools), [rail.tools]);
  const showTools = showConnectors || rail.tools.length > 0;

  return (
    <div className="flex w-full flex-col gap-8" data-testid="prompt-overview">
      <section
        data-testid="prompt-rail-always"
        data-prompt-drop="root"
        className={cn(
          RAIL_SHELL,
          dropDest?.kind === "root" && "ring-1 ring-inset ring-primary",
        )}
        onDragOver={onAcceptAlwaysDrag}
        onDrop={onDropAlways}
      >
        <RailHeading
          description="每回合都带着"
          actions={
            <div data-testid="prompt-overview-updates">
              <Button
                variant="ghost"
                onClick={onOpenUpdates}
                aria-label="最近学到"
              >
                最近学到
              </Button>
            </div>
          }
        >
          常驻
        </RailHeading>
        <div className={CATALOG_GRID_CLASS}>
          {constitutionRows.map((row) => (
            <ItemTile
              key={row.catalogId}
              item={row.item}
              description={row.meta}
              subtitle={formatAlwaysRowChars(row.chars) ?? undefined}
              selected={selectedId === row.catalogId}
              installedCopyIds={installedCopyIds}
              onOpen={() => onOpenItem(row.catalogId)}
              renderMineTile={renderMineTile}
            />
          ))}
          {memoryRows.length > 0 ? (
            <div data-testid="prompt-rail-memory" className="contents">
              {memoryRows.map((row) => (
                <ItemTile
                  key={row.catalogId}
                  item={row.item}
                  description={row.meta}
                  subtitle={formatAlwaysRowChars(row.chars) ?? undefined}
                  selected={selectedId === row.catalogId}
                  installedCopyIds={installedCopyIds}
                  onOpen={() => onOpenItem(row.catalogId)}
                  renderMineTile={renderMineTile}
                />
              ))}
            </div>
          ) : null}
          {alwaysMineRows.map((row) => (
            <ItemTile
              key={row.catalogId}
              item={row.item}
              description={row.meta}
              subtitle={formatAlwaysRowChars(row.chars) ?? undefined}
              selected={selectedId === row.catalogId}
              installedCopyIds={installedCopyIds}
              onOpen={() => onOpenItem(row.catalogId)}
              renderMineTile={renderMineTile}
            />
          ))}
        </div>
      </section>

      <section
        data-testid="prompt-rail-on-demand"
        data-prompt-drop="folder"
        className={RAIL_SHELL}
        onDragOver={(event) => onAcceptFolderDrag(event, otherFolder)}
        onDrop={(event) => onDropFolder(event, otherFolder)}
      >
        <RailHeading description="用到才翻">按需</RailHeading>
        <div data-testid="prompt-rail-create" className={CATALOG_GRID_CLASS}>
          <CatalogTile
            icon={<Plus size={18} />}
            colorVar={artifactColorVar("guidelines")}
            title="新建条目"
            description="写一条按需提示词"
            onClick={busy ? undefined : onCreateMine}
          />
          {creatingFolder ? (
            <div className="flex min-h-[7.5rem] items-center rounded-xl border border-border px-4">
              <InlineInput
                initial=""
                ariaLabel="夹名称"
                onSubmit={onSubmitCreateFolder}
                onCancel={onCancelCreateFolder}
              />
            </div>
          ) : (
            <CatalogTile
              icon={<FolderPlus size={18} />}
              colorVar={artifactColorVar("guidelines")}
              title="新建夹"
              description="给提示词分组"
              onClick={busy ? undefined : onStartCreateFolder}
            />
          )}
        </div>

        {rail.folders.length > 0 ? (
          <div data-testid="my-skills">
            {rail.folders.map((folder) => {
              const highlighted =
                dropDest?.kind === "folder" && dropDest.folder.id === folder.id;
              return (
                <ShelfBlock
                  key={folder.id}
                  title={folder.name}
                  highlighted={highlighted}
                  onDragOver={(event) => onAcceptFolderDrag(event, folder)}
                  onDrop={(event) => onDropFolder(event, folder)}
                >
                  {folder.items.length === 0 ? (
                    <CatalogTile
                      icon={<Plus size={18} />}
                      colorVar={artifactColorVar("guidelines")}
                      title="拖到这里"
                      description="放到这个夹"
                      className="border-dashed"
                    />
                  ) : (
                    folder.items.map((item) => (
                      <ItemTile
                        key={item.id}
                        item={item}
                        description={mineDescription(item)}
                        selected={selectedId === item.id}
                        installedCopyIds={installedCopyIds}
                        onOpen={() => onOpenItem(item.id)}
                        renderMineTile={renderMineTile}
                      />
                    ))
                  )}
                </ShelfBlock>
              );
            })}
          </div>
        ) : null}

        {rail.official.length > 0 ? (
          <ShelfBlock
            testId="prompt-rail-official"
            title="官方"
            highlighted={false}
            onDragOver={onRejectDrag}
            onDrop={onRejectDrag}
          >
            {rail.official.map((item) => (
              <ItemTile
                key={item.id}
                item={item}
                description={skillDescription(item)}
                selected={selectedId === item.id}
                installedCopyIds={installedCopyIds}
                onOpen={() => onOpenItem(item.id)}
              />
            ))}
          </ShelfBlock>
        ) : null}
      </section>

      {showTools ? (
        <section
          data-testid="prompt-rail-tools"
          className={RAIL_SHELL}
          onDragOver={onRejectDrag}
          onDrop={onRejectDrag}
        >
          <RailHeading description="开场即用或查阅后启用，拖不动">
            工具
          </RailHeading>
          {showConnectors ? (
            <ShelfBlock
              testId="prompt-rail-connectors"
              title="连接器"
              highlighted={false}
              className="mt-0"
              onDragOver={onRejectDrag}
              onDrop={onRejectDrag}
            >
              {connectors.map((row) => (
                <CatalogTile
                  key={row.id}
                  icon={<Unplug size={18} />}
                  colorVar={artifactColorVar("connectors")}
                  title={row.label}
                  description={row.description || undefined}
                  accessory={row.accessory}
                  className={
                    selectedId === row.id
                      ? "ring-1 ring-inset ring-primary"
                      : undefined
                  }
                  onClick={() => onOpenItem(row.id)}
                />
              ))}
              {onAddConnector ? (
                <CatalogTile
                  icon={<Plus size={18} />}
                  colorVar={artifactColorVar("connectors")}
                  title="添加连接器"
                  description="本机插头"
                  onClick={onAddConnector}
                />
              ) : null}
              {connectorError ? (
                <p
                  className="col-span-full text-xs text-muted-foreground"
                  role="alert"
                >
                  {connectorError}
                </p>
              ) : null}
            </ShelfBlock>
          ) : null}
          {toolGroups.map((group, index) => (
            <ShelfBlock
              key={group.face}
              testId={`prompt-rail-tools-${group.face}`}
              title={group.title}
              highlighted={false}
              className={index === 0 && !showConnectors ? "mt-0" : undefined}
              onDragOver={onRejectDrag}
              onDrop={onRejectDrag}
            >
              {group.items.map((item) => (
                <ItemTile
                  key={item.id}
                  item={item}
                  description={item.tool.summary}
                  selected={selectedId === item.id}
                  installedCopyIds={installedCopyIds}
                  onOpen={() => onOpenItem(item.id)}
                />
              ))}
            </ShelfBlock>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function groupToolsByFace(
  tools: Extract<PromptCatalogItem, { kind: "tool" }>[],
): {
  face: string;
  title: string;
  items: Extract<PromptCatalogItem, { kind: "tool" }>[];
}[] {
  const buckets = new Map<
    string,
    Extract<PromptCatalogItem, { kind: "tool" }>[]
  >();
  for (const item of tools) {
    const key = item.tool.face;
    const list = buckets.get(key);
    if (list) list.push(item);
    else buckets.set(key, [item]);
  }
  const groups: {
    face: string;
    title: string;
    items: Extract<PromptCatalogItem, { kind: "tool" }>[];
  }[] = [];
  for (const face of FACE_ORDER) {
    const items = buckets.get(face);
    if (!items?.length) continue;
    groups.push({ face, title: FACE_META[face].label, items });
    buckets.delete(face);
  }
  for (const [face, items] of buckets) {
    groups.push({ face, title: face, items });
  }
  return groups;
}

function RailHeading({
  children,
  description,
  actions,
}: {
  children: ReactNode;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-base font-medium text-foreground">{children}</h2>
        {description ? (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}

function ShelfHeading({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-xs font-medium text-muted-foreground">
      {children}
    </h3>
  );
}

function TileShelf({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <>
      <ShelfHeading>{title}</ShelfHeading>
      <div className={CATALOG_GRID_CLASS}>{children}</div>
    </>
  );
}

function ShelfBlock({
  title,
  testId,
  highlighted,
  className,
  onDragOver,
  onDrop,
  children,
}: {
  title: string;
  testId?: string;
  highlighted: boolean;
  className?: string;
  onDragOver: (event: DragEvent<HTMLDivElement>) => void;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  children: ReactNode;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "mt-5",
        className,
        highlighted && "rounded-xl ring-1 ring-inset ring-primary",
      )}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <TileShelf title={title}>{children}</TileShelf>
    </div>
  );
}

function ItemTile({
  item,
  description,
  subtitle,
  selected,
  installedCopyIds,
  onOpen,
  renderMineTile,
}: {
  item: PromptCatalogItem;
  description?: string;
  subtitle?: string;
  selected: boolean;
  installedCopyIds: Set<string>;
  onOpen: () => void;
  renderMineTile?: (row: {
    item: PromptCatalogItem;
    children: ReactNode;
  }) => ReactNode;
}) {
  const visual = tileVisual(item, installedCopyIds);
  const tile = (
    <CatalogTile
      icon={visual.icon}
      colorVar={visual.colorVar}
      title={item.label}
      subtitle={subtitle}
      description={description || undefined}
      accessory={visual.accessory}
      tags={visual.tags}
      className={selected ? "ring-1 ring-inset ring-primary" : undefined}
      onClick={onOpen}
    />
  );
  const wrapped = renderMineTile
    ? renderMineTile({ item, children: tile })
    : tile;
  return (
    <div
      data-testid={`prompt-tile-${item.id}`}
      data-prompt-tile={item.id}
      className={cn(
        "h-full min-w-0",
        item.kind === "mine" && item.disputed && "[&_h3]:line-through",
      )}
    >
      {wrapped}
    </div>
  );
}

function skillDescription(item: PromptCatalogItem): string {
  if (item.kind !== "skill") return "";
  const summary = item.skill.summary.trim();
  if (summary && summary !== item.label) return summary;
  return "";
}

function mineDescription(item: PromptCatalogItem): string {
  if (item.kind !== "mine") return "";
  const description = item.description.trim();
  if (description && description !== item.label) return description;
  return "";
}

function tileVisual(
  item: PromptCatalogItem,
  installedCopyIds: Set<string>,
): {
  icon: ReactNode;
  colorVar: string;
  accessory?: ReactNode;
  tags?: ReactNode;
} {
  if (item.kind === "shared") {
    return {
      icon: <ScrollText size={18} />,
      colorVar: artifactColorVar("guidelines"),
      accessory: sourceBadge("官方"),
    };
  }
  if (item.kind === "identity") {
    return {
      icon: <UserRound size={18} />,
      colorVar: artifactColorVar("guidelines"),
      accessory: sourceBadge("官方"),
    };
  }
  if (item.kind === "skill") {
    return {
      icon: <BookOpen size={18} />,
      colorVar: artifactColorVar("guidelines"),
      accessory: sourceBadge("官方"),
    };
  }
  if (item.kind === "tool") {
    const meta = FACE_META[item.tool.face];
    const Icon = meta?.icon ?? Wrench;
    return {
      icon: <Icon size={18} />,
      colorVar: catalogCategoryColorVar(item.tool.face),
      accessory: sourceBadge(
        item.tool.resident ? RESIDENT_LABEL.resident : RESIDENT_LABEL.deferred,
      ),
      tags: meta ? (
        <Badge tone="muted" pill>
          {meta.label}
        </Badge>
      ) : undefined,
    };
  }
  if (item.kind === "mine") {
    const fromMarket = Boolean(
      item.mineId && installedCopyIds.has(item.mineId),
    );
    const Icon =
      item.memoryKind === "preferences"
        ? SlidersHorizontal
        : item.memoryKind === "profile"
          ? User
          : FileText;
    return {
      icon: <Icon size={18} />,
      colorVar: artifactColorVar("guidelines"),
      accessory: sourceBadge(
        item.disputed ? "已停用" : fromMarket ? "市场" : "我的",
      ),
    };
  }
  return {
    icon: <FileText size={18} />,
    colorVar: artifactColorVar("guidelines"),
  };
}

function sourceBadge(label: string) {
  return (
    <Badge tone="muted" pill>
      {label}
    </Badge>
  );
}
