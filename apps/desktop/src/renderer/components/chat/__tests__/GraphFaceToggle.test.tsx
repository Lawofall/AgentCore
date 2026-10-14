// @vitest-environment jsdom
import { GraphFaceToggle } from "@/components/chat/GraphFaceToggle";
import { ChatPaneProvider } from "@/lib/chatPane";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import type { Message } from "@/stores/conversation/types";
import { useGraphFaceStore } from "@/stores/graphFace";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

const MSG: Message = {
  id: "m1",
  role: "user",
  content: "hi",
  createdAt: "2026-01-01T00:00:00Z",
  executionId: null,
  isStreaming: false,
};

function seed(id: string) {
  useConversationStore.setState({
    currentConversationId: id,
    byId: { [id]: { ...EMPTY_RUNTIME, messages: [MSG] } },
  });
}

afterEach(() => {
  cleanup();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useGraphFaceStore.setState({ byKey: {} });
});

describe("GraphFaceToggle", () => {
  it("没有消息时不占位", () => {
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    render(
      <ChatPaneProvider id="c1">
        <GraphFaceToggle />
      </ChatPaneProvider>,
    );
    expect(screen.queryByRole("button", { name: "图" })).toBeNull();
  });

  it("点「图」整列切到画布，再点回到正文", () => {
    seed("c1");
    render(
      <ChatPaneProvider id="c1">
        <GraphFaceToggle />
      </ChatPaneProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "图" }));
    expect(useGraphFaceStore.getState().byKey.c1).toBe("graph");
    const back = screen.getByRole("button", { name: "正文" });
    expect(back.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(back);
    expect(useGraphFaceStore.getState().byKey.c1).toBe("text");
    expect(
      screen.getByRole("button", { name: "图" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("换一场回到正文，另一栏互不影响", () => {
    seed("c1");
    useConversationStore.setState({
      byId: {
        c1: { ...EMPTY_RUNTIME, messages: [MSG] },
        c2: { ...EMPTY_RUNTIME, messages: [MSG] },
      },
    });
    render(
      <>
        <ChatPaneProvider id="c1">
          <GraphFaceToggle />
        </ChatPaneProvider>
        <ChatPaneProvider id="c2">
          <GraphFaceToggle />
        </ChatPaneProvider>
      </>,
    );
    const [first, second] = screen.getAllByRole("button", { name: "图" });
    fireEvent.click(first!);
    expect(useGraphFaceStore.getState().byKey.c1).toBe("graph");
    expect(useGraphFaceStore.getState().byKey.c2).toBeUndefined();
    act(() => {
      useGraphFaceStore.getState().reset("c1");
    });
    expect(useGraphFaceStore.getState().byKey.c1).toBe("text");
    expect(second).toBeTruthy();
  });
});
