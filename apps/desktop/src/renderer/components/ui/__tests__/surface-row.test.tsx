// @vitest-environment jsdom
import { SurfaceRow, SurfaceRowButton } from "@/components/ui";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("SurfaceRow sidebar ink", () => {
  it("resting rows use full sidebar foreground, not faded alpha", () => {
    render(
      <SurfaceRow variant="sidebar" data-testid="row">
        对话
      </SurfaceRow>,
    );
    const cls = screen.getByTestId("row").className;
    expect(cls).toContain("text-sidebar-foreground");
    expect(cls).not.toContain("text-sidebar-foreground/70");
    expect(cls).not.toContain("hover:text-sidebar-foreground");
  });

  it("active rows keep ink and lift the accent wash", () => {
    render(
      <SurfaceRowButton active aria-label="新对话">
        新对话
      </SurfaceRowButton>,
    );
    const cls = screen.getByRole("button", { name: "新对话" }).className;
    expect(cls).toContain("bg-sidebar-accent");
    expect(cls).toContain("text-sidebar-accent-foreground");
    expect(cls).not.toContain("text-sidebar-foreground/70");
  });
});
