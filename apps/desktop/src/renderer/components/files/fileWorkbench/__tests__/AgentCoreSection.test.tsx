// @vitest-environment jsdom
/**
 * AgentCoreSection 只服务文件夹层：``.agentcore`` 标题 +「新建条目」。
 * 账号提示词不在文件页；折叠时也能建。
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

function renderSection(onOpenEntry: () => void = () => undefined) {
  return render(
    <TooltipProvider>
      <AgentCoreSection
        scope={{ kind: "folder", folderId: "F1" }}
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
  it("文件夹标题是 .agentcore，不挂账号提示词入口", () => {
    renderSection();
    expect(screen.getByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("记忆")).toBeNull();
    expect(screen.queryByText("本文件夹设定")).toBeNull();
    expect(screen.queryByText("最近更新")).toBeNull();
    expect(screen.queryByText("所有对话共用的提示词在工具箱")).toBeNull();
  });
});

describe("AgentCoreSection 新建条目", () => {
  it("「新建条目」与「.agentcore」同一 header 行，始终可见", () => {
    renderSection();
    const title = screen.getByText(".agentcore");
    const create = screen.getByRole("button", { name: "新建条目" });
    expect(title.closest("button")?.contains(create)).toBe(false);
    expect(create.parentElement?.contains(title)).toBe(true);
    expect(create.parentElement?.className).not.toMatch(/group-hover/);
  });

  it("点新建不折叠已展开的标题", async () => {
    renderSection();
    fireEvent.click(screen.getByText(".agentcore"));
    expect(screen.getByTestId("entries")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(screen.getByTestId("entries")).toBeTruthy();
    expect(
      screen
        .getByText(".agentcore")
        .closest("button")
        ?.getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("折叠时新建仍可见；建完展开列表", async () => {
    const onOpen = vi.fn();
    renderSection(onOpen);
    expect(screen.queryByTestId("entries")).toBeNull();
    expect(screen.getByRole("button", { name: "新建条目" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(createAndOpenScopeEntry).toHaveBeenCalledWith(
      { kind: "folder", folderId: "F1" },
      onOpen,
    );
    expect(screen.getByTestId("entries")).toBeTruthy();
  });

  it("新建失败时保持折叠", async () => {
    createAndOpenScopeEntry.mockResolvedValueOnce(false);
    renderSection();
    expect(screen.queryByTestId("entries")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "新建条目" }));
    await waitFor(() => expect(createAndOpenScopeEntry).toHaveBeenCalled());
    expect(screen.queryByTestId("entries")).toBeNull();
  });
});
