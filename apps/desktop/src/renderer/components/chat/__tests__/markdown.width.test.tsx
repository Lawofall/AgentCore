// @vitest-environment jsdom
/**
 * Assistant markdown width: long tokens (URLs / 1000+500+50+…) must wrap
 * inside the reading column — same cap as IM ChatBubble.
 */

import { Markdown } from "@/components/chat/Markdown";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("Markdown width", () => {
  it("caps the body so an unbreakable token cannot blow the chat pane", () => {
    const { container } = render(
      <Markdown content={"1000+500+50+".repeat(40)} />,
    );
    const body = container.querySelector(".markdown-body");
    expect(body?.className).toContain("min-w-0");
    expect(body?.className).toContain("max-w-full");
    expect(body?.className).toContain("[overflow-wrap:anywhere]");
  });

  it("marks a live non-muted answer for the tail caret", () => {
    const { container } = render(<Markdown content="你好" isStreaming />);
    expect(
      container
        .querySelector(".markdown-body")
        ?.hasAttribute("data-stream-caret"),
    ).toBe(true);
  });

  it("does not mark muted thinking or settled markdown", () => {
    const liveThought = render(<Markdown content="推理" isStreaming muted />);
    expect(
      liveThought.container
        .querySelector(".markdown-body")
        ?.hasAttribute("data-stream-caret"),
    ).toBe(false);
    liveThought.unmount();

    const { container } = render(<Markdown content="你好" />);
    expect(
      container
        .querySelector(".markdown-body")
        ?.hasAttribute("data-stream-caret"),
    ).toBe(false);
  });
});
