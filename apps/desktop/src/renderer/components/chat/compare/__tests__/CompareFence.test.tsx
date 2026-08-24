// @vitest-environment jsdom

import { CompareFence } from "@/components/chat/compare/CompareFence";
import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import type { FileSource } from "@/lib/fileSource";
import { useNarrowLayout } from "@/lib/useNarrowLayout";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: vi.fn(),
}));
vi.mock("@/lib/useNarrowLayout", () => ({
  useNarrowLayout: vi.fn(() => false),
}));

const read = vi.fn(async (path: string) => ({
  kind: "image" as const,
  dataUrl: `data:image/png;base64,${path}`,
  mime: "image/png",
  size: 10,
}));

const source = {
  id: "workspace:c1",
  read,
} as unknown as FileSource;

const BODY = `A|方案一
site/v1.png
---
B|方案二
site/v2.png`;

describe("CompareFence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(source);
    vi.mocked(useNarrowLayout).mockReturnValue(false);
  });

  it("desktop: renders two columns with both images", async () => {
    render(<CompareFence body={BODY} conversationId="c1" />);
    expect(screen.getByText("方案一")).toBeTruthy();
    expect(screen.getByText("方案二")).toBeTruthy();
    expect(await screen.findByAltText("方案一")).toBeTruthy();
    expect(await screen.findByAltText("方案二")).toBeTruthy();
    expect(read).toHaveBeenCalledWith("site/v1.png");
    expect(read).toHaveBeenCalledWith("site/v2.png");
  });

  it("narrow: stacks panes and switches with tabs", async () => {
    vi.mocked(useNarrowLayout).mockReturnValue(true);
    render(<CompareFence body={BODY} conversationId="c1" />);
    expect(await screen.findByAltText("方案一")).toBeTruthy();
    expect(screen.queryByAltText("方案二")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /B · 方案二/ }));
    expect(await screen.findByAltText("方案二")).toBeTruthy();
    expect(screen.queryByAltText("方案一")).toBeNull();
  });

  it("invalid body falls back to source pre", () => {
    const { container } = render(
      <CompareFence body={"only one\nsite/a.png"} conversationId="c1" />,
    );
    expect(container.querySelector("pre code")?.textContent).toContain(
      "only one",
    );
  });

  it("explicit fileSource bypasses hook-resolved source", async () => {
    const explicitRead = vi.fn(async (path: string) => ({
      kind: "image" as const,
      dataUrl: `data:image/png;base64,explicit-${path}`,
      mime: "image/png",
      size: 10,
    }));
    const explicitSource = {
      id: "workspace:explicit",
      read: explicitRead,
    } as unknown as FileSource;
    vi.mocked(useConversationFileSource).mockReturnValue(null);

    render(
      <CompareFence
        body={BODY}
        conversationId="c1"
        fileSource={explicitSource}
      />,
    );
    expect(await screen.findByAltText("方案一")).toBeTruthy();
    expect(explicitRead).toHaveBeenCalledWith("site/v1.png");
    expect(read).not.toHaveBeenCalled();
  });
});
