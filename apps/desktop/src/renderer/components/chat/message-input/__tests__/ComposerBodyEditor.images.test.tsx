// @vitest-environment jsdom
import { inlineToken } from "@/lib/inlineBody";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ComposerBodyEditor } from "../ComposerBodyEditor";
import type { PendingAttachment } from "../composerAttachments";

afterEach(() => {
  cleanup();
});

const imageAtt: PendingAttachment = {
  id: "a1",
  key: "dropped:shot.png:1",
  name: "shot.png",
  path: "shot.png",
  text: "",
  truncated: false,
  kind: "file",
  binary: true,
};

const pdfAtt: PendingAttachment = {
  id: "a2",
  key: "dropped:brief.pdf:1",
  name: "brief.pdf",
  path: "brief.pdf",
  text: "",
  truncated: false,
  kind: "file",
};

describe("ComposerBodyEditor image tokens", () => {
  it("does not draw an inline pill for image attachments", () => {
    render(
      <ComposerBodyEditor
        value={`看${inlineToken("A", 0)}`}
        attachments={[imageAtt]}
        agentMentions={[]}
        maxLength={32_000}
        onChange={vi.fn()}
        onReconcile={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onRemoveAgent={vi.fn()}
        onCaret={vi.fn()}
        onKeyDown={vi.fn()}
        onPaste={vi.fn()}
      />,
    );
    expect(screen.getByTestId("composer-body").textContent).toContain("看");
    expect(screen.getByTestId("composer-body").textContent).not.toContain(
      "shot.png",
    );
    expect(screen.queryByRole("button", { name: "移除附件" })).toBeNull();
  });

  it("still draws a filename pill for non-image files", () => {
    render(
      <ComposerBodyEditor
        value={inlineToken("A", 0)}
        attachments={[pdfAtt]}
        agentMentions={[]}
        maxLength={32_000}
        onChange={vi.fn()}
        onReconcile={vi.fn()}
        onRemoveAttachment={vi.fn()}
        onRemoveAgent={vi.fn()}
        onCaret={vi.fn()}
        onKeyDown={vi.fn()}
        onPaste={vi.fn()}
      />,
    );
    expect(screen.getByText("brief.pdf")).toBeTruthy();
    expect(screen.getByRole("button", { name: "移除附件" })).toBeTruthy();
  });
});
