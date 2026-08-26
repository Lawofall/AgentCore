import { hasInAppPreview } from "@/lib/capabilities";
import { useSidePanelStore } from "@/stores/sidePanel";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { openWorkspaceDeliverable } from "../openWorkspaceDeliverable";
import { openWorkspaceHtmlInBrowser } from "../openWorkspaceHtmlInBrowser";

vi.mock("@/lib/capabilities", () => ({
  hasInAppPreview: vi.fn(() => false),
}));

vi.mock("@/lib/openWorkspaceHtmlInBrowser", () => ({
  openWorkspaceHtmlInBrowser: vi.fn(),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: {
    getState: () => ({ showFile: vi.fn() }),
  },
}));

const showFile = vi.fn();
const preview = vi.mocked(hasInAppPreview);
const openHtml = vi.mocked(openWorkspaceHtmlInBrowser);

describe("openWorkspaceDeliverable", () => {
  beforeEach(() => {
    showFile.mockReset();
    openHtml.mockReset();
    preview.mockReturnValue(false);
    vi.mocked(useSidePanelStore).getState = () => ({ showFile }) as never;
  });

  it("opens a markdown path in the File tab", () => {
    openWorkspaceDeliverable("c1", "AgentCore/文档/工作稿/白板PRD.md");
    expect(showFile).toHaveBeenCalledWith(
      "AgentCore/文档/工作稿/白板PRD.md",
      "白板PRD.md",
      undefined,
    );
    expect(openHtml).not.toHaveBeenCalled();
  });

  it("sends HTML to the in-app browser when preview is available", () => {
    preview.mockReturnValue(true);
    openWorkspaceDeliverable("c1", "site/index.html");
    expect(openHtml).toHaveBeenCalledWith("c1", "site/index.html", undefined);
    expect(showFile).not.toHaveBeenCalled();
  });
});
