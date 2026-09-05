import type { ToolApproval, ToolCategory } from "@/services/capabilities";
import {
  BookOpen,
  FolderOpen,
  Globe,
  type LucideIcon,
  MessageCircleQuestion,
  Network,
  Search,
  Terminal,
} from "lucide-react";

/** Per-category display label + icon for the tool catalog. `skill` is kept for type
 * completeness (the backend enum still defines it) but is intentionally absent from
 * CATEGORY_ORDER below: no tool carries it anymore (`consult` / legacy consult_skill
 * are 编排/orchestration), and 技能 indications now live in the「AI 提示词」page, not
 * as a tool group. */
export const CATEGORY_META: Record<
  ToolCategory,
  { label: string; icon: LucideIcon }
> = {
  filesystem: { label: "文件系统", icon: FolderOpen },
  search: { label: "搜索", icon: Search },
  research: { label: "研究", icon: Globe },
  execution: { label: "执行", icon: Terminal },
  orchestration: { label: "编排", icon: Network },
  interaction: { label: "交互", icon: MessageCircleQuestion },
  skill: { label: "技能", icon: BookOpen },
};

/** Reading order for the grouped tool list (skill excluded — see CATEGORY_META). */
export const CATEGORY_ORDER: ToolCategory[] = [
  "research",
  "search",
  "filesystem",
  "execution",
  "orchestration",
  "interaction",
];

export const APPROVAL_LABEL: Record<ToolApproval, string> = {
  never: "自动执行",
  grantable: "需审批",
};

/** Which side of the team holds a tool — the CEO coordinator, the 队员 (workers),
 * or both. Neutral styling: this is metadata, not a status. */
export function availabilityLabel(availableTo: string[]): string {
  const ceo = availableTo.includes("ceo");
  const worker = availableTo.includes("worker");
  if (ceo && worker) return "全员";
  if (ceo) return "CEO";
  return "队员";
}
