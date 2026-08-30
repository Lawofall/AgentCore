// @vitest-environment jsdom
/**
 * PermissionAxesBadge — 三配方默认面 +「设为新会话默认」；轴在「改某一条」后。
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
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
}));
vi.mock("@/services/permissionAxes", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/permissionAxes")>();
  return {
    ...actual,
    resolveDefaultPermissionAxes: vi.fn(
      async () => actual.RECIPE_AXES.less_interrupt,
    ),
    setUserDefaultRecipe: vi.fn(async (p: string) => p),
    setConversationPermissionAxes: vi.fn(),
    setComposerDraftAxes: vi.fn(),
    confirmAutoCommandIfNeeded: vi.fn(() => true),
  };
});

import { TooltipProvider } from "@/components/ui/tooltip";
import { notifyError, notifySuccess } from "@/lib/toast";
import { setUserDefaultRecipe } from "@/services/permissionAxes";
import { useConversationStore } from "@/stores/conversation";
import { PermissionAxesBadge } from "../PermissionPresetBadge";

const setUserDefaultMock = vi.mocked(setUserDefaultRecipe);

function renderBadge() {
  return render(
    <TooltipProvider>
      <PermissionAxesBadge />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useConversationStore.setState({ currentConversationId: null });
  setUserDefaultMock.mockClear();
  vi.mocked(notifySuccess).mockClear();
  vi.mocked(notifyError).mockClear();
});

afterEach(cleanup);

describe("PermissionAxesBadge", () => {
  it("sets user default when current axes match a built-in recipe", async () => {
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText("权限：全放行")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：全放行"));
    const btn = screen.getByRole("button", { name: "设为新会话默认" });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(setUserDefaultMock).toHaveBeenCalledWith("less_interrupt");
      expect(notifySuccess).toHaveBeenCalledWith(
        expect.stringContaining("全放行"),
      );
    });
  });

  it("disables set-default when axes are custom", async () => {
    const { resolveDefaultPermissionAxes } = await import(
      "@/services/permissionAxes"
    );
    vi.mocked(resolveDefaultPermissionAxes).mockResolvedValueOnce({
      file_write: "session",
      command: "ask",
      host: "ask",
    });
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText(/权限：/));
    const btn = screen.getByRole("button", { name: "设为新会话默认" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(btn);
    expect(setUserDefaultMock).not.toHaveBeenCalled();
  });

  it("配方会话打开后只见三档，轴在「改某一条」之后", async () => {
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText("权限：全放行")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：全放行"));
    expect(screen.getByText("谨慎")).toBeTruthy();
    expect(screen.getByText("托管")).toBeTruthy();
    expect(screen.queryByText("改文件")).toBeNull();
    expect(screen.queryByText("执行命令")).toBeNull();
    expect(screen.queryByText("本机 Host")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "改某一条" }));
    expect(screen.getByText("改文件")).toBeTruthy();
    expect(screen.getByText("执行命令")).toBeTruthy();
    expect(screen.getByText("本机 Host")).toBeTruthy();
  });

  it("已经是自定义时，打开就看见轴", async () => {
    const { resolveDefaultPermissionAxes } = await import(
      "@/services/permissionAxes"
    );
    vi.mocked(resolveDefaultPermissionAxes).mockResolvedValueOnce({
      file_write: "session",
      command: "ask",
      host: "ask",
    });
    renderBadge();
    await waitFor(() => {
      expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText(/权限：/));
    expect(screen.getByText("改文件")).toBeTruthy();
    expect(screen.getByText("执行命令")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "改某一条" })).toBeNull();
  });
});
