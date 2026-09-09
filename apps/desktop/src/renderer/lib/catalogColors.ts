import type { ToolFace } from "@/services/capabilities";
import type { CSSProperties } from "react";

/** Stable toolbox / creation-tool identity keys → `--artifact-*` tokens. */
export type ArtifactKind =
  | "doc"
  | "mindmap"
  | "table"
  | "canvas"
  | "slides"
  | "connectors"
  | "workflow"
  | "tools"
  | "guidelines"
  | "manual";

/** CSS `var(...)` for a creation-tool or featured toolbox entry. The 能力 entries
 * (工具 / AI 提示词) borrow the catalog family so the launcher tile and the catalog
 * page read as the same color identity. */
export function artifactColorVar(kind: ArtifactKind): string {
  if (kind === "tools") return "var(--primary)";
  if (kind === "guidelines") return "var(--catalog-orchestration)";
  return `var(--artifact-${kind})`;
}

/** CSS `var(...)` for an AI capability catalog tool face. */
export function catalogCategoryColorVar(face: ToolFace): string {
  return `var(--catalog-${face})`;
}

/** Tinted icon shell (inline only — tokens are not mapped to Tailwind classes). */
export function typeIconShellStyle(
  colorVar: string,
  options?: { muted?: boolean },
): CSSProperties {
  const mix = options?.muted ? "10%" : "14%";
  return {
    backgroundColor: `color-mix(in oklab, ${colorVar} ${mix}, transparent)`,
    color: colorVar,
  };
}
