// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const narrowState = {
  isNarrow: false,
  hideChrome: false,
  conversationDrawerOpen: false,
  setConversationDrawerOpen: () => undefined,
};

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => narrowState,
}));

import { PageHeader } from "../page-header";

afterEach(() => {
  narrowState.isNarrow = false;
  cleanup();
});

describe("PageHeader", () => {
  it("renders a single-line title with optional same-row meta and action", () => {
    render(
      <PageHeader
        title="用户服务协议"
        meta="更新日期：2026-07-28"
        action={<button type="button">刷新</button>}
      />,
    );
    const heading = screen.getByRole("heading", {
      level: 1,
      name: "用户服务协议",
    });
    expect(heading.parentElement?.textContent).toContain(
      "更新日期：2026-07-28",
    );
    expect(screen.getByRole("button", { name: "刷新" })).toBeTruthy();
  });

  it("hides the in-page title when the narrow back bar already names the page", () => {
    narrowState.isNarrow = true;
    render(
      <PageHeader
        title="账户设置"
        action={<button type="button">刷新</button>}
      />,
    );
    expect(screen.queryByRole("heading", { name: "账户设置" })).toBeNull();
    expect(screen.getByRole("button", { name: "刷新" })).toBeTruthy();
  });

  it("renders nothing on narrow when there is no meta, action, or back", () => {
    narrowState.isNarrow = true;
    const { container } = render(<PageHeader title="通用" />);
    expect(container.textContent).toBe("");
  });

  it("links back and keeps title plus actions on one row", () => {
    const { container } = render(
      <MemoryRouter>
        <PageHeader
          title="工作流"
          back={{ to: "/toolbox", label: "工具箱" }}
          action={<button type="button">新建工作流</button>}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("link", { name: "工具箱" }).getAttribute("href"),
    ).toBe("/toolbox");
    const header = container.querySelector("header");
    expect(
      header?.contains(
        screen.getByRole("heading", { level: 1, name: "工作流" }),
      ),
    ).toBe(true);
    expect(
      header?.contains(screen.getByRole("button", { name: "新建工作流" })),
    ).toBe(true);
    expect(header?.className).toContain("border-b");
  });

  it("drops its own border when the page brings a section tab baseline", () => {
    const { container } = render(
      <MemoryRouter>
        <PageHeader
          title="自动化"
          back={{ to: "/toolbox", label: "工具箱" }}
          bordered={false}
        />
      </MemoryRouter>,
    );
    expect(container.querySelector("header")?.className).not.toContain(
      "border-b",
    );
  });
});
