// @vitest-environment jsdom
/**
 * PermissionAxesBadge — 四档边界；这台电脑仅本机引擎可见；完全权限全端可见。
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  useConversations: vi.fn(() => []),
  patchConversationCache: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));
vi.mock("@/lib/capabilities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/capabilities")>();
  return {
    ...actual,
    hasLocalEngine: vi.fn(() => false),
  };
});
vi.mock("@/services/permissionAxes", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/permissionAxes")>();
  return {
    ...actual,
    resolveDefaultPermissionAxes: vi.fn(async () => ({
      boundary: "folder" as const,
    })),
    rememberBoundarySelection: vi.fn(),
    setConversationPermissionAxes: vi.fn(),
    setComposerDraftAxes: vi.fn(),
    confirmComputerIfNeeded: vi.fn(() => true),
  };
});

import { TooltipProvider } from "@/components/ui/tooltip";
import { hasLocalEngine } from "@/lib/capabilities";
import { notifyError } from "@/lib/toast";
import {
  rememberBoundarySelection,
  resolveDefaultPermissionAxes,
} from "@/services/permissionAxes";
import { useConversationStore } from "@/stores/conversation";
import { PermissionAxesBadge } from "../PermissionPresetBadge";

const rememberMock = vi.mocked(rememberBoundarySelection);

function renderBadge() {
  return render(
    <TooltipProvider>
      <PermissionAxesBadge />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null });
  rememberMock.mockClear();
  vi.mocked(notifyError).mockClear();
  vi.mocked(hasLocalEngine).mockReturnValue(false);
  vi.mocked(resolveDefaultPermissionAxes).mockResolvedValue({
    boundary: "folder",
  });
});

afterEach(cleanup);

describe("PermissionAxesBadge", () => {
  it("remembers the picked boundary and has no set-as-default action", async () => {
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText("权限：本文件夹")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：本文件夹"));
    expect(screen.queryByRole("button", { name: "设为新会话默认" })).toBeNull();
    fireEvent.click(screen.getByText("只看"));
    expect(rememberMock).toHaveBeenCalledWith("read");
  });

  it("web hides 这台电脑 and shows 只看 / 本文件夹 / 完全权限", async () => {
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText("权限：本文件夹")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：本文件夹"));
    expect(screen.getByText("只看")).toBeTruthy();
    expect(screen.getByText("本文件夹 · 荐")).toBeTruthy();
    expect(screen.getByText("完全权限")).toBeTruthy();
    expect(
      screen.getByText("只能看和搜索。不能改文件、跑命令、用浏览器。"),
    ).toBeTruthy();
    expect(screen.queryByText("这台电脑")).toBeNull();
    expect(screen.queryByText("改某一条")).toBeNull();
    expect(screen.queryByText("谨慎")).toBeNull();
  });

  it("desktop offers 这台电脑 and 完全权限", async () => {
    vi.mocked(hasLocalEngine).mockReturnValue(true);
    vi.mocked(resolveDefaultPermissionAxes).mockResolvedValue({
      boundary: "computer",
    });
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText("权限：这台电脑")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：这台电脑"));
    expect(screen.getByText("这台电脑 · 荐")).toBeTruthy();
    expect(screen.getByText("完全权限")).toBeTruthy();
    expect(screen.getByText("本文件夹")).toBeTruthy();
  });
});
