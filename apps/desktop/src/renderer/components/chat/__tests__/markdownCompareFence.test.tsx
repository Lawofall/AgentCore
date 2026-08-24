// @vitest-environment jsdom

import { Markdown } from "@/components/chat/Markdown";
import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import type { FileSource } from "@/lib/fileSource";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: vi.fn(),
}));

const read = vi.fn(async () => ({
  kind: "image" as const,
  dataUrl: "data:image/png;base64,xx",
  mime: "image/png",
  size: 1,
}));

const source = {
  id: "workspace:c1",
  read,
} as unknown as FileSource;

describe("Markdown compare fence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(source);
  });

  it("routes ```compare fences to CompareFence", async () => {
    render(
      <Markdown
        conversationId="c1"
        content={`\`\`\`compare
A|左
a.png
---
B|右
b.png
\`\`\``}
      />,
    );
    expect(screen.getByText("左")).toBeTruthy();
    expect(screen.getByText("右")).toBeTruthy();
    expect(await screen.findByAltText("左")).toBeTruthy();
  });

  it("explicit fileSource is forwarded to CompareFence", async () => {
    const explicitRead = vi.fn(async () => ({
      kind: "image" as const,
      dataUrl: "data:image/png;base64,explicit",
      mime: "image/png",
      size: 1,
    }));
    const explicitSource = {
      id: "workspace:explicit",
      read: explicitRead,
    } as unknown as FileSource;
    vi.mocked(useConversationFileSource).mockReturnValue(null);

    render(
      <Markdown
        conversationId="c1"
        fileSource={explicitSource}
        content={`\`\`\`compare
A|左
a.png
---
B|右
b.png
\`\`\``}
      />,
    );
    expect(await screen.findByAltText("左")).toBeTruthy();
    expect(explicitRead).toHaveBeenCalledWith("a.png");
    expect(read).not.toHaveBeenCalled();
  });
});
