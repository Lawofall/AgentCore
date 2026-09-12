import type { SkillStoreGroup } from "@/services/skillStore";
import { isSkillStoreGroup } from "@/services/skillStore";

export const SKILL_STORE_GROUPS = [
  { id: "legal", label: "法律合规" },
  { id: "writing", label: "写作成稿" },
  { id: "research", label: "研究调研" },
  { id: "product", label: "产品商业" },
  { id: "engineering", label: "开发工程" },
  { id: "decision", label: "决策对抗" },
] as const satisfies readonly { id: SkillStoreGroup; label: string }[];

export function skillStoreGroupLabel(id: SkillStoreGroup): string {
  return SKILL_STORE_GROUPS.find((row) => row.id === id)?.label ?? id;
}

export { isSkillStoreGroup };
export type { SkillStoreGroup };
