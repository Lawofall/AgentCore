// @vitest-environment jsdom
/**
 * 输入区「统筹」：没这场写草稿；有这场 PATCH 这场。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  __resetComposerDelegateModeForTests,
  clearComposerDraftDelegateMode,
  getComposerDraftDelegateMode,
  setComposerDraftDelegateMode,
} from "@/lib/composerDelegateMode";
import { useConversationStore } from "@/stores/conversation";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const patch = vi.hoisted(() => vi.fn());
const setMode = vi.hoisted(() => vi.fn());
const conversations = vi.hoisted(() => ({
  rows: [] as { id: string; delegateMode?: boolean }[],
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => conversations.rows,
  patchConversationCache: (...args: unknown[]) => patch(...args),
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));
vi.mock("@/services/conversations", () => ({
  setConversationDelegateMode: (...args: unknown[]) => setMode(...args),
}));

import { ComposerDelegateMode } from "../ComposerDelegateMode";

function renderChip() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ComposerDelegateMode />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  conversations.rows = [];
  patch.mockReset();
  patch.mockImplementation((id: string, next: { delegateMode?: boolean }) => {
    conversations.rows = conversations.rows.map((row) =>
      row.id === id ? { ...row, ...next } : row,
    );
  });
  setMode.mockReset();
  setMode.mockResolvedValue({ id: "c1", delegateMode: true });
  __resetComposerDelegateModeForTests();
  useConversationStore.setState({ currentConversationId: null });
});

afterEach(cleanup);

describe("ComposerDelegateMode", () => {
  it("没这场时拨开关只写草稿", () => {
    renderChip();
    const btn = screen.getByRole("switch", { name: "统筹" });
    expect(btn.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(btn);
    expect(getComposerDraftDelegateMode()).toBe(true);
    expect(
      screen.getByRole("switch", { name: "统筹" }).getAttribute("aria-checked"),
    ).toBe("true");
    expect(setMode).not.toHaveBeenCalled();
    expect(patch).not.toHaveBeenCalled();
  });

  it("极简时灰掉且点不动", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ComposerDelegateMode chatOnly />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const btn = screen.getByRole("switch", { name: "统筹" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("aria-checked")).toBe("false");
    expect(btn.textContent).not.toContain("关");
    fireEvent.click(btn);
    expect(getComposerDraftDelegateMode()).toBe(false);
    expect(setMode).not.toHaveBeenCalled();
  });

  it("关着写出「· 关」，不铺底", () => {
    renderChip();
    const btn = screen.getByRole("switch", { name: "统筹" });
    expect(btn.getAttribute("aria-checked")).toBe("false");
    const tokens = btn.className.split(/\s+/);
    expect(tokens).not.toContain("bg-muted");
    expect(tokens).not.toContain("bg-accent");
    expect(btn.textContent).toContain("· 关");
  });

  it("开着时用小点标记，不铺底", () => {
    setComposerDraftDelegateMode(true);
    renderChip();
    const btn = screen.getByRole("switch", { name: "统筹" });
    expect(btn.getAttribute("aria-checked")).toBe("true");
    const tokens = btn.className.split(/\s+/);
    expect(tokens).not.toContain("bg-muted");
    expect(tokens).not.toContain("bg-accent");
    expect(btn.textContent).not.toContain("关");
    const dot = btn.querySelector("span.rounded-full");
    expect(dot).not.toBeNull();
    expect(dot?.className.split(/\s+/)).toContain("bg-success");
  });

  it("已有这场时拨开关写这场", async () => {
    conversations.rows = [{ id: "c1", delegateMode: false }];
    useConversationStore.setState({ currentConversationId: "c1" });
    renderChip();
    fireEvent.click(screen.getByRole("switch", { name: "统筹" }));
    expect(patch).toHaveBeenCalledWith("c1", { delegateMode: true });
    await waitFor(() => {
      expect(setMode).toHaveBeenCalledWith("c1", true);
    });
    expect(
      screen.getByRole("switch", { name: "统筹" }).getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("这场拨开后，空草稿跟上次", async () => {
    conversations.rows = [{ id: "c1", delegateMode: false }];
    useConversationStore.setState({ currentConversationId: "c1" });
    renderChip();
    fireEvent.click(screen.getByRole("switch", { name: "统筹" }));
    await waitFor(() => {
      expect(setMode).toHaveBeenCalledWith("c1", true);
    });
    act(() => {
      useConversationStore.setState({ currentConversationId: null });
    });
    expect(getComposerDraftDelegateMode()).toBe(true);
    expect(
      screen.getByRole("switch", { name: "统筹" }).getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("刷新空草稿仍跟上次选用", () => {
    setComposerDraftDelegateMode(true);
    clearComposerDraftDelegateMode();
    renderChip();
    expect(
      screen.getByRole("switch", { name: "统筹" }).getAttribute("aria-checked"),
    ).toBe("true");
  });
});
