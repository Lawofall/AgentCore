// @vitest-environment jsdom
import {
  SegmentedNav,
  type SegmentedNavItem,
} from "@/components/ui/segmented-nav";
import { cleanup, render, screen } from "@testing-library/react";
import { Timer, Wrench } from "lucide-react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

const ITEMS: SegmentedNavItem[] = [
  {
    id: "tools",
    label: "工具",
    to: "/toolbox/tools",
    icon: Wrench,
    colorVar: "var(--primary)",
  },
  {
    id: "automations",
    label: "自动化",
    to: "/toolbox/automations",
    icon: Timer,
  },
];

function renderNav(path: string, items: readonly SegmentedNavItem[] = ITEMS) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SegmentedNav aria-label="工具箱能力" items={items} />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("SegmentedNav", () => {
  it("renders one link per item inside a named landmark", () => {
    renderNav("/toolbox/tools");
    const nav = screen.getByRole("navigation", { name: "工具箱能力" });
    expect(nav.querySelectorAll("a")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /工具/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /自动化/ })).toBeTruthy();
  });

  it("marks only the matching segment active", () => {
    renderNav("/toolbox/tools");
    const active = screen.getByRole("link", { name: /^工具$/ });
    expect(active.getAttribute("aria-current")).toBe("page");
    expect(active.classList.contains("bg-accent")).toBe(true);

    const idle = screen.getByRole("link", { name: /自动化/ });
    expect(idle.getAttribute("aria-current")).toBeNull();
    expect(idle.classList.contains("bg-accent")).toBe(false);
  });

  it("keeps a segment lit on its sub-routes by default", () => {
    renderNav("/toolbox/automations/inbox");
    expect(
      screen.getByRole("link", { name: /自动化/ }).getAttribute("aria-current"),
    ).toBe("page");
  });

  it("limits the highlight to an exact match when end is set", () => {
    renderNav("/toolbox/automations/inbox", [
      { ...ITEMS[1], end: true },
    ] as SegmentedNavItem[]);
    expect(
      screen.getByRole("link", { name: /自动化/ }).getAttribute("aria-current"),
    ).toBeNull();
  });

  it("renders a labelled count badge and hides it at zero", () => {
    renderNav("/toolbox/tools", [
      { ...ITEMS[1], badge: 3, badgeLabel: "3 条待处理" },
      { ...ITEMS[0], badge: 0 },
    ] as SegmentedNavItem[]);
    expect(screen.getByLabelText("3 条待处理").textContent).toBe("3");
    expect(screen.getByRole("link", { name: /^工具$/ }).textContent).toBe(
      "工具",
    );
  });

  it("caps the badge at 99+", () => {
    renderNav("/toolbox/tools", [
      { ...ITEMS[1], badge: 120, badgeLabel: "120 条待处理" },
    ] as SegmentedNavItem[]);
    expect(screen.getByLabelText("120 条待处理").textContent).toBe("99+");
  });

  it("absorbs a narrow row by scrolling itself", () => {
    renderNav("/toolbox/tools");
    const nav = screen.getByRole("navigation", { name: "工具箱能力" });
    // 挤不下时收缩自己横向滚，而不是把整行撑出页面级横向滚动条。
    expect(nav.className).toContain("min-w-0");
    expect(nav.className).toContain("overflow-x-auto");
    // overlay 条靠 globals.css 的普通类藏掉——Tailwind 等价物会输给全局规则。
    expect(nav.classList.contains("scrollbar-hidden")).toBe(true);
  });

  it("tints the icon with the item color token", () => {
    const { container } = renderNav("/toolbox/tools");
    const tinted = container.querySelector<HTMLElement>("a [style]");
    expect(tinted?.style.color).toBe("var(--primary)");
    // Items without a colorVar stay on the link's own text color.
    expect(
      screen.getByRole("link", { name: /自动化/ }).querySelector("[style]"),
    ).toBeNull();
  });
});
