import {
  type AutonomyRecipe,
  type PermissionAxes,
  RECIPE_AXES,
  RECIPE_LABELS,
  RECIPE_ORDER,
  axesEqual,
  axesShortLabel,
  isIllegalAxes,
  matchRecipe,
  needsAutoCommandConfirm,
  normalizeAxes,
  permissionAxesShortLabel,
  recipeToAxes,
} from "@/services/permissionAxes";
import { describe, expect, it } from "vitest";

describe("permissionAxes mapping", () => {
  it("maps recipes ↔ axes", () => {
    expect(recipeToAxes("cautious")).toEqual(RECIPE_AXES.cautious);
    expect(recipeToAxes("less_interrupt")).toEqual(RECIPE_AXES.less_interrupt);
    expect(recipeToAxes("managed")).toEqual(RECIPE_AXES.managed);
    expect(RECIPE_AXES.cautious.host).toBe("off");
    expect(RECIPE_AXES.less_interrupt).toEqual({
      file_write: "session",
      command: "auto",
      host: "session",
    });
    expect(RECIPE_AXES.managed).toEqual(RECIPE_AXES.less_interrupt);
    expect(RECIPE_ORDER).toEqual(["cautious", "less_interrupt", "managed"]);
    expect("write_code" in RECIPE_AXES).toBe(false);
  });

  it("matches recipes and reports custom", () => {
    expect(matchRecipe(RECIPE_AXES.less_interrupt)).toBe("less_interrupt");
    expect(matchRecipe(RECIPE_AXES.managed)).toBe("less_interrupt");
    expect(
      matchRecipe({
        file_write: "session",
        command: "ask",
        host: "ask",
      }),
    ).toBe("custom");
  });

  it("rejects illegal auto+ask", () => {
    expect(
      isIllegalAxes({
        file_write: "ask",
        command: "auto",
        host: "ask",
      }),
    ).toBe(true);
    expect(isIllegalAxes(RECIPE_AXES.managed)).toBe(false);
    expect(isIllegalAxes(RECIPE_AXES.less_interrupt)).toBe(false);
    expect(
      normalizeAxes({
        file_write: "ask",
        command: "auto",
        host: "ask",
      }),
    ).toEqual(RECIPE_AXES.less_interrupt);
  });

  it("does not fold command=kickoff into auto/ask", () => {
    const unknown = { file_write: "ask" as const, command: "bogus" };
    const kickoff = { file_write: "ask" as const, command: "kickoff" };
    expect(normalizeAxes(kickoff)).toEqual(normalizeAxes(unknown));
    expect(normalizeAxes(kickoff)).not.toEqual({
      file_write: "ask",
      command: "ask",
      host: "session",
    });
    expect(
      normalizeAxes({ file_write: "session", command: "kickoff" }),
    ).toEqual(normalizeAxes({ file_write: "session", command: "bogus" }));
  });

  it("flags auto-command confirm only on enter", () => {
    expect(
      needsAutoCommandConfirm(RECIPE_AXES.cautious, RECIPE_AXES.less_interrupt),
    ).toBe(true);
    expect(
      needsAutoCommandConfirm(RECIPE_AXES.cautious, RECIPE_AXES.managed),
    ).toBe(true);
    expect(
      needsAutoCommandConfirm(RECIPE_AXES.less_interrupt, RECIPE_AXES.managed),
    ).toBe(false);
    expect(
      needsAutoCommandConfirm(RECIPE_AXES.managed, RECIPE_AXES.managed),
    ).toBe(false);
    expect(
      needsAutoCommandConfirm(RECIPE_AXES.managed, RECIPE_AXES.less_interrupt),
    ).toBe(false);
  });

  it("resolves short labels for chip / 系统行", () => {
    expect(axesShortLabel(RECIPE_AXES.cautious)).toBe("谨慎");
    expect(axesShortLabel(RECIPE_AXES.less_interrupt)).toBe("全放行");
    expect(axesShortLabel(RECIPE_AXES.managed)).toBe("全放行");
    expect(
      axesShortLabel({
        file_write: "session",
        command: "ask",
        host: "ask",
      }),
    ).toBe("信任 · 每次 · 本机问");
    expect(
      permissionAxesShortLabel({
        file_write: "session",
        command: "ask",
        host: "ask",
      }),
    ).toBe("信任 · 每次 · 本机问");
    expect(permissionAxesShortLabel(RECIPE_AXES.less_interrupt)).toBe("全放行");
    expect(permissionAxesShortLabel("less_interrupt")).toBe("全放行");
    expect(permissionAxesShortLabel("workspace")).toBe("全放行");
    expect(permissionAxesShortLabel("first_grant")).toBe("全放行");
    expect(
      permissionAxesShortLabel(
        '{"file_write":"session","command":"auto","team_kickoff":"skip","host":"session"}',
      ),
    ).toBe("全放行");
    expect(
      permissionAxesShortLabel(
        '{"file_write":"session","command":"auto","team_kickoff":"rules","host":"session"}',
      ),
    ).toBe("全放行");
    expect(
      permissionAxesShortLabel(
        '{"file_write":"session","command":"auto","team_kickoff":"rules","host":"ask"}',
      ),
    ).toBe("信任 · 免审 · 本机问");
    expect(
      permissionAxesShortLabel(
        '{"file_write":"session","command":"auto","team_kickoff":"skip","host":"ask"}',
      ),
    ).toBe("信任 · 免审 · 本机问");
    expect(permissionAxesShortLabel("{not-json")).toBeNull();
    expect(permissionAxesShortLabel("bogus")).toBeNull();
    expect(permissionAxesShortLabel(null)).toBeNull();
    expect(permissionAxesShortLabel(42)).toBeNull();
  });

  it("recipe order covers all labels", () => {
    for (const id of RECIPE_ORDER) {
      expect(RECIPE_LABELS[id as AutonomyRecipe].short.length).toBeGreaterThan(
        0,
      );
      expect(axesEqual(recipeToAxes(id), RECIPE_AXES[id])).toBe(true);
    }
  });

  it("normalize fills defaults including missing host → session", () => {
    const a: PermissionAxes = normalizeAxes({});
    expect(a).toEqual(RECIPE_AXES.less_interrupt);
    expect(
      normalizeAxes({
        file_write: "session",
        command: "auto",
      }),
    ).toEqual(RECIPE_AXES.less_interrupt);
    expect(
      normalizeAxes({
        file_write: "ask",
        command: "ask",
        host: "off",
      }),
    ).toEqual(RECIPE_AXES.cautious);
  });
});
