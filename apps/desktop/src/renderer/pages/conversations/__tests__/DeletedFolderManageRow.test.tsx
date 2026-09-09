// @vitest-environment jsdom
import { ApiError } from "@/services/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/folders", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/folders")>();
  return { ...actual, restoreFolder: vi.fn(), purgeTrashedFolder: vi.fn() };
});

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

import { notifyError, notifyInfo } from "@/lib/toast";
import { purgeTrashedFolder, restoreFolder } from "@/services/folders";
import { DeletedFolderManageRow } from "../DeletedFolderManageRow";

const restore = vi.mocked(restoreFolder);
const purge = vi.mocked(purgeTrashedFolder);
const errorToast = vi.mocked(notifyError);
const infoToast = vi.mocked(notifyInfo);

const deleted = {
  id: "f1",
  name: "商标案",
  mode: "cloud" as const,
  deletedAt: new Date(Date.now() - 3_600_000).toISOString(),
  purgeAt: new Date(Date.now() + 29 * 86_400_000).toISOString(),
};

function renderRow() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <DeletedFolderManageRow folder={deleted} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  restore.mockReset();
  purge.mockReset();
  errorToast.mockReset();
  infoToast.mockReset();
});

describe("DeletedFolderManageRow", () => {
  it("shows the remaining retention and restores the folder", async () => {
    restore.mockResolvedValue({
      id: "f1",
      name: "商标案",
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
    });
    renderRow();

    expect(screen.getByText("剩 28 天")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("恢复文件夹 商标案"));

    await waitFor(() => expect(restore).toHaveBeenCalledWith("f1"));
    expect(errorToast).not.toHaveBeenCalled();
    expect(infoToast).not.toHaveBeenCalled();
  });

  it("says so when the folder came back under another name", async () => {
    restore.mockResolvedValue({
      id: "f1",
      name: "商标案 (2)",
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
    });
    renderRow();

    fireEvent.click(screen.getByLabelText("恢复文件夹 商标案"));

    await waitFor(() => expect(infoToast).toHaveBeenCalledTimes(1));
    expect(infoToast.mock.calls[0][1]?.description).toContain("商标案 (2)");
  });

  it("surfaces the server's 409 verbatim instead of retrying", async () => {
    restore.mockRejectedValue(
      new ApiError(409, JSON.stringify({ detail: "该项目已被清理，无法恢复" })),
    );
    renderRow();

    fireEvent.click(screen.getByLabelText("恢复文件夹 商标案"));

    await waitFor(() => expect(errorToast).toHaveBeenCalledTimes(1));
    expect(errorToast.mock.calls[0][1]).toBe("恢复失败");
    expect(restore).toHaveBeenCalledTimes(1);
  });

  it("asks before permanently deleting", async () => {
    purge.mockResolvedValue(undefined);
    renderRow();

    fireEvent.click(screen.getByLabelText("彻底删除文件夹 商标案"));
    expect(purge).not.toHaveBeenCalled();
    expect(screen.getByText("彻底删除「商标案」？")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "彻底删除" }));
    await waitFor(() => expect(purge).toHaveBeenCalledWith("f1"));
  });
});
