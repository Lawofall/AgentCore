// @vitest-environment jsdom
/**
 * 设置二级导航的信息架构：三组九项（关于在偏好末项）。
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
  it("groups the nine sub-pages under three headings", () => {
    const { container } = renderNav();
    const groups = Array.from(container.querySelectorAll("nav h2")).map(
      (h) => h.textContent,
    );
    expect(groups).toEqual(["账户", "模型", "偏好"]);
    expect(container.querySelectorAll("nav a")).toHaveLength(9);
  });

  it("keeps every group multi-item, so no heading outweighs its content", () => {
    const { container } = renderNav();
    for (const group of container.querySelectorAll("nav > div > div")) {
      expect(group.querySelectorAll("a").length).toBeGreaterThan(1);
    }
  });

  it("points 偏好 at 通用 / 消息隐私 / 快捷键 / 关于", () => {
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
    expect(screen.queryByRole("link", { name: "外观" })).toBeNull();
    expect(screen.queryByRole("link", { name: "反馈" })).toBeNull();
  });
});
