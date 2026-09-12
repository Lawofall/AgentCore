import { ToolboxShell } from "@/pages/toolbox/ToolboxShell";
import { isKnownAppRoute } from "@/pages/toolbox/manual/gates/appRoutes";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function renderShell(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/toolbox" element={<ToolboxShell />}>
          <Route path="mine/skills" element={<div>技能内容</div>} />
          <Route path="mine/creation" element={<div>创作内容</div>} />
          <Route path="mine/mcp" element={<div>MCP内容</div>} />
          <Route path="mine/workflows" element={<div>工作流内容</div>} />
          <Route
            path="market"
            element={<div data-testid="market">市场内容</div>}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("工具箱壳", () => {
  it("顶栏是种类 tab + 右槽市场，不重复可见「工具箱」标题", () => {
    renderShell(APP_PATHS.toolbox.mine.skills);
    expect(
      screen.getByRole("heading", { level: 1, name: "提示词" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("heading", { level: 1, name: "工具箱" }),
    ).toBeNull();
    expect(screen.queryByRole("tablist", { name: "工具箱" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "我的" })).toBeNull();
    const kinds = screen.getByRole("navigation", { name: "工具箱种类" });
    const kindLinks = within(kinds).getAllByRole("link").slice(0, 3);
    expect(kindLinks.map((el) => el.textContent)).toEqual([
      "提示词",
      "创作",
      "工作流",
    ]);
    expect(kindLinks.every((el) => el.querySelector("svg"))).toBe(true);
    expect(
      screen.getByRole("link", { name: "市场" }).getAttribute("href"),
    ).toBe(APP_PATHS.toolbox.market);
    expect(screen.queryByRole("link", { name: "手册" })).toBeNull();
    expect(screen.queryByRole("link", { name: "连接器" })).toBeNull();
    expect(screen.queryByRole("link", { name: "工具箱" })).toBeNull();
    expect(screen.getByText("技能内容")).toBeTruthy();
  });

  it("种类没有独立工具 tab", () => {
    renderShell(APP_PATHS.toolbox.mine.skills);
    const kinds = screen.getByRole("navigation", { name: "工具箱种类" });
    expect(within(kinds).queryByRole("link", { name: "工具" })).toBeNull();
    expect(screen.queryByRole("link", { name: "连接器" })).toBeNull();
    expect(screen.getByRole("link", { name: "市场" })).toBeTruthy();
  });

  it("提示词与其它种类一样走页面留白", () => {
    const skills = renderShell(APP_PATHS.toolbox.mine.skills);
    const skillsInner = skills.container.querySelector(".mx-auto");
    expect(skillsInner?.className).toContain("px-6");
    expect(skillsInner?.className).toContain("py-6");
    cleanup();
    const creation = renderShell(APP_PATHS.toolbox.mine.creation);
    const creationInner = creation.container.querySelector(".mx-auto");
    expect(creationInner?.className).toContain("px-6");
    expect(creationInner?.className).toContain("py-6");
  });

  it("切到市场后种类 tab 仍在，页头市场为当前页", () => {
    renderShell(APP_PATHS.toolbox.mine.skills);
    fireEvent.click(screen.getByRole("link", { name: "市场" }));
    expect(screen.getByTestId("market")).toBeTruthy();
    const kinds = screen.getByRole("navigation", { name: "工具箱种类" });
    expect(kinds).toBeTruthy();
    for (const label of ["提示词", "创作", "工作流"]) {
      expect(
        within(kinds)
          .getByRole("link", { name: label })
          .getAttribute("aria-current"),
      ).not.toBe("page");
    }
    expect(
      screen.getByRole("link", { name: "市场" }).getAttribute("aria-current"),
    ).toBe("page");
    expect(
      screen.getByRole("heading", { level: 1, name: "市场" }),
    ).toBeTruthy();
  });

  it("创作 tab 可达", () => {
    renderShell(APP_PATHS.toolbox.mine.skills);
    fireEvent.click(screen.getByRole("link", { name: "创作" }));
    expect(screen.getByText("创作内容")).toBeTruthy();
  });

  it("商店别名仍是已知路由", () => {
    expect(APP_PATHS.toolbox.store).toBe("/toolbox/market");
    expect(isKnownAppRoute(APP_PATHS.toolbox.market)).toBe(true);
    expect(isKnownAppRoute("/toolbox/store")).toBe(true);
    expect(isKnownAppRoute(APP_PATHS.toolbox.mine.creation)).toBe(true);
  });
});
