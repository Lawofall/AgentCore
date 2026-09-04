import { PromptDocument } from "@/components/prompt/PromptDocument";
import { PackOverview } from "@/components/tools/CapabilityPackCard";
import { RoleIdentityBlock } from "@/components/tools/RoleIdentityBlock";
import { Badge, SectionLabel } from "@/components/ui";
import {
  DEFAULT_PROMPT_CATALOG_ID,
  type PromptCatalogItem,
  buildPromptCatalog,
  flattenPromptCatalog,
  skillCatalogId,
} from "@/lib/promptCatalog";
import { cn } from "@/lib/utils";
import type { Capabilities } from "@/services/capabilities";
import { useMemo, useState } from "react";

const GROUP_HINT: Record<PromptCatalogItem["kind"], string> = {
  shared: "每个 Agent（主 Agent 与队员）共享的基座。",
  identity:
    "本回合三选一，不是叠加上去的三层。主 Agent 对用户负责；队员对节点交差。",
  contract: "叶子队员与可再委派的队员共用。主 Agent 不走这份合同。",
  skill: "不是独立能力，而是某个内置工具的进阶用法；按需 consult 才注入。",
  pack: "本部署已上架的垂直领域能力；包内技能按需注入对话。",
};

/** Left TOC + right reader for the 工具箱「AI 提示词」page. */
export function PromptCatalog({ data }: { data: Capabilities }) {
  const groups = useMemo(() => buildPromptCatalog(data), [data]);
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
        {groups.map((group) => (
          <div
            key={group.id}
            className="mb-3 last:mb-0"
            data-testid={group.testId}
          >
            <SectionLabel className="px-2 py-1">{group.label}</SectionLabel>
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
                        "flex w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                        item.depth === 1 && "pl-5 text-muted-foreground",
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
        <CatalogDetail
          item={selected}
          onSelectSkill={(name) => setSelectedId(skillCatalogId(name))}
        />
      </div>
    </div>
  );
}

function CatalogDetail({
  item,
  onSelectSkill,
}: {
  item: PromptCatalogItem;
  onSelectSkill: (name: string) => void;
}) {
  return (
    <div className="space-y-3">
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <h2 className="font-medium text-foreground text-sm">{item.label}</h2>
          <Badge tone="muted">
            {item.kind === "pack"
              ? "能力包"
              : item.group === "standing"
                ? "常驻"
                : "按需"}
          </Badge>
          {item.kind === "identity" ? <Badge tone="muted">三选一</Badge> : null}
        </div>
        {item.kind === "skill" ? (
          <p className="font-mono text-muted-foreground text-xs">
            {item.skill.name}
          </p>
        ) : null}
        <p className="text-muted-foreground text-xs">
          {item.kind === "pack" ? item.pack.summary : GROUP_HINT[item.kind]}
        </p>
      </header>

      {item.kind === "identity" ? (
        <RoleIdentityBlock
          ceoIdentity={item.ceoIdentity}
          nestedIdentity={item.nestedIdentity}
          leafIdentity={item.leafIdentity}
        />
      ) : null}
      {item.kind === "shared" || item.kind === "contract" ? (
        <PromptDocument
          text={item.text}
          compact={false}
          maxHeightClass="max-h-none"
        />
      ) : null}
      {item.kind === "skill" ? (
        <PromptDocument
          text={item.skill.body}
          compact={false}
          maxHeightClass="max-h-none"
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
