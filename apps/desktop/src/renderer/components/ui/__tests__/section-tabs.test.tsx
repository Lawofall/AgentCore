// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { SectionTabs } from "../section-tabs";

afterEach(cleanup);

describe("SectionTabs", () => {
  it("marks the active section with an underline, not a filled pill", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/automations"]}>
        <Routes>
          <Route
            path="/toolbox/automations"
            element={
              <SectionTabs
                aria-label="自动化分区"
                items={[
                  { to: "/toolbox/automations", label: "任务", end: true },
                  { to: "/toolbox/automations/inbox", label: "收件箱" },
                ]}
              />
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "自动化分区" });
    const active = within(nav).getByRole("link", { name: "任务" });
    expect(active.className).not.toContain("bg-accent");
    const underline = active.querySelector('span[aria-hidden="true"]');
    expect(underline?.className).toContain("bg-primary");
    expect(underline?.className).toContain("h-0.5");
  });
});
