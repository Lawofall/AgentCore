// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { MessageAttachmentMeta } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AttachmentChip } from "../AttachmentChip";

const { showFile } = vi.hoisted(() => ({
  showFile: vi.fn(),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: { showFile: typeof showFile }) => unknown) =>
    sel({ showFile }),
}));

afterEach(() => {
  cleanup();
  showFile.mockReset();
});

const fileAtt: MessageAttachmentMeta = {
  id: "a1",
  name: "shot.png",
  path: "attachments/shot.png",
  truncated: false,
  kind: "file",
  workspacePath: "attachments/shot.png",
};

function renderChip(att: MessageAttachmentMeta, interactive?: boolean) {
  return render(
    <TooltipProvider>
      <AttachmentChip att={att} interactive={interactive} />
    </TooltipProvider>,
  );
}

describe("AttachmentChip", () => {
  it("history file chip opens the workspace File tab", () => {
    renderChip(fileAtt, true);
    fireEvent.click(screen.getByRole("button", { name: "打开 shot.png" }));
    expect(showFile).toHaveBeenCalledWith("attachments/shot.png", "shot.png");
  });

  it("is a citation label when not interactive", () => {
    renderChip(fileAtt);
    expect(screen.getByText("shot.png")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(showFile).not.toHaveBeenCalled();
  });

  it("dir / conversation / document chips stay labels", () => {
    renderChip(
      {
        id: "d1",
        name: "docs",
        path: "docs",
        truncated: false,
        kind: "dir",
        workspacePath: "docs",
      },
      true,
    );
    expect(screen.queryByRole("button")).toBeNull();

    cleanup();
    renderChip(
      {
        id: "c1",
        name: "旧对话",
        path: "旧对话",
        truncated: false,
        kind: "conversation",
        conversationId: "other",
      },
      true,
    );
    expect(screen.queryByRole("button")).toBeNull();

    cleanup();
    renderChip(
      {
        id: "p1",
        name: "风格",
        path: "风格",
        truncated: false,
        kind: "document",
        documentId: "doc-1",
      },
      true,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("file without workspacePath stays a label", () => {
    renderChip(
      {
        id: "a2",
        name: "pending.png",
        path: "pending.png",
        truncated: false,
        kind: "file",
      },
      true,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("unpacked archive chip opens the sibling folder", () => {
    renderChip(
      {
        ...fileAtt,
        name: "pack.zip",
        path: "attachments/pack.zip",
        workspacePath: "attachments/pack.zip",
        archiveDir: "attachments/pack",
        archiveStatus: "ok",
      },
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开 pack.zip" }));
    expect(showFile).toHaveBeenCalledWith("attachments/pack", "pack");
  });

  it("opens when kind is omitted (interjection attachments)", () => {
    renderChip({ ...fileAtt, kind: undefined }, true);
    fireEvent.click(screen.getByRole("button", { name: "打开 shot.png" }));
    expect(showFile).toHaveBeenCalledWith("attachments/shot.png", "shot.png");
  });
});
