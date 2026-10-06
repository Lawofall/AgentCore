import {
  PROMPT_TOOLS_TAG,
  RESIDENT_LABEL,
  exceptionAudienceTags,
} from "@/components/tools/catalogMeta";
import type { PromptCatalogItem } from "@/lib/promptCatalog";
import { formatAlwaysRowChars } from "@/lib/promptSizes";
import { skillStoreGroupLabel } from "@/pages/toolbox/market/skillStoreGroups";
import type { CapabilitySkill } from "@/services/capabilities";
import type { SkillStoreListing } from "@/services/skillStore";

export type PromptShelfChipTone =
  | "muted"
  | "primary"
  | "success"
  | "destructive";

export type PromptShelfChip = {
  label: string;
  tone?: PromptShelfChipTone;
};

export type PromptShelfCopy = {
  title: string;
  subtitle?: string;
  description: string;
  tags: string[];
  accessory: PromptShelfChip[];
};

export const PROMPT_SHELF_AFFORDANCE = {
  createEntry: { title: "新建条目", description: "写一条按需提示词" },
  createFolder: { title: "新建夹", description: "给提示词分组" },
  dropHere: { title: "拖到这里", description: "放到这个夹" },
} as const;

export type PromptMineShelfOpts = {
  fromMarket?: boolean;
  hasUpdate?: boolean;
  listingStatus?: SkillStoreListing["status"] | null;
  sceneGroupLabel?: string;
};

function distinctLine(value: string | undefined, title: string): string {
  const line = (value ?? "").trim();
  if (!line || line === title) return "";
  return line;
}

function chip(label: string, tone?: PromptShelfChipTone): PromptShelfChip {
  return tone ? { label, tone } : { label };
}

function toolsTag(hasTools: boolean): string[] {
  return hasTools ? [PROMPT_TOOLS_TAG] : [];
}

function skillAudience(skill: CapabilitySkill): string[] {
  const audience = skill.audience;
  return audience && audience.length > 0 ? audience : ["ceo", "worker"];
}

export function promptMineShelfOpts(
  item: Extract<PromptCatalogItem, { kind: "mine" }>,
  listings: readonly SkillStoreListing[],
  installed: readonly SkillStoreListing[],
): PromptMineShelfOpts {
  const rowInstalled = installed.find(
    (row) => row.installDocumentId === item.mineId,
  );
  if (rowInstalled) {
    return {
      fromMarket: true,
      hasUpdate: rowInstalled.hasUpdate,
      listingStatus: null,
      sceneGroupLabel: skillStoreGroupLabel(rowInstalled.group),
    };
  }
  const authored = listings.find((row) => row.documentId === item.mineId);
  const showGroup =
    authored != null &&
    (authored.status === "published" || authored.status === "taken_down");
  return {
    fromMarket: false,
    hasUpdate: false,
    listingStatus: authored?.status ?? null,
    sceneGroupLabel: showGroup
      ? skillStoreGroupLabel(authored.group)
      : undefined,
  };
}

export function promptItemShelfCopy(
  item: PromptCatalogItem,
  opts: PromptMineShelfOpts & { alwaysChars?: number } = {},
): PromptShelfCopy {
  const subtitle =
    opts.alwaysChars == null
      ? undefined
      : (formatAlwaysRowChars(opts.alwaysChars) ?? undefined);
  if (item.kind === "shared") {
    return {
      title: item.label,
      subtitle,
      description: "每回合都在的工作宪法",
      tags: [],
      accessory: [chip("官方")],
    };
  }
  if (item.kind === "skill") {
    return {
      title: item.label,
      description: distinctLine(item.skill.blurb, item.label),
      tags: [
        ...toolsTag((item.skill.requires_tools?.length ?? 0) > 0),
        ...exceptionAudienceTags(skillAudience(item.skill)),
      ],
      accessory: [chip("官方")],
    };
  }
  if (item.kind === "tool") {
    const title = distinctLine(item.tool.summary, "") || item.label;
    const tags = [
      ...toolsTag(true),
      ...(item.tool.approval === "grantable" ? ["需审批"] : []),
      ...exceptionAudienceTags(item.tool.available_to),
    ];
    return {
      title,
      description: distinctLine(item.tool.blurb, title),
      tags,
      accessory: [chip("官方")],
    };
  }
  return mineShelfCopy(item, { ...opts, subtitle });
}

function mineShelfCopy(
  item: Extract<PromptCatalogItem, { kind: "mine" }>,
  opts: PromptMineShelfOpts & { subtitle?: string },
): PromptShelfCopy {
  const description = distinctLine(item.description, item.label);
  const tags = [...(opts.sceneGroupLabel ? [opts.sceneGroupLabel] : [])];
  return {
    title: item.label,
    subtitle: opts.subtitle,
    description: description || "",
    tags,
    accessory: mineAccessory(item, opts),
  };
}

function mineAccessory(
  item: Extract<PromptCatalogItem, { kind: "mine" }>,
  opts: PromptMineShelfOpts,
): PromptShelfChip[] {
  const inAlways = item.applyMode === "always";
  if (opts.fromMarket) {
    const chips: PromptShelfChip[] = [];
    if (inAlways) chips.push(chip("必带"));
    chips.push(chip("市场"));
    if (opts.hasUpdate) chips.push(chip("有更新", "primary"));
    return chips;
  }
  const published = opts.listingStatus === "published";
  const takenDown = opts.listingStatus === "taken_down";
  const chips: PromptShelfChip[] = [];
  if (inAlways) chips.push(chip("必带"));
  if (published || takenDown) chips.push(chip("我的"));
  if (published) chips.push(chip("已上架"));
  if (takenDown) chips.push(chip("平台已下架"));
  return chips;
}

export function promptConnectorShelfCopy(row: {
  label: string;
  runtimeError?: string | null;
}): PromptShelfCopy {
  return {
    title: row.label,
    description: row.runtimeError?.trim() || "",
    tags: [],
    accessory: [],
  };
}

export function promptShelfHeaderChips(
  copy: PromptShelfCopy,
): PromptShelfChip[] {
  return [...copy.accessory, ...copy.tags.map((label) => chip(label))];
}

/** Dialog title chips: same as the card, plus 开场即用 for listed tools. */
export function promptReadHeaderChips(
  copy: PromptShelfCopy,
  item: PromptCatalogItem,
): PromptShelfChip[] {
  if (item.kind !== "tool") return promptShelfHeaderChips(copy);
  return [
    ...copy.accessory,
    chip(RESIDENT_LABEL.resident),
    ...copy.tags.map((label) => chip(label)),
  ];
}
