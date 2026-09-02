import { describe, expect, it } from "vitest";
import {
  MERMAID_FLOWCHART_LAYOUT,
  MERMAID_FONT_SIZE_PX,
  mermaidRenderConfig,
} from "../mermaidConfig";

describe("mermaidRenderConfig", () => {
  it("uses a compact flowchart layout and body-adjacent type", () => {
    const cfg = mermaidRenderConfig(false);
    expect(cfg.theme).toBe("base");
    expect(cfg.securityLevel).toBe("strict");
    expect(cfg.fontSize).toBe(MERMAID_FONT_SIZE_PX);
    expect(cfg.flowchart).toMatchObject({
      nodeSpacing: MERMAID_FLOWCHART_LAYOUT.nodeSpacing,
      rankSpacing: MERMAID_FLOWCHART_LAYOUT.rankSpacing,
      useMaxWidth: true,
    });
    expect(MERMAID_FLOWCHART_LAYOUT.nodeSpacing).toBeLessThan(50);
    expect(MERMAID_FLOWCHART_LAYOUT.rankSpacing).toBeLessThan(50);
  });

  it("maps diagrams to design tokens instead of mermaid stock palettes", () => {
    const light = mermaidRenderConfig(false);
    const dark = mermaidRenderConfig(true);
    expect(light.theme).toBe("base");
    expect(dark.theme).toBe("base");
    expect(light.themeVariables.darkMode).toBe(false);
    expect(dark.themeVariables.darkMode).toBe(true);
    expect(light.themeVariables.useGradient).toBe(false);
    expect(dark.themeVariables.primaryColor).toBe("oklch(0.185 0.004 255)");
    expect(dark.themeVariables.background).toBe("oklch(0.13 0.004 255)");
    expect(light.themeVariables.primaryColor).not.toBe(
      dark.themeVariables.primaryColor,
    );
  });
});
