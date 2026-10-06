// @vitest-environment jsdom
import { ConversationSplitView } from "@/components/chat/ConversationSplitView";
import { useConversationSplitStore } from "@/stores/conversationSplit";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/chat/ChatView", () => ({
  ChatView: () => <div data-testid="split-chat" />,
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [
    { id: "left", title: "左场" },
    { id: "right", title: "右场" },
  ],
}));

vi.mock("@/services/messages", () => ({
  loadLatestWindow: () => new Promise(() => undefined),
}));

afterEach(() => {
  cleanup();
  useConversationSplitStore.setState({ split: null, roomFits: true });
});

function renderSplit(showDockToggle = true) {
  useConversationSplitStore.setState({
    split: { panes: ["left", "right"], focus: 0, ratio: 0.5 },
    roomFits: true,
  });
  return render(
    <MemoryRouter>
      <ConversationSplitView
        routeHydratePhase="loading"
        onRouteHydrateRetry={() => undefined}
        showDockToggle={showDockToggle}
      />
    </MemoryRouter>,
  );
}

describe("ConversationSplitView", () => {
  it("locks each pane so the header stays above the body", () => {
    renderSplit();

    const panes = document.querySelectorAll("[data-conversation-pane]");
    expect(panes).toHaveLength(2);
    for (const pane of panes) {
      expect(pane.className).toContain("min-h-0");
      expect(pane.className).toContain("overflow-hidden");
      expect(pane.className).toContain("flex-col");
      expect(pane.parentElement?.className).toContain("flex-col");
      expect(pane.parentElement?.className).toContain("overflow-hidden");
      const body = pane.querySelector("[data-pane-body]");
      expect(body?.className).toContain("min-h-0");
      expect(body?.className).toContain("flex-1");
      expect(body?.className).toContain("overflow-hidden");
      expect(body?.querySelector("[data-testid=split-chat]")).toBeTruthy();
      expect(
        pane.querySelector("[data-pane-header]")?.textContent,
      ).toBeTruthy();
    }

    expect(screen.getByLabelText("左场").textContent).toContain("左场");
    expect(screen.getByLabelText("右场").textContent).toContain("右场");
  });

  it("keeps close buttons outside the loading cover", () => {
    renderSplit();
    const closes = screen.getAllByRole("button", { name: "关闭这一栏" });
    expect(closes).toHaveLength(2);
    for (const button of closes) {
      expect(button.className).toContain("bg-card");
      expect(button.closest("[data-pane-header]")).toBeTruthy();
      expect(button.closest("[data-pane-body]")).toBeNull();
    }
    for (const overlay of screen.getAllByLabelText("正在加载对话")) {
      expect(overlay.closest("[data-pane-body]")).toBeTruthy();
      expect(overlay.closest("[data-pane-header]")).toBeNull();
    }
  });

  it("puts the dock toggle in the right header, beside close", () => {
    renderSplit(true);
    const right = document.querySelector('[data-conversation-pane="1"]');
    const left = document.querySelector('[data-conversation-pane="0"]');
    expect(
      right?.querySelector("[data-pane-header]")?.textContent,
    ).toBeTruthy();
    expect(
      right
        ?.querySelector("[data-pane-header]")
        ?.querySelector('[aria-label="侧面板"]'),
    ).toBeTruthy();
    expect(
      left
        ?.querySelector("[data-pane-header]")
        ?.querySelector('[aria-label="侧面板"]'),
    ).toBeNull();
  });

  it("closes a pane from its header", () => {
    renderSplit(false);
    fireEvent.click(screen.getAllByRole("button", { name: "关闭这一栏" })[1]);
    expect(useConversationSplitStore.getState().split).toBeNull();
  });
});
