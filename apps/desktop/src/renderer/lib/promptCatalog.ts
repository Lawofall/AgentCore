import {
  extractCeoIdentity,
  splitWorkerGuideline,
  workerContractFromGuidelines,
} from "@/lib/splitGuidelineRoles";
import type {
  Capabilities,
  CapabilityPack,
  CapabilitySkill,
} from "@/services/capabilities";

export type PromptCatalogGroupId = "standing" | "on_demand" | "packs";

export type PromptCatalogItem =
  | {
      id: "shared";
      kind: "shared";
      group: "standing";
      label: string;
      depth: 0;
      text: string;
    }
  | {
      id: "identity";
      kind: "identity";
      group: "standing";
      label: string;
      depth: 0;
      ceoIdentity: string;
      nestedIdentity: string;
      leafIdentity: string;
    }
  | {
      id: "contract";
      kind: "contract";
      group: "standing";
      label: string;
      depth: 0;
      text: string;
    }
  | {
      id: string;
      kind: "skill";
      group: "on_demand" | "packs";
      label: string;
      depth: 0 | 1;
      skill: CapabilitySkill;
    }
  | {
      id: string;
      kind: "pack";
      group: "packs";
      label: string;
      depth: 0;
      pack: CapabilityPack;
    };

export interface PromptCatalogGroup {
  id: PromptCatalogGroupId;
  label: string;
  testId?: string;
  items: PromptCatalogItem[];
}

export const DEFAULT_PROMPT_CATALOG_ID = "identity";

export function skillCatalogId(name: string): string {
  return `skill:${name}`;
}

export function packCatalogId(id: string): string {
  return `pack:${id}`;
}

/** Split the capability payload into the TOC groups the 工具箱「AI 提示词」reader shows. */
export function buildPromptCatalog(data: Capabilities): PromptCatalogGroup[] {
  const packs = data.packs ?? [];
  const packSkillNames = new Set(
    packs.flatMap((pack) => pack.skills.map((skill) => skill.name)),
  );
  const thinSkills = data.skills.filter(
    (skill) => !packSkillNames.has(skill.name),
  );
  const contract = workerContractFromGuidelines(
    data.guidelines.worker_leaf,
    data.guidelines.worker_captain,
  );

  const standing: PromptCatalogItem[] = [
    {
      id: "shared",
      kind: "shared",
      group: "standing",
      label: "全员共享准则",
      depth: 0,
      text: data.guidelines.shared_base,
    },
    {
      id: "identity",
      kind: "identity",
      group: "standing",
      label: "角色身份",
      depth: 0,
      ceoIdentity: extractCeoIdentity(data.guidelines.ceo_addon),
      nestedIdentity: splitWorkerGuideline(data.guidelines.worker_captain)
        .identity,
      leafIdentity: splitWorkerGuideline(data.guidelines.worker_leaf).identity,
    },
  ];
  if (contract) {
    standing.push({
      id: "contract",
      kind: "contract",
      group: "standing",
      label: "队员交付合同",
      depth: 0,
      text: contract,
    });
  }

  const groups: PromptCatalogGroup[] = [
    { id: "standing", label: "常驻模板", items: standing },
  ];

  if (thinSkills.length > 0) {
    groups.push({
      id: "on_demand",
      label: "按需注入",
      items: thinSkills.map((skill) => ({
        id: skillCatalogId(skill.name),
        kind: "skill" as const,
        group: "on_demand" as const,
        label: skill.summary,
        depth: 0 as const,
        skill,
      })),
    });
  }

  if (packs.length > 0) {
    groups.push({
      id: "packs",
      label: "能力包",
      testId: "capability-packs",
      items: packs.flatMap((pack) => [
        {
          id: packCatalogId(pack.id),
          kind: "pack" as const,
          group: "packs" as const,
          label: pack.name,
          depth: 0 as const,
          pack,
        },
        ...pack.skills.map((skill) => ({
          id: skillCatalogId(skill.name),
          kind: "skill" as const,
          group: "packs" as const,
          label: skill.summary,
          depth: 1 as const,
          skill,
        })),
      ]),
    });
  }

  return groups;
}

export function flattenPromptCatalog(
  groups: PromptCatalogGroup[],
): PromptCatalogItem[] {
  return groups.flatMap((group) => group.items);
}
