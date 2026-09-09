import type { LucideIcon } from "lucide-react";
import { Presentation, ScrollText, Workflow, Wrench } from "lucide-react";

export const TOOLBOX_KINDS = [
  "skills",
  "tools",
  "creation",
  "workflows",
] as const;

export type ToolboxKind = (typeof TOOLBOX_KINDS)[number];

export const TOOLBOX_KIND_LABEL: Record<ToolboxKind, string> = {
  skills: "提示词",
  tools: "工具",
  creation: "创作",
  workflows: "工作流",
};

/** 与命令面板同一套符号。 */
export const TOOLBOX_KIND_ICON: Record<ToolboxKind, LucideIcon> = {
  skills: ScrollText,
  tools: Wrench,
  creation: Presentation,
  workflows: Workflow,
};

/** 市场货架真有存货的种类。动作工具 / 创作不进 chip。 */
export const MARKET_KINDS = ["skills", "workflows"] as const;

export type MarketKind = (typeof MARKET_KINDS)[number];

export function isMarketKind(value: string | null): value is MarketKind {
  return value != null && (MARKET_KINDS as readonly string[]).includes(value);
}
