import { join } from "node:path";
import { type Mock, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  dialog: { showMessageBox: vi.fn() },
  BrowserWindow: { getFocusedWindow: () => null, getAllWindows: () => [] },
}));

import { dialog } from "electron";
import {
  absIsUnderAnyRoot,
  confirmFolderReadGrant,
  confirmFolderWriteGrant,
  grantNeedsConfirm,
  sessionModeCovers,
} from "../fs/grantConfirm";

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

describe("grantNeedsConfirm", () => {
  it("well-known and inbox abs stay silent on first readonly mint", () => {
    expect(
      grantNeedsConfirm({
        mode: "readonly",
        haveMode: undefined,
        wellKnown: "downloads",
        absUnderInbox: false,
      }),
    ).toBe(false);
    expect(
      grantNeedsConfirm({
        mode: "readonly",
        haveMode: undefined,
        wellKnown: null,
        absUnderInbox: true,
      }),
    ).toBe(false);
  });

  it("model-invented non-inbox abs confirms on first readonly mint", () => {
    expect(
      grantNeedsConfirm({
        mode: "readonly",
        haveMode: undefined,
        wellKnown: null,
        absUnderInbox: false,
      }),
    ).toBe(true);
  });

  it("already-granted session root does not re-ask", () => {
    expect(
      grantNeedsConfirm({
        mode: "readonly",
        haveMode: "readonly",
        wellKnown: null,
        absUnderInbox: false,
      }),
    ).toBe(false);
  });

  it("organize / attach_rw still confirm even under inbox", () => {
    expect(
      grantNeedsConfirm({
        mode: "organize",
        haveMode: "readonly",
        wellKnown: "desktop",
        absUnderInbox: true,
      }),
    ).toBe(true);
  });

  it("computer and full skip folder dialogs including write upgrades", () => {
    const invented = {
      mode: "readonly" as const,
      haveMode: undefined,
      wellKnown: null,
      absUnderInbox: false,
    };
    expect(grantNeedsConfirm({ ...invented, boundary: "computer" })).toBe(
      false,
    );
    expect(grantNeedsConfirm({ ...invented, boundary: "full" })).toBe(false);
    expect(grantNeedsConfirm({ ...invented, boundary: "folder" })).toBe(true);
    expect(grantNeedsConfirm({ ...invented, boundary: "read" })).toBe(true);
    expect(
      grantNeedsConfirm({
        mode: "attach_rw",
        haveMode: "readonly",
        wellKnown: "desktop",
        absUnderInbox: true,
        boundary: "computer",
      }),
    ).toBe(false);
    expect(
      grantNeedsConfirm({
        mode: "organize",
        haveMode: undefined,
        wellKnown: null,
        absUnderInbox: false,
        boundary: "full",
      }),
    ).toBe(false);
  });
});

describe("absIsUnderAnyRoot", () => {
  it("matches the inbox root and a nested file", () => {
    const inbox = join("/tmp", "Downloads");
    expect(absIsUnderAnyRoot(inbox, [inbox])).toBe(true);
    expect(absIsUnderAnyRoot(join(inbox, "pack.zip"), [inbox])).toBe(true);
    expect(absIsUnderAnyRoot(join("/tmp", "Projects", "app"), [inbox])).toBe(
      false,
    );
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

describe("confirmFolderReadGrant", () => {
  it("取消为默认；允许只读", async () => {
    showMessageBox.mockResolvedValueOnce({ response: 0 });
    await expect(
      confirmFolderReadGrant({ displayLabel: "工程" }),
    ).resolves.toBe(false);
    showMessageBox.mockResolvedValueOnce({ response: 1 });
    await expect(
      confirmFolderReadGrant({ displayLabel: "工程" }),
    ).resolves.toBe(true);
    const box = showMessageBox.mock.calls.at(-1)?.[0];
    expect(box.defaultId).toBe(0);
    expect(box.buttons).toEqual(["取消", "允许只读"]);
    expect(box.detail).toContain("不能改原件");
  });
});
