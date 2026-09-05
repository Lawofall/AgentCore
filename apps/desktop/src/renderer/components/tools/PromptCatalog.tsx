import { PromptDocument } from "@/components/prompt/PromptDocument";
import { PackOverview } from "@/components/tools/CapabilityPackCard";
import { RoleIdentityBlock } from "@/components/tools/RoleIdentityBlock";
import {
  Badge,
  Button,
  Input,
  SectionLabel,
  Select,
  Textarea,
} from "@/components/ui";
import { useFolders } from "@/hooks/useFolders";
import {
  DEFAULT_PROMPT_CATALOG_ID,
  type PromptCatalogItem,
  buildPromptCatalog,
  flattenPromptCatalog,
  mineCatalogId,
  skillCatalogId,
  withMineSkills,
} from "@/lib/promptCatalog";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import type { Capabilities } from "@/services/capabilities";
import {
  createRuleDocument,
  renameDocument,
  setDocumentDisputed,
  writeDocument,
} from "@/services/documents";
import { isFolderOwner } from "@/services/folders";
import {
  EMPTY_SKILL_CATALOG,
  type OverlayLayer,
  type SkillCatalog,
  composeOnDemandSkillContent,
  getSkillCatalog,
  muteSkillSlot,
  replaceSkillSlot,
  restoreSkillSlot,
  skillBodyFromContent,
  skillFileName,
  unmuteSkillSlot,
} from "@/services/skillCatalog";
import {
  type SkillStoreListing,
  listMySkillListings,
  publishSkill,
  publishSkillVersion,
  unpublishSkill,
} from "@/services/skillStore";
import { useEffect, useMemo, useState } from "react";

const GROUP_HINT: Record<
  Exclude<PromptCatalogItem["kind"], "skill" | "pack">,
  string
> = {
  shared: "每个 Agent（主 Agent 与队员）共享的基座。",
  identity:
    "本回合三选一，不是叠加上去的三层。主 Agent 对用户负责；队员对节点交差。队员交付形态在该回合「收到的上下文」的交付物规格里。",
  mine: "账号里的按需技能。写触发语和正文；要点名占官方槽，再点「换用」。",
};

function skillSubtitle(
  muted: boolean,
  mineCount: number,
  mutedLayer: OverlayLayer | null,
): string {
  if (muted && mutedLayer === "inherited") {
    return "对话里暂时看不到这一行（来自外层）。本层放回目录清不掉外层。";
  }
  if (muted) return "对话里暂时看不到这一行。";
  if (mineCount === 0) {
    return "出厂只读。要换用法，先在「我的技能」写一份。";
  }
  return "出厂正文只读。";
}

function overlayErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.serverMessage?.trim()) return err.serverMessage;
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        return parsed.detail;
      }
    } catch {
      /* keep falling through */
    }
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return "没保存成功";
}

/** Left TOC + right reader for the 工具箱「AI 提示词」page. */
export function PromptCatalog({ data }: { data: Capabilities }) {
  const folders = useFolders();
  const [folderId, setFolderId] = useState("");
  const [overlay, setOverlay] = useState<SkillCatalog>(EMPTY_SKILL_CATALOG);
  const [listings, setListings] = useState<SkillStoreListing[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scope = folderId || null;

  const groups = useMemo(
    () => withMineSkills(buildPromptCatalog(data), overlay.mine),
    [data, overlay.mine],
  );
  const items = useMemo(() => flattenPromptCatalog(groups), [groups]);
  const fallbackId =
    items.find((item) => item.id === DEFAULT_PROMPT_CATALOG_ID)?.id ??
    items[0]?.id ??
    null;
  const [selectedId, setSelectedId] = useState<string | null>(fallbackId);
  const selected =
    items.find((item) => item.id === selectedId) ??
    items.find((item) => item.id === fallbackId) ??
    items[0];

  useEffect(() => {
    let cancelled = false;
    void getSkillCatalog(scope)
      .then((catalog) => {
        if (!cancelled) setOverlay(catalog);
      })
      .catch(() => {
        if (!cancelled) setOverlay(EMPTY_SKILL_CATALOG);
      });
    void listMySkillListings()
      .then((mine) => {
        if (!cancelled) setListings(mine);
      })
      .catch(() => {
        if (!cancelled) setListings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [scope]);

  async function run(action: () => Promise<SkillCatalog | undefined>) {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      if (next) setOverlay(next);
      else setOverlay(await getSkillCatalog(scope));
    } catch (err) {
      setError(overlayErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateMine() {
    await run(async () => {
      const created = await createRuleDocument(
        skillFileName("未命名技能"),
        null,
        composeOnDemandSkillContent("", ""),
        "on_demand",
      );
      const catalog = await getSkillCatalog(scope);
      setSelectedId(mineCatalogId(created.id));
      return catalog;
    });
  }

  if (selected == null) return null;

  return (
    <div
      className="flex min-h-0 flex-1 overflow-hidden rounded-xl border border-border bg-card"
      data-testid="prompt-catalog"
    >
      <nav
        aria-label="提示词目录"
        className="w-56 shrink-0 overflow-y-auto border-r border-border px-2 py-2"
      >
        {folders.length > 0 ? (
          <div className="mb-3 px-2">
            <SectionLabel className="px-0 py-0">范围</SectionLabel>
            <Select
              aria-label="技能目录范围"
              className="mt-1"
              value={folderId}
              onChange={(event) => setFolderId(event.target.value)}
            >
              <option value="">账号</option>
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.relPath || folder.name}
                  {isFolderOwner(folder) ? "" : "（只读）"}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        {groups.map((group) => (
          <div
            key={group.id}
            className="mb-3 last:mb-0"
            data-testid={group.testId}
          >
            <div className="flex items-center justify-between gap-1 px-2 py-1">
              <SectionLabel className="px-0 py-0">{group.label}</SectionLabel>
              {group.id === "mine" ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void onCreateMine()}
                >
                  新建
                </Button>
              ) : null}
            </div>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const isCurrent = item.id === selected.id;
                const hidden =
                  item.kind === "skill" &&
                  overlay.slots.find((row) => row.name === item.skill.name)
                    ?.muted;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      aria-current={isCurrent ? "true" : undefined}
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        "flex w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                        item.depth === 1 && "pl-5 text-muted-foreground",
                        hidden &&
                          !isCurrent &&
                          "text-muted-foreground opacity-60",
                        isCurrent && "bg-accent text-accent-foreground",
                      )}
                    >
                      <span className="min-w-0 truncate">{item.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-4">
        {error ? (
          <p className="mb-3 text-destructive text-xs" role="alert">
            {error}
          </p>
        ) : null}
        <CatalogDetail
          item={selected}
          overlay={overlay}
          listings={listings}
          busy={busy}
          onSelectSkill={(name) => setSelectedId(skillCatalogId(name))}
          onReplace={(slot, documentId) =>
            void run(() => replaceSkillSlot(slot, documentId, scope))
          }
          onRestore={(slot) => void run(() => restoreSkillSlot(slot, scope))}
          onMute={(slot) => void run(() => muteSkillSlot(slot, scope))}
          onUnmute={(slot) => void run(() => unmuteSkillSlot(slot, scope))}
          onSaveMine={(item, draft) =>
            void run(async () => {
              const fileName = skillFileName(draft.name);
              if (fileName !== skillFileName(item.label)) {
                await renameDocument(item.mineId, fileName);
              }
              const written = await writeDocument(
                item.mineId,
                composeOnDemandSkillContent(draft.description, draft.body),
                item.version,
              );
              if (written.conflict) {
                throw new Error("刚有更新，刷新后再保存");
              }
              if (!written.ok) {
                throw new Error("没保存成功");
              }
              return undefined;
            })
          }
          onDisableMine={(item) =>
            void run(async () => {
              await setDocumentDisputed(item.mineId, true);
              setSelectedId(fallbackId);
              return undefined;
            })
          }
          onPublishMine={(item) =>
            void run(async () => {
              const existing = listings.find(
                (row) => row.documentId === item.mineId,
              );
              if (existing?.status === "taken_down") return undefined;
              if (existing?.status === "published") {
                await publishSkillVersion(existing.id, item.mineId);
              } else {
                await publishSkill(item.mineId);
              }
              setListings(await listMySkillListings());
              return undefined;
            })
          }
          onUnpublishMine={(item) =>
            void run(async () => {
              const existing = listings.find(
                (row) => row.documentId === item.mineId,
              );
              if (!existing) return undefined;
              await unpublishSkill(existing.id);
              setListings(await listMySkillListings());
              return undefined;
            })
          }
        />
      </div>
    </div>
  );
}

function CatalogDetail({
  item,
  overlay,
  listings,
  busy,
  onSelectSkill,
  onReplace,
  onRestore,
  onMute,
  onUnmute,
  onSaveMine,
  onDisableMine,
  onPublishMine,
  onUnpublishMine,
}: {
  item: PromptCatalogItem;
  overlay: SkillCatalog;
  listings: SkillStoreListing[];
  busy: boolean;
  onSelectSkill: (name: string) => void;
  onReplace: (slot: string, documentId: string) => void;
  onRestore: (slot: string) => void;
  onMute: (slot: string) => void;
  onUnmute: (slot: string) => void;
  onSaveMine: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => void;
  onDisableMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onPublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onUnpublishMine: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  const slot =
    item.kind === "skill"
      ? overlay.slots.find((row) => row.name === item.skill.name)
      : undefined;

  return (
    <div className="space-y-3">
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <h2 className="font-medium text-foreground text-sm">{item.label}</h2>
          <Badge tone="muted">
            {item.kind === "pack"
              ? "能力包"
              : item.kind === "mine"
                ? "我的"
                : item.group === "standing"
                  ? "常驻"
                  : "按需"}
          </Badge>
          {item.kind === "mine" &&
          listings.some(
            (row) =>
              row.documentId === item.mineId && row.status === "published",
          ) ? (
            <Badge tone="muted">已上架</Badge>
          ) : null}
          {item.kind === "mine" &&
          listings.some(
            (row) =>
              row.documentId === item.mineId && row.status === "taken_down",
          ) ? (
            <Badge tone="muted">平台已下架</Badge>
          ) : null}
          {item.kind === "identity" ? <Badge tone="muted">三选一</Badge> : null}
          {slot?.replacedBy ? (
            <Badge tone="muted">
              {slot.replacedLayer === "inherited"
                ? `已换用 ${slot.replacedBy.name}（外层）`
                : `已换用 ${slot.replacedBy.name}`}
            </Badge>
          ) : null}
          {slot?.muted ? (
            <Badge tone="muted">
              {slot.mutedLayer === "inherited" ? "已藏起（外层）" : "已藏起"}
            </Badge>
          ) : null}
        </div>
        <p className="text-muted-foreground text-xs">
          {item.kind === "pack"
            ? item.pack.summary
            : item.kind === "skill"
              ? skillSubtitle(
                  slot?.muted ?? false,
                  overlay.mine.length,
                  slot?.mutedLayer ?? null,
                )
              : GROUP_HINT[item.kind]}
        </p>
      </header>

      {item.kind === "identity" ? (
        <RoleIdentityBlock
          ceoIdentity={item.ceoIdentity}
          nestedIdentity={item.nestedIdentity}
          leafIdentity={item.leafIdentity}
        />
      ) : null}
      {item.kind === "shared" ? (
        <PromptDocument
          text={item.text}
          compact={false}
          maxHeightClass="max-h-none"
        />
      ) : null}
      {item.kind === "skill" ? (
        <>
          <SlotReplaceBar
            slotName={item.skill.name}
            replacedBy={slot?.replacedBy ?? null}
            replacedLayer={slot?.replacedLayer ?? null}
            muted={slot?.muted ?? false}
            mutedLayer={slot?.mutedLayer ?? null}
            mine={overlay.mine}
            busy={busy}
            writable={overlay.writable}
            onReplace={onReplace}
            onRestore={onRestore}
            onMute={onMute}
            onUnmute={onUnmute}
          />
          <PromptDocument
            text={item.skill.body}
            compact={false}
            maxHeightClass="max-h-none"
          />
        </>
      ) : null}
      {item.kind === "mine" ? (
        <MineSkillEditor
          key={item.id}
          item={item}
          listing={
            listings.find((row) => row.documentId === item.mineId) ?? null
          }
          writable={overlay.writable}
          busy={busy}
          onSave={onSaveMine}
          onDisable={onDisableMine}
          onPublish={onPublishMine}
          onUnpublish={onUnpublishMine}
        />
      ) : null}
      {item.kind === "pack" ? (
        <PackOverview
          pack={item.pack}
          heading={false}
          onSelectSkill={onSelectSkill}
        />
      ) : null}
    </div>
  );
}

function SlotReplaceBar({
  slotName,
  replacedBy,
  replacedLayer,
  muted,
  mutedLayer,
  mine,
  busy,
  writable,
  onReplace,
  onRestore,
  onMute,
  onUnmute,
}: {
  slotName: string;
  replacedBy: SkillCatalog["slots"][number]["replacedBy"];
  replacedLayer: OverlayLayer | null;
  muted: boolean;
  mutedLayer: OverlayLayer | null;
  mine: SkillCatalog["mine"];
  busy: boolean;
  writable: boolean;
  onReplace: (slot: string, documentId: string) => void;
  onRestore: (slot: string) => void;
  onMute: (slot: string) => void;
  onUnmute: (slot: string) => void;
}) {
  const [picked, setPicked] = useState(replacedBy?.documentId ?? "");
  useEffect(() => {
    setPicked(replacedBy?.documentId ?? "");
  }, [replacedBy?.documentId]);
  const locked = busy || !writable;
  const canRestore = Boolean(replacedBy) && replacedLayer === "here";
  const canUnmute = muted && mutedLayer === "here";

  return (
    <div className="space-y-2" data-testid="slot-replace">
      {!writable ? (
        <p className="text-muted-foreground text-xs">
          这张桌的换用和藏起由桌主设置。
        </p>
      ) : null}
      {mine.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <Select
            aria-label="换用我的技能"
            className="w-auto min-w-40"
            value={picked}
            disabled={locked}
            onChange={(event) => setPicked(event.target.value)}
          >
            <option value="">出厂正文</option>
            {mine.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </Select>
          <Button
            type="button"
            disabled={
              locked || !picked || picked === (replacedBy?.documentId ?? "")
            }
            onClick={() => onReplace(slotName, picked)}
          >
            换用
          </Button>
          {canRestore ? (
            <Button
              type="button"
              variant="ghost"
              disabled={locked}
              onClick={() => onRestore(slotName)}
            >
              恢复出厂
            </Button>
          ) : null}
        </div>
      ) : null}
      <div>
        {canUnmute ? (
          <Button
            type="button"
            variant="ghost"
            disabled={locked}
            onClick={() => onUnmute(slotName)}
          >
            放回目录
          </Button>
        ) : muted && mutedLayer === "inherited" ? null : (
          <Button
            type="button"
            variant="ghost"
            disabled={locked}
            onClick={() => onMute(slotName)}
          >
            从对话目录藏起
          </Button>
        )}
      </div>
    </div>
  );
}

function MineSkillEditor({
  item,
  listing,
  writable,
  busy,
  onSave,
  onDisable,
  onPublish,
  onUnpublish,
}: {
  item: Extract<PromptCatalogItem, { kind: "mine" }>;
  listing: SkillStoreListing | null;
  writable: boolean;
  busy: boolean;
  onSave: (
    item: Extract<PromptCatalogItem, { kind: "mine" }>,
    draft: { name: string; description: string; body: string },
  ) => void;
  onDisable: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onPublish: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
  onUnpublish: (item: Extract<PromptCatalogItem, { kind: "mine" }>) => void;
}) {
  const [name, setName] = useState(item.label);
  const [description, setDescription] = useState(item.description);
  const [body, setBody] = useState(skillBodyFromContent(item.content));

  return (
    <form
      className="space-y-3"
      data-testid="mine-skill-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(item, { name, description, body });
      }}
    >
      <div className="block space-y-1">
        <span className="text-muted-foreground text-xs">名称</span>
        <Input
          aria-label="名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={busy}
        />
      </div>
      <div className="block space-y-1">
        <span className="text-muted-foreground text-xs">
          触发语（目录里那一行）
        </span>
        <Input
          aria-label="触发语（目录里那一行）"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          disabled={busy}
        />
      </div>
      <div className="block space-y-1">
        <span className="text-muted-foreground text-xs">正文</span>
        <Textarea
          aria-label="正文"
          rows={14}
          className="min-h-48 font-mono"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          disabled={busy}
        />
      </div>
      {item.occupies.length > 0 ? (
        <p className="text-muted-foreground text-xs">
          当前占槽 {item.occupies.join("、")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          保存
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={busy}
          onClick={() => onDisable(item)}
        >
          停用
        </Button>
        {writable && listing?.status !== "taken_down" ? (
          <>
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => onPublish(item)}
            >
              上架
            </Button>
            {listing?.status === "published" ? (
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => onUnpublish(item)}
              >
                下架
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
    </form>
  );
}
