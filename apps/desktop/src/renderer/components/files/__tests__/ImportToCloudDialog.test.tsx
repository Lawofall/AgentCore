// @vitest-environment jsdom

import { ImportToCloudDialog } from "@/components/files/ImportToCloudDialog";
import { pickLocalFolderRoot } from "@/lib/bindLocalFolder";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/bindLocalFolder", () => ({
  pickLocalFolderRoot: vi.fn(),
}));
vi.mock("@/lib/importToCloudJob", () => ({
  isImportToCloudJobRunning: () => false,
  startImportToCloudJob: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
}));

afterEach(() => {
  cleanup();
});

describe("ImportToCloudDialog submit failure tone", () => {
  it("picker failure is muted, not destructive", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: false,
      reason: "unavailable",
      message: "当前环境不能选本机文件夹",
    });
    render(<ImportToCloudDialog open onOpenChange={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByText("选择文件夹…"));
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("当前环境不能选本机文件夹");
    expect(alert.className).toContain("text-muted-foreground");
    expect(alert.className).not.toContain("destructive");
  });

  it("keeps the original import description", () => {
    render(<ImportToCloudDialog open onOpenChange={() => {}} />);
    expect(
      screen.getByText(
        "把选中的本机文件夹复制一份到「我的文件」。之后改的是云上这份副本，本机原文件夹不会跟着变，两边也不会自动同步。",
      ),
    ).toBeTruthy();
  });
});
