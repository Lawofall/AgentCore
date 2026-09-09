import { isKnownAppRoute } from "@/pages/toolbox/manual/gates/appRoutes";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { describe, expect, it } from "vitest";

describe("workflow routes isolated from collaboration graph", () => {
  it("registers toolbox workflow paths", () => {
    expect(isKnownAppRoute(APP_PATHS.toolbox.workflows.root)).toBe(true);
    expect(isKnownAppRoute("/toolbox/workflows/wf-demo")).toBe(true);
  });

  it("does not collide with conversation / turn graph routes", () => {
    expect(APP_PATHS.toolbox.workflows.root).not.toContain("conversations");
    expect(APP_PATHS.toolbox.workflows.edit("x")).toBe("/toolbox/workflows/x");
    expect(APP_PATHS.toolbox.workflows.root).not.toBe(
      APP_PATHS.toolbox.mine.automations,
    );
  });
});
