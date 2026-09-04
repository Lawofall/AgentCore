// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "../Sidebar";

vi.mock("@/lib/newConversation", () => ({
  startNewConversation: vi.fn(),
}));

vi.mock("@/stores/messaging", () => ({
  useUnreadTotal: () => 0,
}));

vi.mock("../RecentConversations", () => ({
  RecentConversations: () => null,
  ViewAllConversations: () => null,
}));

vi.mock("../PinnedConversations", () => ({
  PinnedConversations: () => null,
}));

vi.mock("../WorkspaceGroups", () => ({
  WorkspaceGroups: () => null,
}));

vi.mock("../UserMenu", () => ({
  UserMenu: () => <div data-testid="user-menu" />,
}));

vi.mock("@/lib/capabilities", () => ({
  isWebClient: () => false,
}));

vi.mock("@/lib/railHotkeys", () => ({
  RailHotkeySlotsProvider: ({
    children,
  }: {
    children: React.ReactNode;
  }) => children,
}));

function renderSidebar() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <Sidebar />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useSidebarStore.setState({ collapsed: false });
  useUIStore.setState({ searchOpen: false, searchInitialQuery: "" });
});

afterEach(() => {
  cleanup();
});

describe("Sidebar · 折叠导航可达性", () => {
  it("折叠态主导航按钮带 aria-label", () => {
    useSidebarStore.setState({ collapsed: true });
    renderSidebar();

    expect(screen.getByRole("button", { name: "新对话" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "文件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "消息" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "工具箱" })).toBeTruthy();
  });

  it("折叠态搜索假入口带 aria-label（桌面无顶栏搜索）", () => {
    useSidebarStore.setState({ collapsed: true });
    renderSidebar();

    expect(screen.getByRole("button", { name: "搜索或运行命令" })).toBeTruthy();
  });
});

describe("Sidebar · 桌面搜索假入口", () => {
  // isWebClient mocked false — desktop Electron path; search must live here, not TitleBar.
  it("展开态渲染搜索假入口并打开命令面板", () => {
    renderSidebar();

    const trigger = screen.getByRole("button", { name: /搜索或运行命令/ });
    fireEvent.click(trigger);
    expect(useUIStore.getState().searchOpen).toBe(true);
  });

  it("搜索假入口和主导航同在一个 nav 栈", () => {
    renderSidebar();

    const nav = screen.getByRole("navigation");
    expect(
      within(nav).getByRole("button", { name: /搜索或运行命令/ }),
    ).toBeTruthy();
    expect(within(nav).getByRole("button", { name: "新对话" })).toBeTruthy();
  });
});
