import { ToolboxPage } from "@/pages/ToolboxPage";
import { isKnownAppRoute } from "@/pages/toolbox/manual/gates/appRoutes";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { useStandingInboxStore } from "@/stores/standingInbox";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

beforeEach(() => {
  useStandingInboxStore.setState({ badge: 0 });
});

afterEach(cleanup);

function renderHome() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.root]}>
      <Routes>
        <Route path={APP_PATHS.toolbox.root} element={<ToolboxPage />} />
        <Route
          path={APP_PATHS.toolbox.store}
          element={<div data-testid="store-page">商店页</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("工具箱商店入口", () => {
  it("能力组有商店瓦片，点进去走 /toolbox/store", () => {
    renderHome();
    expect(APP_PATHS.toolbox.store).toBe("/toolbox/store");
    expect(isKnownAppRoute(APP_PATHS.toolbox.store)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /商店/ }));
    expect(screen.getByTestId("store-page")).toBeTruthy();
  });
});

describe("工具箱首页自动化角标", () => {
  it("收件箱未读挂在自动化磁贴上", () => {
    useStandingInboxStore.setState({ badge: 4 });
    renderHome();
    const tile = screen.getByRole("button", { name: /自动化/ });
    expect(within(tile).getByLabelText("4 条待处理").textContent).toBe("4");
  });

  it("徽章过百收敛成 99+", () => {
    useStandingInboxStore.setState({ badge: 128 });
    renderHome();
    const tile = screen.getByRole("button", { name: /自动化/ });
    expect(within(tile).getByLabelText("128 条待处理").textContent).toBe("99+");
  });
});
