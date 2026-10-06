// @vitest-environment jsdom
/**
 * 设置二级导航：宽屏三组九项（装配在工具箱，不进侧栏）。赞助在偏好末项。
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
  it("groups wide settings under three headings and leaves 装配 to the toolbox", () => {
    const { container } = renderNav();
    const groups = Array.from(container.querySelectorAll("nav h2")).map(
      (h) => h.textContent,
    );
    expect(groups).toEqual(["账户", "模型", "偏好"]);
    expect(container.querySelectorAll("nav a")).toHaveLength(9);
    expect(screen.queryByRole("link", { name: "装配" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "服务商" }).getAttribute("href"),
    ).toBe("/more/providers");
  });

  it("keeps 账户 and 偏好 multi-item; 模型 is only 服务商 on a wide screen", () => {
    const { container } = renderNav();
    const counts = Array.from(
      container.querySelectorAll("nav > div > div"),
    ).map((group) => group.querySelectorAll("a").length);
    expect(counts).toEqual([3, 1, 5]);
  });

  it("points 偏好 at 通用 / 消息隐私 / 快捷键 / 关于 / 赞助", () => {
    renderNav();
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
    expect(
      screen.getByRole("link", { name: "赞助" }).getAttribute("href"),
    ).toBe("/more/sponsor");
    expect(screen.queryByRole("link", { name: "外观" })).toBeNull();
    expect(screen.queryByRole("link", { name: "反馈" })).toBeNull();
  });
});
