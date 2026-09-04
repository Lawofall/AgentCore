// @vitest-environment jsdom
import { TabChip, type TabChipProps } from "@/components/ui";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

function renderChip(props: Partial<TabChipProps> = {}) {
  const { label = "README", ...rest } = props;
  return render(
    <TooltipProvider>
      <TabChip
        label={label}
        onSelect={vi.fn()}
        onClose={vi.fn()}
        onPopOut={vi.fn()}
        {...rest}
      />
    </TooltipProvider>,
  );
}

function classTokens(el: HTMLElement): string[] {
  return el.className.split(/\s+/).filter(Boolean);
}

describe("TabChip overlay chrome", () => {
  it("positions close and pop-out as an absolute overlay", () => {
    renderChip({ active: true });
    const close = screen.getByRole("button", { name: "关闭 README" });
    const popOut = screen.getByRole("button", { name: "弹出 README" });
    expect(classTokens(close)).toContain("absolute");
    expect(classTokens(popOut)).toContain("absolute");
    expect(close.getAttribute("data-no-tab-drag")).toBe("");
    expect(popOut.getAttribute("data-no-tab-drag")).toBe("");
  });

  it("shows close on the active tab without hover", () => {
    renderChip({ active: true });
    const close = screen.getByRole("button", { name: "关闭 README" });
    expect(classTokens(close)).toContain("opacity-100");
    expect(classTokens(close)).not.toContain("opacity-0");
    expect(classTokens(close)).toContain("pointer-events-auto");
    expect(classTokens(close)).not.toContain("pointer-events-none");
  });

  it("does not show pop-out merely because the tab is active", () => {
    renderChip({ active: true });
    const popOut = screen.getByRole("button", { name: "弹出 README" });
    expect(classTokens(popOut)).toContain("opacity-0");
    expect(classTokens(popOut)).toContain("pointer-events-none");
    expect(classTokens(popOut)).not.toContain("opacity-100");
  });

  it("reserves a close gutter on the active tab so the title is not covered", () => {
    const { rerender } = renderChip({ active: true, label: "协作图审计员" });
    const active = screen.getByRole("button", {
      name: /^协作图审计员$/,
    }).parentElement;
    expect(active?.className.split(/\s+/)).toContain("pr-6");

    rerender(
      <TooltipProvider>
        <TabChip
          label="协作图审计员"
          onSelect={vi.fn()}
          onClose={vi.fn()}
          onPopOut={vi.fn()}
          active={false}
        />
      </TooltipProvider>,
    );
    const idle = screen.getByRole("button", {
      name: /^协作图审计员$/,
    }).parentElement;
    expect(idle?.className.split(/\s+/)).not.toContain("pr-6");
  });

  it("reveals close and pop-out on hover and focus-within classes, and keeps them tabbable", () => {
    renderChip({ active: false });
    const close = screen.getByRole("button", { name: "关闭 README" });
    const popOut = screen.getByRole("button", { name: "弹出 README" });
    const select = screen.getByRole("button", { name: "README" });

    expect(classTokens(close)).toContain("opacity-0");
    expect(classTokens(close)).toContain("pointer-events-none");
    expect(close.className).toContain("group-hover/tab:opacity-100");
    expect(close.className).toContain("group-focus-within/tab:opacity-100");
    expect(close.className).toContain("group-hover/tab:pointer-events-auto");
    expect(close.className).toContain(
      "group-focus-within/tab:pointer-events-auto",
    );

    expect(classTokens(popOut)).toContain("opacity-0");
    expect(popOut.className).toContain("group-hover/tab:opacity-100");
    expect(popOut.className).toContain("group-focus-within/tab:opacity-100");

    select.focus();
    expect(document.activeElement).toBe(select);
    close.focus();
    expect(document.activeElement).toBe(close);
    expect(popOut.tabIndex).not.toBe(-1);
  });

  it("strip keeps close as an overlay and middle-click closes", () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <TooltipProvider>
        <TabChip
          variant="strip"
          label="notes.md"
          onSelect={onSelect}
          onClose={onClose}
        />
      </TooltipProvider>,
    );
    const tab = screen.getByRole("tab", { name: "notes.md" });
    const close = screen.getByRole("button", { name: "关闭 notes.md" });
    expect(classTokens(close)).toContain("absolute");

    fireEvent.pointerDown(tab, { button: 1 });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
