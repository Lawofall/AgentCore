import { describe, expect, it } from "vitest";
import {
  debateGraphIdentity,
  isGenericDebateSideName,
} from "../debateIdentity";

describe("debateGraphIdentity", () => {
  it("hides default 正方/反方 titles and uses stance colors", () => {
    expect(isGenericDebateSideName("正方")).toBe(true);
    expect(isGenericDebateSideName("反方")).toBe(true);
    const pro = debateGraphIdentity({ role: "正方", stance: "pro" });
    expect(pro).toEqual({
      color: "var(--debate-side-pro)",
      glyph: "正",
      showRoleTitle: false,
    });
    const con = debateGraphIdentity({ role: "反方", stance: "con" });
    expect(con).toEqual({
      color: "var(--debate-side-con)",
      glyph: "反",
      showRoleTitle: false,
    });
  });

  it("infers stance from the default name when wire stance is missing", () => {
    expect(debateGraphIdentity({ role: "正方" }).color).toBe(
      "var(--debate-side-pro)",
    );
    expect(debateGraphIdentity({ role: "反方" }).showRoleTitle).toBe(false);
  });

  it("keeps custom side names as titles with stance color", () => {
    const id = debateGraphIdentity({ role: "原告", stance: "pro" });
    expect(id.showRoleTitle).toBe(true);
    expect(id.glyph).toBe("原");
    expect(id.color).toBe("var(--debate-side-pro)");
  });

  it("leaves non-debate roles on the agent hash palette", () => {
    const id = debateGraphIdentity({ role: "主持人" });
    expect(id.showRoleTitle).toBe(true);
    expect(id.glyph).toBe("主");
    expect(id.color).toMatch(/^var\(--agent-\d+\)$/);
  });
});
