import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/** Tool governance level (generated from backend `ToolApproval`). */
export type ToolApproval = Schemas["ToolApproval"];
/** Tool grouping (generated from backend `ToolCategory`). */
export type ToolCategory = Schemas["ToolCategory"];
/** A tool's catalog entry; `parameters` is the call JSON Schema, `available_to` a
 * subset of ["ceo","worker"] saying which side of the team holds it. */
export type CapabilityTool = Schemas["CapabilityTool"];
/** A system Skill: catalog `summary` + the full `body` the CEO pulls on demand. */
export type CapabilitySkill = Schemas["CapabilitySkill"];
/** The system-prompt template the agents follow (静态 蓝图). */
export type CapabilityGuidelines = Schemas["CapabilityGuidelines"];
/** Deployment-listed capability pack (catalog display; nested `skills` are pack contents). */
export type CapabilityPack = Schemas["CapabilityPack"];
/** Complete capability picture for 工具箱 → 能力图鉴. */
export type Capabilities = Schemas["CapabilitiesResponse"];

/** Load the platform's complete capability catalog (read-only): every tool with its
 * CEO/worker reach, the system Skills, and the system-prompt templates (shared base,
 * worker identities, CEO). Single fetch backing 工具箱 → 能力图鉴. */
export async function getCapabilities(): Promise<Capabilities> {
  return api.get<Capabilities>("/v1/capabilities");
}
