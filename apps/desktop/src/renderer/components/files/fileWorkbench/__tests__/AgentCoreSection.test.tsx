// @vitest-environment jsdom
/**
 * 全局设定标题。文件夹条目挂在文件树 ``.agentcore`` 行内，不再单独叫「本文件夹设定」。
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/fileWorkbench/EntriesSection", () => ({
  EntriesSection: () => <div data-testid="entries" />,
}));

import { AgentCoreSection } from "../AgentCoreSection";

function renderSection(scope: "global" | "folder") {
  return render(
    <AgentCoreSection
      scope={
        scope === "global"
          ? { kind: "global" }
          : { kind: "folder", folderId: "F1" }
      }
      memoryActivePath={null}
      documentActivePath={null}
      onOpenEntry={() => undefined}
      onEntryDeleted={() => undefined}
      onEntryRenamed={() => undefined}
    />,
  );
}

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
