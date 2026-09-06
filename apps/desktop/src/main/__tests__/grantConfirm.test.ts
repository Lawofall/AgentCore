import { type Mock, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  dialog: { showMessageBox: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
}));

import { dialog } from "electron";
import { confirmFolderWriteGrant, sessionModeCovers } from "../fs/grantConfirm";

const showMessageBox = dialog.showMessageBox as unknown as Mock;

beforeEach(() => {
  showMessageBox.mockReset();
});

describe("sessionModeCovers", () => {
  it("readonly < organize < attach_rw", () => {
    expect(sessionModeCovers("readonly", "organize")).toBe(false);
    expect(sessionModeCovers("readonly", "attach_rw")).toBe(false);
    expect(sessionModeCovers("organize", "organize")).toBe(true);
    expect(sessionModeCovers("organize", "attach_rw")).toBe(false);
    expect(sessionModeCovers("attach_rw", "organize")).toBe(true);
    expect(sessionModeCovers(undefined, "organize")).toBe(false);
  });
});

describe("confirmFolderWriteGrant", () => {
  it("取消（response 0）→ false；默认 / Esc 均为取消", async () => {
    showMessageBox.mockResolvedValueOnce({ response: 0 });
    await expect(
      confirmFolderWriteGrant({ mode: "organize", displayLabel: "咨询" }),
    ).resolves.toBe(false);
    const box = showMessageBox.mock.calls.at(-1)?.[0];
    expect(box.defaultId).toBe(0);
    expect(box.cancelId).toBe(0);
    expect(box.type).toBe("warning");
    expect(box.buttons).toEqual(["取消", "允许整理"]);
    expect(box.message).toContain("整理");
    expect(box.detail).toContain("咨询");
    expect(box.detail).toContain("不覆盖");
  });

  it("允许（response 1）→ true", async () => {
    showMessageBox.mockResolvedValueOnce({ response: 1 });
    await expect(
      confirmFolderWriteGrant({ mode: "attach_rw", displayLabel: "工程" }),
    ).resolves.toBe(true);
    const box = showMessageBox.mock.calls.at(-1)?.[0];
    expect(box.buttons).toEqual(["取消", "允许改这个目录"]);
    expect(box.detail).toContain("可覆盖");
  });
});
