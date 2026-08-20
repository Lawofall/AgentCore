// @vitest-environment jsdom
/**
 * Console shell: sidebar grouping + active-section logic, the narrow-screen drawer,
 * and the per-route error boundary that keeps one bad page from blanking the window.
 *
 * The drawer cases are deliberately picky about *where* the close control lives: the
 * header trigger is underneath the panel and the scrim once the drawer is open, so a
 * close affordance that only exists up there is a drawer you cannot shut.
 */

import { AdminShell } from "@/components/AdminShell";
import { useAuthStore } from "@/stores/auth";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/auth", () => ({ logout: vi.fn(() => Promise.resolve()) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: "u1",
      username: "root",
      displayName: "Root Admin",
      email: null,
      emailVerifiedAt: null,
      role: "admin",
      passwordMustChange: false,
    },
    mfaSetupRequired: false,
    pendingMfaToken: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function Boom(): never {
  throw new Error("row shape exploded");
}

function renderShell(path: string, element: React.ReactNode = <div>页面内容</div>) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AdminShell />}>
          <Route path="/overview" element={element} />
          <Route path="/users" element={element} />
          <Route path="/conversations/:segment" element={element} />
          <Route path="/replay/:id" element={element} />
          <Route path="/quota" element={element} />
          <Route path="/system" element={element} />
          <Route path="/account" element={element} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** aria-current is how the shell marks the section you are in. */
function currentNavLabel(): string | undefined {
  return document.querySelector('[aria-current="page"]')?.textContent?.trim();
}

describe("AdminShell navigation", () => {
  it("groups the nine sections instead of listing them flat", () => {
    renderShell("/overview");
    for (const group of ["监控", "排查", "管理"]) {
      expect(screen.getByText(group)).toBeTruthy();
    }
    const nav = screen.getByRole("navigation", { name: "主导航", hidden: true });
    expect(
      within(nav)
        .getAllByRole("link", { hidden: true })
        .map((a) => a.textContent?.trim()),
    ).toEqual([
      "概览",
      "分析",
      "对话",
      "审计",
      "用户",
      "公告",
      "内测群",
      "平台额度",
      "系统",
    ]);
  });

  it("marks the section matching the current route", () => {
    renderShell("/overview");
    expect(currentNavLabel()).toBe("概览");
  });

  it("marks 平台额度 on its own route", () => {
    renderShell("/quota");
    expect(currentNavLabel()).toBe("平台额度");
  });

  it("keeps 对话 lit while drilled into a replay", () => {
    // /replay/:id has no nav entry of its own — it is reached *from* 对话, and losing
    // the highlight there makes the drill-in feel like it left the console.
    renderShell("/replay/conv-1");
    expect(currentNavLabel()).toBe("对话");
  });

  it("marks the account row when on the account page", () => {
    renderShell("/account");
    // The row also holds an avatar initial, so match on the name rather than exactly.
    expect(currentNavLabel()).toContain("Root Admin");
  });
});

describe("AdminShell drawer", () => {
  it("opens from the narrow-screen trigger and closes from the panel's own control", () => {
    renderShell("/overview");
    const trigger = screen.getByLabelText("打开导航");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);
    expect(screen.getByLabelText("关闭导航")).toBeTruthy();
    // At 375px the panel and its scrim cover the header the trigger sits in, so
    // the way out has to be inside the drawer rather than behind it.
    expect(screen.getByRole("dialog").contains(screen.getByLabelText("关闭导航"))).toBe(
      true,
    );

    fireEvent.click(screen.getByLabelText("关闭导航"));
    expect(screen.queryByLabelText("关闭导航")).toBeNull();
  });

  it("keeps the trigger's aria state and the covered column honest", () => {
    renderShell("/overview");
    const trigger = screen.getByLabelText("打开导航");
    fireEvent.click(trigger);

    const panel = screen.getByRole("dialog");
    expect(panel.getAttribute("aria-modal")).toBe("true");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.getAttribute("aria-controls")).toBe(panel.id);
    // A button still labelled 「打开导航」 while its own aria-expanded reads true is
    // only defensible if nothing can reach it: the drawer owns the close action.
    expect(trigger.closest("[inert]")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("关闭导航"));
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-controls")).toBeNull();
    expect(trigger.closest("[inert]")).toBeNull();
  });

  it("moves focus into the drawer and hands it back on Esc", () => {
    renderShell("/overview");
    const trigger = screen.getByLabelText("打开导航");
    trigger.focus();

    fireEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByLabelText("关闭导航"));

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("still dismisses from the scrim, without announcing it as a second control", () => {
    renderShell("/overview");
    fireEvent.click(screen.getByLabelText("打开导航"));
    expect(screen.getAllByRole("button", { name: "关闭导航" })).toHaveLength(1);

    // The scrim is aria-hidden by design, so it can only be reached structurally.
    const scrim = screen.getByRole("dialog").previousElementSibling;
    if (!scrim) throw new Error("drawer scrim missing");
    fireEvent.mouseDown(scrim);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes itself when navigation actually happens", () => {
    renderShell("/overview");
    fireEvent.click(screen.getByLabelText("打开导航"));

    // The drawer renders a second copy of the nav; clicking either one navigates.
    fireEvent.click(screen.getAllByRole("link", { name: "用户" })[0]);
    expect(screen.queryByLabelText("关闭导航")).toBeNull();
  });
});

describe("AdminShell resilience", () => {
  it("contains a page crash instead of blanking the console", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderShell("/overview", <Boom />);

    expect(screen.getByText("这个页面出错了")).toBeTruthy();
    expect(screen.getByText("row shape exploded")).toBeTruthy();
    // The shell itself survives, so there is still a way out.
    expect(screen.getAllByRole("link", { name: "用户" }).length).toBeGreaterThan(0);
    consoleError.mockRestore();
  });

  it("offers a skip link ahead of the sidebar", () => {
    renderShell("/overview");
    const skip = screen.getByRole("link", { name: "跳到主内容" });
    expect(skip.getAttribute("href")).toBe("#main");
  });
});
