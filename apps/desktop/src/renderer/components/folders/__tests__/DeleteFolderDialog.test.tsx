// @vitest-environment jsdom
import { DeleteFolderDialog } from "@/components/folders/DeleteFolderDialog";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useFolders", () => ({
  useFolderTrash: () => ({ data: { retentionDays: 30 } }),
}));

afterEach(cleanup);

function renderDialog(
  over: Partial<Parameters<typeof DeleteFolderDialog>[0]> = {},
) {
  const props = {
    open: true,
    onOpenChange: vi.fn(),
    name: "季度报告",
    liveConvCount: 2,
    isLocal: false,
    onConfirm: vi.fn(),
    onPermanentConfirm: vi.fn(),
    ...over,
  };
  render(<DeleteFolderDialog {...props} />);
  return props;
}

describe("DeleteFolderDialog 设定口径", () => {
  it("默认软删：设定退出注入，恢复时回来", () => {
    renderDialog();
    expect(screen.getByText(/30 天内可在「最近删除」中恢复/)).toBeTruthy();
    expect(
      screen.getByText(/这张桌子的 AI\s*设定.*一并退出.*恢复文件夹时一起回来/),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "立即永久清除全部对话、云端文件与这张桌的设定（不可恢复）",
      ),
    ).toBeTruthy();
  });

  it("勾永久清除：设定一并不可恢复", () => {
    const props = renderDialog();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /立即永久清除全部对话、云端文件与这张桌的设定/,
      }),
    );
    expect(
      screen.getByText(
        "将永久删除全部对话、云端文件，以及这张桌子的 AI 设定，不可恢复。",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "彻底删除" }));
    expect(props.onPermanentConfirm).toHaveBeenCalledTimes(1);
    expect(props.onConfirm).not.toHaveBeenCalled();
  });
});
