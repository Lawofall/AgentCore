// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ClipboardEvent as ReactClipboardEvent } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ComposerBodyEditor } from "../ComposerBodyEditor";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderEditor(
  over: {
    value?: string;
    maxLength?: number;
    onPaste?: (e: ReactClipboardEvent) => void;
  } = {},
) {
  const onPaste = over.onPaste ?? vi.fn();
  render(
    <ComposerBodyEditor
      value={over.value ?? ""}
      attachments={[]}
      agentMentions={[]}
      maxLength={over.maxLength ?? 32_000}
      onChange={vi.fn()}
      onReconcile={vi.fn()}
      onRemoveAttachment={vi.fn()}
      onRemoveAgent={vi.fn()}
      onCaret={vi.fn()}
      onKeyDown={vi.fn()}
      onPaste={onPaste}
    />,
  );
  return { onPaste, body: screen.getByTestId("composer-body") };
}

function clipboardData(plain: string, html = "") {
  return {
    getData: (type: string) =>
      type === "text/plain" ? plain : type === "text/html" ? html : "",
    files: [],
    items: [],
  };
}

function stubInsertText() {
  const insert = vi.fn().mockReturnValue(true);
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    writable: true,
    value: insert,
  });
  return insert;
}

describe("ComposerBodyEditor paste", () => {
  it("is a plain-copy island", () => {
    renderEditor();
    expect(
      screen.getByTestId("composer-body").hasAttribute("data-copy-plain"),
    ).toBe(true);
  });

  it("inserts text/plain and does not keep styled HTML", () => {
    const insert = stubInsertText();
    const { body } = renderEditor();
    fireEvent.paste(body, {
      clipboardData: clipboardData(
        "讨论优化",
        '<div style="background:#e8e8e8;border-radius:12px">讨论优化</div>',
      ),
    });
    expect(insert).toHaveBeenCalledWith("insertText", false, "讨论优化");
  });

  it("does not insert text when the drop handler already took files", () => {
    const insert = stubInsertText();
    const { body } = renderEditor({
      onPaste: (e) => e.preventDefault(),
    });
    fireEvent.paste(body, {
      clipboardData: clipboardData("should-not-insert"),
    });
    expect(insert).not.toHaveBeenCalled();
  });

  it("clips paste to maxLength", () => {
    const insert = stubInsertText();
    const { body } = renderEditor({ value: "abc", maxLength: 5 });
    fireEvent.paste(body, {
      clipboardData: clipboardData("hello"),
    });
    expect(insert).toHaveBeenCalledWith("insertText", false, "he");
  });
});
