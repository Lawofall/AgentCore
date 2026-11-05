// @vitest-environment jsdom
import { CreateFolderCascadePanel } from "@/components/folders/CreateFolderPanel";
import { useFoldersStore } from "@/stores/folders";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mutateAsync } = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
}));

vi.mock("@/hooks/useFolders", () => ({
  useCreateFolder: () => ({ mutateAsync, isPending: false }),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: {
    getState: () => ({ currentConversationId: "c1" }),
  },
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

describe("CreateFolderCascadePanel", () => {
  beforeEach(() => {
    mutateAsync.mockReset();
    useFoldersStore.setState({
      pendingRevealFolderId: null,
      pendingRenameFolderId: null,
    });
  });

  afterEach(cleanup);

  it("names the folder then reveals it without entering rename", async () => {
    mutateAsync.mockResolvedValue({
      folder: {
        id: "f-new",
        name: "设计",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
      },
      created: true,
    });
    render(<CreateFolderCascadePanel onClose={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("文件夹名称"), {
      target: { value: "设计" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        name: "设计",
        mode: "cloud",
      });
      expect(useFoldersStore.getState().pendingRevealFolderId).toBe("f-new");
      expect(useFoldersStore.getState().pendingRenameFolderId).toBeNull();
    });
  });
});
