// @vitest-environment jsdom
/**
 * 设置二级导航：宽屏三组十一项（能力在工具箱，不进侧栏）。订阅在账户组。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { MorePage } from "../MorePage";

function renderNav() {
  return render(
    <MemoryRouter initialEntries={["/more/general"]}>
      <MorePage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
});

describe("MorePage 导航分组", () => {
  it("groups wide settings under three headings and leaves 能力 to the toolbox", () => {
    const { container } = renderNav();
    const groups = Array.from(container.querySelectorAll("nav h2")).map(
      (h) => h.textContent,
    );
    expect(groups).toEqual(["账户", "模型", "偏好"]);
    expect(container.querySelectorAll("nav a")).toHaveLength(11);
    expect(screen.queryByRole("link", { name: "能力" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "模型" }).getAttribute("href"),
    ).toBe("/more/models");
    expect(
      screen.getByRole("link", { name: "服务商" }).getAttribute("href"),
    ).toBe("/more/providers");
    expect(
      screen.getByRole("link", { name: "联网搜索" }).getAttribute("href"),
    ).toBe("/more/search");
  });

  it("keeps 账户 and 偏好 multi-item; 模型 is 名单、服务商 and 联网搜索", () => {
    const { container } = renderNav();
    const counts = Array.from(
      container.querySelectorAll("nav > div > div"),
    ).map((group) => group.querySelectorAll("a").length);
    expect(counts).toEqual([4, 3, 4]);
  });

  it("points 账户 at 订阅 and 偏好 at 通用 / 消息隐私 / 快捷键 / 关于", () => {
    renderNav();
    expect(
      screen.getByRole("link", { name: "订阅" }).getAttribute("href"),
    ).toBe("/more/subscription");
    expect(
      screen.getByRole("link", { name: "通用" }).getAttribute("href"),
    ).toBe("/more/general");
    expect(
      screen.getByRole("link", { name: "消息隐私" }).getAttribute("href"),
    ).toBe("/more/messages");
    expect(
      screen.getByRole("link", { name: "快捷键" }).getAttribute("href"),
    ).toBe("/more/shortcuts");
    expect(
      screen.getByRole("link", { name: "关于" }).getAttribute("href"),
    ).toBe("/more/about");
    expect(screen.queryByRole("link", { name: "赞助" })).toBeNull();
    expect(screen.queryByRole("link", { name: "外观" })).toBeNull();
    expect(screen.queryByRole("link", { name: "反馈" })).toBeNull();
  });
});
