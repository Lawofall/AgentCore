import {
  capabilityFromProfile,
  testAssemblyProfile,
} from "@/lib/assemblyCapability";
import { describe, expect, it } from "vitest";

describe("capabilityFromProfile", () => {
  it("maps official 标准 to its deny list and unfiltered membership", () => {
    const cap = capabilityFromProfile(testAssemblyProfile("asm-standard"));
    expect(cap).toEqual({
      assemblyId: "asm-standard",
      disabledTools: ["browser", "debate", "folders", "chats"],
      omittedProjections: [],
      omittedFactorySkills: [],
      omitDeskRules: false,
      enabledMcpServerIds: [],
      skillMembership: null,
    });
  });

  it("copies user assembly lists and membership", () => {
    const cap = capabilityFromProfile({
      ...testAssemblyProfile("asm-mine"),
      kind: "user",
      name: "写稿",
      is_default: false,
      recipe: null,
      disabled_tools: ["web"],
      omitted_factory_skills: ["page_ui"],
      omit_desk_rules: false,
      enabled_mcp_server_ids: ["fs"],
      skill_membership: { "doc-1": "always" },
    });
    expect(cap.assemblyId).toBe("asm-mine");
    expect(cap.disabledTools).toEqual(["web"]);
    expect(cap.omittedFactorySkills).toEqual(["page_ui"]);
    expect(cap.enabledMcpServerIds).toEqual(["fs"]);
    expect(cap.skillMembership).toEqual({ "doc-1": "always" });
  });
});
