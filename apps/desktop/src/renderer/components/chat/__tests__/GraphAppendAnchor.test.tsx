// @vitest-environment jsdom
import { useConversationStore } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { GraphAppendAnchor } from "../GraphAppendAnchor";

const CID = "c-gappend-anchor";

describe("GraphAppendAnchor", () => {
  beforeEach(() => {
    useConversationStore.setState({ currentConversationId: null, byId: {} });
    const store = useConversationStore.getState();
    store.switchConversation(CID);
    store.addMessage({
      id: "client-m1",
      serverMessageId: "m1",
      role: "assistant",
      content: "host",
      createdAt: new Date().toISOString(),
      executionId: "exec1",
      isStreaming: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders 新开一队 copy and focuses the previous graph by hostMessageId", () => {
    render(<GraphAppendAnchor hostMessageId="m1" />);
    expect(screen.getByTestId("graph-append-anchor").textContent).toContain(
      "新开一队、接着上一张继续",
    );
    fireEvent.click(screen.getByTestId("graph-append-anchor"));
    expect(useConversationStore.getState().byId[CID].messageFocus?.id).toBe(
      "client-m1",
    );
  });

  it("navigates by prevExecutionId to the prior graph bubble", () => {
    render(<GraphAppendAnchor prevExecutionId="exec1" />);
    expect(screen.getByTestId("graph-append-anchor").textContent).toContain(
      "新开一队、接着上一张继续",
    );
    fireEvent.click(screen.getByTestId("graph-append-anchor"));
    expect(useConversationStore.getState().byId[CID].messageFocus?.id).toBe(
      "client-m1",
    );
  });

  it("uses the same 新开一队 copy for a debate graph", () => {
    render(<GraphAppendAnchor prevExecutionId="exec1" actKind="debate" />);
    expect(screen.getByTestId("graph-append-anchor").textContent).toContain(
      "新开一队、接着上一张继续",
    );
    expect(screen.getByTestId("graph-append-anchor").textContent).not.toContain(
      "追加",
    );
  });

  it("appends authorizedBy subtitle for stage_card / auto / preview", () => {
    render(
      <GraphAppendAnchor
        prevExecutionId="exec1"
        actKind="debate"
        authorizedBy="stage_card"
      />,
    );
    expect(screen.getByTestId("graph-append-anchor").textContent).toContain(
      "经推进卡授权",
    );
  });

  it("explains when the prior graph is outside the loaded window", () => {
    useConversationStore.getState().prependMessages([], true, CID);
    render(<GraphAppendAnchor prevExecutionId="exec-older" />);
    const anchor = screen.getByTestId("graph-append-anchor");
    expect(anchor.getAttribute("data-unavailable")).toBe("true");
    expect(anchor.tagName).not.toBe("BUTTON");
    expect(anchor.textContent).toContain("新开一队、接着上一张继续");
    expect(anchor.textContent).toContain(
      "上一张图不在当前消息窗，往上翻可查看",
    );
    fireEvent.click(anchor);
    expect(useConversationStore.getState().byId[CID].messageFocus).toBeNull();
  });

  it("explains when the prior graph is not in this conversation", () => {
    render(<GraphAppendAnchor hostMessageId="missing-host" />);
    const anchor = screen.getByTestId("graph-append-anchor");
    expect(anchor.getAttribute("data-unavailable")).toBe("true");
    expect(anchor.textContent).toContain("上一张图不在当前对话");
    fireEvent.click(anchor);
    expect(useConversationStore.getState().byId[CID].messageFocus).toBeNull();
  });
});
