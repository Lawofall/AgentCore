import { MemoryUpdatesView } from "@/components/files/MemoryUpdatesView";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { PromptWorkbench } from "@/components/prompt/PromptWorkbench";
import { MemoryRecentWrites } from "@/components/tools/MemoryRecentWrites";
import { PublishSkillDialog } from "@/components/tools/PublishSkillDialog";
import { RoleIdentityBlock } from "@/components/tools/RoleIdentityBlock";
import {
  ToolInspector,
  type ToolInspectorView,
} from "@/components/tools/ToolInspector";
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
  ConnectorInspector,
  ConnectorStatusBadge,
  NEW_CONNECTOR_ID,
} from "@/pages/toolbox/ConnectorsPage";
import { getDocument } from "@/services/documents";
import { getMemoryFile } from "@/services/memory";
import type { SkillCatalog } from "@/services/skillCatalog";
import { skillBodyFromContent } from "@/services/skillCatalog";
import type { SkillStoreGroup, SkillStoreListing } from "@/services/skillStore";
import type { McpServerListItem } from "@shared/mcp-contract";
import { useEffect, useState } from "react";

export type ConnectorPick = {
  kind: "connector";
  id: string;
  label: string;
  server: McpServerListItem | null;
};

export type PromptReadLeaf = PromptCatalogItem | ConnectorPick;

type MineView = "preview" | "source";

export function PromptReadDialog({
  open,
  updatesOpen,
  item,
  overlay,
  listings,
  installedCopyIds,
  busy,
  showToolsHint,
  toolsHint,
  toolCallingNames,
  mcpApi,
  mcpBusyId,
  onOpenChange,
  onOpenUpdatesLeaf,
  onMcpBusy,
  onMcpSaved,
  onCloseNewConnector,
  onSaveMine,
  onSaveAccount,
  onPublishMine,
  onUnpublishMine,
}: {
  open: boolean;
  updatesOpen: boolean;
  item: PromptReadLeaf | null;
  overlay: SkillCatalog;
  listings: SkillStoreListing[];
  installedCopyIds: Set<string>;
  busy: boolean;
  showToolsHint: boolean;
  toolsHint: string;
  toolCallingNames: ReadonlySet<string>;
  mcpApi: Window["mcpApi"];
  mcpBusyId: string | null;
  onOpenChange: (open: boolean) => void;
  onOpenUpdatesLeaf: (
    path: string,
    name: string,
    projectId?: string | null,
  ) => void;
  onMcpBusy: (id: string | null) => void;
  onMcpSaved: () => Promise<void>;
  onCloseNewConnector: () => void;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => Promise<boolean>;
  onSaveAccount: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; body: string; version: string },
  ) => Promise<boolean>;
  onPublishMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    group: SkillStoreGroup,
  ) => void;
  onUnpublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  const [mineView, setMineView] = useState<MineView>("source");
  const [toolView, setToolView] = useState<ToolInspectorView>("guide");
  const [publishOpen, setPublishOpen] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: item id is an intentional re-run key
  useEffect(() => {
    setMineView("source");
    setToolView("guide");
    setPublishOpen(false);
  }, [item && "id" in item ? item.id : null]);

  const showItem = item != null && !updatesOpen;
  const mineItem =
    item?.kind === "mine"
      ? (item as Extract<PromptCatalogItem, { kind: "mine" }>)
      : null;
  const fromMarket = Boolean(
    mineItem?.mineId && installedCopyIds.has(mineItem.mineId),
  );
  const listing = mineItem
    ? (listings.find((row) => row.documentId === mineItem.mineId) ?? null)
    : null;
  const header = readHeader({
    updatesOpen,
    showItem,
    item,
    installedCopyIds,
    listing,
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
  const showPublish = Boolean(mineItem) && showItem && !mineItem?.memoryKind;
  const toolItem = item?.kind === "tool" ? item : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && publishOpen) return;
        onOpenChange(next);
      }}
    >
      <DialogContent
        size="lg"
        className="flex max-h-[min(80vh,36rem)] flex-col"
        data-testid="prompt-read-dialog"
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
        <DialogHeader>
          <div className="flex min-w-0 items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <DialogTitle>{header.title}</DialogTitle>
                {header.badge ? (
                  <Badge tone="muted">{header.badge}</Badge>
                ) : null}
                {header.extraBadge ? (
                  <Badge tone="muted">{header.extraBadge}</Badge>
                ) : null}
                {item?.kind === "connector" && item.server ? (
                  <ConnectorStatusBadge server={item.server} />
                ) : null}
              </div>
              {mineItem && showItem ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <SegmentedControl
                    aria-label="阅读方式"
                    value={mineView}
                    onChange={setMineView}
                    items={[
                      { value: "preview", label: "预览" },
                      { value: "source", label: "源码" },
                    ]}
                    className="w-auto"
                  />
                  {canPublish ? (
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => setPublishOpen(true)}
                    >
                      上架
                    </Button>
                  ) : null}
                  {canUnpublish ? (
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => mineItem && onUnpublishMine(mineItem)}
                    >
                      下架
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {toolItem && showItem ? (
                <div className="mt-2">
                  <SegmentedControl
                    aria-label="阅读方式"
                    value={toolView}
                    onChange={setToolView}
                    items={[
                      { value: "guide", label: "说明" },
                      { value: "source", label: "源码" },
                    ]}
                    className="w-auto"
                  />
                </div>
              ) : null}
            </div>
          </div>
          <DialogDescription className="sr-only">
            {header.description}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex min-h-0 flex-1 flex-col pb-5">
          {updatesOpen ? (
            <div className="min-h-0 flex-1">
              <MemoryUpdatesView onOpenLeaf={onOpenUpdatesLeaf} />
            </div>
          ) : showItem && item ? (
            <ReadBody
              item={item}
              mineView={mineView}
              toolView={toolView}
              overlay={overlay}
              showToolsHint={showToolsHint}
              toolsHint={toolsHint}
              toolCallingNames={toolCallingNames}
              mcpApi={mcpApi}
              mcpBusyId={mcpBusyId}
              onMcpBusy={onMcpBusy}
              onMcpSaved={onMcpSaved}
              onCloseNewConnector={onCloseNewConnector}
              onSaveMine={onSaveMine}
              onSaveAccount={onSaveAccount}
            />
          ) : null}
        </DialogBody>
        {showPublish && mineItem ? (
          <PublishSkillDialog
            open={publishOpen}
            busy={busy}
            initialGroup={listing?.group ?? null}
            onOpenChange={setPublishOpen}
            onConfirm={(group) => {
              setPublishOpen(false);
              onPublishMine(mineItem, group);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function readHeader({
  updatesOpen,
  showItem,
  item,
  installedCopyIds,
  listing,
}: {
  updatesOpen: boolean;
  showItem: boolean;
  item: PromptReadLeaf | null;
  installedCopyIds: Set<string>;
  listing: SkillStoreListing | null;
}): {
  title: string;
  badge: string | null;
  extraBadge: string | null;
  description: string;
} {
  if (updatesOpen) {
    return {
      title: "最近学到",
      badge: null,
      extraBadge: null,
      description: "跨对话流水账",
    };
  }
  if (showItem && item) {
    if (item.kind === "connector") {
      return {
        title: item.id === NEW_CONNECTOR_ID ? "新建连接器" : "编辑连接器",
        badge: null,
        extraBadge: null,
        description: item.label,
      };
    }
    if (item.kind === "tool") {
      return {
        title: item.label,
        badge: item.tool.resident ? "开场即用" : "查阅后启用",
        extraBadge: null,
        description: item.tool.summary,
      };
    }
    if (item.kind === "mine") {
      const fromMarket = Boolean(
        item.mineId && installedCopyIds.has(item.mineId),
      );
      const extra =
        listing?.status === "published"
          ? "已上架"
          : listing?.status === "taken_down"
            ? "平台已下架"
            : item.aiMaintained
              ? "AI 可能改"
              : null;
      return {
        title: item.label,
        badge: fromMarket ? "市场" : "我的",
        extraBadge: extra,
        description: item.description || item.label,
      };
    }
    if (item.kind === "identity") {
      return {
        title: item.label,
        badge: "官方",
        extraBadge: "三选一",
        description: "角色身份",
      };
    }
    return {
      title: item.label,
      badge: "官方",
      extraBadge: null,
      description: item.label,
    };
  }
  return {
    title: "提示词",
    badge: null,
    extraBadge: null,
    description: "提示词",
  };
}

function ReadBody({
  item,
  mineView,
  toolView,
  overlay,
  showToolsHint,
  toolsHint,
  toolCallingNames,
  mcpApi,
  mcpBusyId,
  onMcpBusy,
  onMcpSaved,
  onCloseNewConnector,
  onSaveMine,
  onSaveAccount,
}: {
  item: PromptReadLeaf;
  mineView: MineView;
  toolView: ToolInspectorView;
  overlay: SkillCatalog;
  showToolsHint: boolean;
  toolsHint: string;
  toolCallingNames: ReadonlySet<string>;
  mcpApi: Window["mcpApi"];
  mcpBusyId: string | null;
  onMcpBusy: (id: string | null) => void;
  onMcpSaved: () => Promise<void>;
  onCloseNewConnector: () => void;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => Promise<boolean>;
  onSaveAccount: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; body: string; version: string },
  ) => Promise<boolean>;
}) {
  if (item.kind === "connector") {
    if (!mcpApi) return null;
    return (
      <ConnectorInspector
        key={item.id}
        server={item.server}
        api={mcpApi}
        busyId={mcpBusyId}
        onBusy={onMcpBusy}
        onCloseNew={onCloseNewConnector}
        onSaved={onMcpSaved}
        hideChrome
      />
    );
  }

  if (item.kind === "tool") {
    return (
      <ToolInspector
        key={item.id}
        tool={item.tool}
        hideChrome
        view={toolView}
        capabilityHint={
          showToolsHint && toolCallingNames.has(item.tool.name)
            ? toolsHint
            : undefined
        }
      />
    );
  }

  if (item.kind === "identity") {
    return (
      <RoleIdentityBlock
        ceoIdentity={item.ceoIdentity}
        nestedIdentity={item.nestedIdentity}
        leafIdentity={item.leafIdentity}
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

  if (item.kind === "mine" && item.memoryKind) {
    return (
      <AccountEntryEditor item={item} view={mineView} onSave={onSaveAccount} />
    );
  }

  if (item.kind === "mine") {
    return (
      <MineSkillEditor
        item={item}
        view={mineView}
        writable={overlay.writable}
        onSave={onSaveMine}
      />
    );
  }

  return null;
}

function MineSkillEditor({
  item,
  view,
  writable,
  onSave,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  view: MineView;
  writable: boolean;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => Promise<boolean>;
}) {
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
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [item.mineId, item.content, item.version]);

  if (view === "preview") {
    if (loading) {
      return <p className="text-sm text-muted-foreground">加载中…</p>;
    }
    return (
      <PromptDocument
        text={body}
        compact={false}
        framed={false}
        maxHeightClass="max-h-none"
      />
    );
  }

  return (
    <PromptWorkbench
      key={loading ? `${item.id}-loading` : item.id}
      testId="mine-skill-editor"
      title={item.label}
      titleEditable
      badges={null}
      initialTrigger={item.description}
      triggerEnabled={item.applyMode === "on_demand"}
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
                  description: draft.trigger,
                  body: draft.body,
                },
              )
          : undefined
      }
    />
  );
}

function AccountEntryEditor({
  item,
  view,
  onSave,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  view: MineView;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; body: string; version: string },
  ) => Promise<boolean>;
}) {
  const [body, setBody] = useState(item.content);
  const [version, setVersion] = useState(item.version);
  const [loading, setLoading] = useState(!item.content);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        if (item.memoryKind) {
          const file = await getMemoryFile(item.memoryKind);
          if (cancelled) return;
          setBody(file.content);
          setVersion(file.version);
          setLoading(false);
          return;
        }
        if (!item.mineId) {
          setLoading(false);
          return;
        }
        const doc = await getDocument(item.mineId);
        if (cancelled) return;
        setBody(doc.content);
        setVersion(doc.version);
        setLoading(false);
      } catch {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [item.memoryKind, item.mineId]);

  const renameable = !item.memoryKind && Boolean(item.mineId);

  if (view === "preview") {
    if (loading) {
      return <p className="text-sm text-muted-foreground">加载中…</p>;
    }
    return (
      <>
        <PromptDocument
          text={body}
          compact={false}
          framed={false}
          maxHeightClass="max-h-none"
        />
        {item.memoryKind ? (
          <MemoryRecentWrites memoryKind={item.memoryKind} />
        ) : null}
      </>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <PromptWorkbench
        key={loading ? `${item.id}-loading` : item.id}
        testId="account-entry-editor"
        title={item.label}
        titleEditable={renameable}
        badges={null}
        initialBody={body}
        bodyLoading={loading}
        onSave={(draft) =>
          onSave(item, { name: draft.title, body: draft.body, version })
        }
      />
      {item.memoryKind ? (
        <MemoryRecentWrites memoryKind={item.memoryKind} />
      ) : null}
    </div>
  );
}
