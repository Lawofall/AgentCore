// @vitest-environment jsdom
/**
 * PermissionAxesBadge — recipe popover +「设为新会话默认」(user-level autonomy).
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
      expect(screen.getByLabelText("权限：少打断")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("权限：少打断"));
    const btn = screen.getByRole("button", { name: "设为新会话默认" });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(setUserDefaultMock).toHaveBeenCalledWith("less_interrupt");
      expect(notifySuccess).toHaveBeenCalledWith(
        expect.stringContaining("少打断"),
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
});
