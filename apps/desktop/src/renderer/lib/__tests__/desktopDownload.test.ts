import {
  DESKTOP_DOWNLOAD_URL,
  DESKTOP_REQUIRED_HINT,
  DESKTOP_REQUIRED_MESSAGE,
  guideDesktopDownload,
  isDesktopFolderAction,
} from "@/lib/desktopDownload";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("desktopDownload", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("recognizes grant / bind / open / register local project actions", () => {
    expect(isDesktopFolderAction("grant_readonly_folder")).toBe(false);
    expect(isDesktopFolderAction("grant_organize_folder")).toBe(true);
    expect(isDesktopFolderAction("bind_local_folder")).toBe(true);
    expect(isDesktopFolderAction("open_local_project")).toBe(true);
    expect(isDesktopFolderAction("register_local_project")).toBe(true);
    expect(isDesktopFolderAction(undefined)).toBe(false);
    expect(isDesktopFolderAction("continue_cloud")).toBe(false);
  });

  it("opens official download page and returns desktop-required message", () => {
    const open = vi.fn();
    vi.stubGlobal("window", { open });
    const msg = guideDesktopDownload();
    expect(open).toHaveBeenCalledWith(
      DESKTOP_DOWNLOAD_URL,
      "_blank",
      "noopener,noreferrer",
    );
    expect(msg).toBe(DESKTOP_REQUIRED_MESSAGE);
    expect(msg).toContain(DESKTOP_REQUIRED_HINT);
    expect(msg).toContain("https://fashitianxia.xyz/download");
  });
});
