// @vitest-environment jsdom

import { BorrowToCloudDialog } from "@/components/files/BorrowToCloudDialog";
import { ImportToCloudDialog } from "@/components/files/ImportToCloudDialog";
import { pickLocalFolderRoot } from "@/lib/bindLocalFolder";
import { startBorrowToCloudJob } from "@/lib/borrowToCloudJob";
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
vi.mock("@/lib/borrowToCloudJob", () => ({
  isBorrowToCloudJobRunning: () => false,
  startBorrowToCloudJob: vi.fn(),
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

const USER_FACE_FORBIDDEN = /合回|过桥|遗留|云协作|本机传统|sidecar|通道/;

describe("BorrowToCloudDialog", () => {
  it("explains copy-to-cloud, original stays, write-back later; not opening local to edit", () => {
    render(<BorrowToCloudDialog open onOpenChange={() => {}} />);
    expect(screen.getByText("云上做完再写入")).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toContain("复制到云上做这一单");
    expect(text).toContain("原件先不动");
    expect(text).toContain("做完再决定写不写回");
    expect(text).toContain("不是打开本机文件夹直接改");
    expect(text).not.toMatch(USER_FACE_FORBIDDEN);
    expect(text).not.toContain("请在新文件夹里继续");
    expect(text).not.toContain("当前对话用的还是本机原文件夹");
  });

  it("picker failure is muted, not destructive", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: false,
      reason: "unavailable",
      message: "当前环境不能选本机文件夹",
    });
    render(<BorrowToCloudDialog open onOpenChange={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByText("选择文件夹…"));
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("当前环境不能选本机文件夹");
    expect(alert.className).toContain("text-muted-foreground");
    expect(alert.className).not.toContain("destructive");
  });

  it("submits with ownsRoot left to the job (authorized root stays)", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyApp" },
    });
    vi.mocked(startBorrowToCloudJob).mockReturnValue(true);
    const onOpenChange = vi.fn();
    render(<BorrowToCloudDialog open onOpenChange={onOpenChange} />);
    await act(async () => {
      fireEvent.click(screen.getByText("选择文件夹…"));
    });
    fireEvent.change(screen.getByPlaceholderText("默认取本机文件夹名"), {
      target: { value: "CloudCopy" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "开始" }));
    });
    expect(startBorrowToCloudJob).toHaveBeenCalledWith({
      root: { id: "root-1", name: "MyApp" },
      folderName: "CloudCopy",
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("prefills a Composer-picked root without asking to pick again", async () => {
    window.fsApi = {
      listRoots: vi.fn().mockResolvedValue([{ id: "root-1", name: "MyApp" }]),
    } as unknown as typeof window.fsApi;
    render(
      <BorrowToCloudDialog
        open
        onOpenChange={() => {}}
        prefill={{
          rootId: "root-1",
          folderName: "CloudCopy",
          ownsRoot: true,
        }}
      />,
    );
    expect(await screen.findByText("MyApp")).toBeTruthy();
    expect(screen.getByDisplayValue("CloudCopy")).toBeTruthy();
  });
});

describe("ImportToCloudDialog copy stays the import wording", () => {
  it("keeps the original import description", () => {
    render(<ImportToCloudDialog open onOpenChange={() => {}} />);
    expect(
      screen.getByText(
        "把选中的本机文件夹复制一份到「我的文件」。之后改的是云上这份副本，本机原文件夹不会跟着变，两边也不会自动同步。",
      ),
    ).toBeTruthy();
    expect(screen.getByText("导入到「我的文件」")).toBeTruthy();
  });
});
