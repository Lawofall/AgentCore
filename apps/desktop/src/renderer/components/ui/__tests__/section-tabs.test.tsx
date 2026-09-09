// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { SectionTabs } from "../section-tabs";

afterEach(cleanup);

describe("SectionTabs", () => {
  it("marks the active section with an accent capsule, not inverse", () => {
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
    const idle = within(nav).getByRole("link", { name: "收件箱" });
    expect(active.className).toContain("bg-accent");
    expect(active.className).toContain("text-accent-foreground");
    expect(active.className).toContain("rounded-full");
    expect(active.className).not.toContain("bg-foreground");
    expect(idle.className).not.toContain("bg-accent");
    expect(active.querySelector('span[aria-hidden="true"]')).toBeNull();
  });

  it("optional icon is decorative and does not change the accessible name", () => {
    render(
      <MemoryRouter initialEntries={["/a"]}>
        <SectionTabs
          aria-label="分区"
          items={[
            {
              to: "/a",
              label: "甲",
              end: true,
              icon: <svg data-testid="tab-icon" />,
            },
          ]}
        />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "分区" });
    expect(within(nav).getByRole("link", { name: "甲" })).toBeTruthy();
    expect(
      screen.getByTestId("tab-icon").closest("[aria-hidden='true']"),
    ).toBeTruthy();
  });

  it("optional action sits in the same row", () => {
    render(
      <MemoryRouter initialEntries={["/a"]}>
        <SectionTabs
          aria-label="分区"
          items={[{ to: "/a", label: "甲", end: true }]}
          action={<a href="/b">乙</a>}
        />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "分区" });
    expect(within(nav).getByRole("link", { name: "乙" })).toBeTruthy();
  });
});
