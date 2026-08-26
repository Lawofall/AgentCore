// @vitest-environment jsdom
/**
 * 全局设定标题。「新建条目」与标题同一行；点新建不折叠；折叠时也能建。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createAndOpenScopeEntry = vi.hoisted(() => vi.fn(async () => true));

vi.mock("@/components/files/fileWorkbench/EntriesSection", () => ({
  EntriesSection: () => <div data-testid="entries" />,
}));

vi.mock("@/components/files/fileWorkbench/createScopeEntry", () => ({
  createAndOpenScopeEntry,
}));

import { AgentCoreSection } from "../AgentCoreSection";

function renderSection(
  scope: "global" | "folder",
  onOpenEntry: () => void = () => undefined,
) {
  return render(
    <TooltipProvider>
      <AgentCoreSection
        scope={
          scope === "global"
            ? { kind: "global" }
            : { kind: "folder", folderId: "F1" }
        }
        memoryActivePath={null}
        documentActivePath={null}
        onOpenEntry={onOpenEntry}
        onEntryDeleted={() => undefined}
        onEntryRenamed={() => undefined}
      />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  createAndOpenScopeEntry.mockReset();
  createAndOpenScopeEntry.mockResolvedValue(true);
});

describe("AgentCoreSection 标题", () => {
  it("全局显示「全局设定」；文件夹标题是 .agentcore，不再叫记忆", () => {
    const { unmount } = renderSection("global");
    expect(screen.getByText("全局设定")).toBeTruthy();
    expect(screen.queryByText(".agentcore")).toBeNull();
    expect(screen.queryByText("记忆")).toBeNull();
    expect(screen.queryByText("本文件夹设定")).toBeNull();
    unmount();

    renderSection("folder");
    expect(screen.getByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("记忆")).toBeNull();
    expect(screen.queryByText("本文件夹设定")).toBeNull();
  });
});

describe("AgentCoreSection 新建条目", () => {
  it("「新建条目」与「全局设定」同一 header 行，始终可见", () => {
    renderSection("global");
    const title = screen.getByText("全局设定");
    const create = screen.getByRole("button", { name: "新建条目" });
    expect(title.closest("button")?.contains(create)).toBe(false);
    expect(create.parentElement?.contains(title)).toBe(true);
    expect(create.parentElement?.className).not.toMatch(/group-hover/);
  });

  it("点新建不折叠已展开的标题", async () => {
    renderSection("global");
    expect(screen.getByTestId("entries")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(screen.getByTestId("entries")).toBeTruthy();
    expect(
      screen
        .getByText("全局设定")
        .closest("button")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("折叠时新建仍可见；建完展开列表", async () => {
    const onOpen = vi.fn();
    renderSection("global", onOpen);
    fireEvent.click(screen.getByText("全局设定"));
    expect(screen.queryByTestId("entries")).toBeNull();
    expect(screen.getByRole("button", { name: "新建条目" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(createAndOpenScopeEntry).toHaveBeenCalledWith(
      { kind: "global" },
      onOpen,
    );
    expect(screen.getByTestId("entries")).toBeTruthy();
  });

  it("新建失败时保持折叠", async () => {
    createAndOpenScopeEntry.mockResolvedValueOnce(false);
    renderSection("global");
    fireEvent.click(screen.getByText("全局设定"));
    expect(screen.queryByTestId("entries")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(screen.queryByTestId("entries")).toBeNull();
  });
});
