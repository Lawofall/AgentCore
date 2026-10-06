// @vitest-environment jsdom
/**
 * 页顶搜索没命中时工具区和交代留在树上，换词还能再出现。
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactNode, useEffect } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/pages/toolbox/useEditingAssembly", () => ({
  useEditingAssembly: () => ({
    profile: null,
    conversationId: null,
    conversation: undefined,
    pending: false,
  }),
}));

vi.mock("@/components/tools/useCapabilities", () => ({
  useCapabilities: () => ({
    data: { tools: [] },
    status: "ready",
    reload: () => undefined,
  }),
}));

vi.mock("@/pages/toolbox/AssemblyOverview", () => ({
  AssemblyOverview: ({
    renderNames,
  }: {
    renderNames?: (names: ReactNode) => ReactNode;
  }) => {
    const names = <div data-testid="assembly-overview" />;
    return renderNames ? renderNames(names) : names;
  },
}));

vi.mock("@/components/tools/EnvelopeSwitchList", () => ({
  EnvelopeSwitchList: () => <div data-testid="envelopes" />,
}));

vi.mock("@/pages/toolbox/mcp/McpPage", () => ({
  McpRoute: () => null,
}));

vi.mock("@/components/tools/ToolSwitchList", () => ({
  ToolSwitchList: ({
    query = "",
    heading,
    onMissChange,
  }: {
    query?: string;
    heading?: string;
    onMissChange?: (miss: boolean) => void;
  }) => {
    const miss = query.includes("zzz");
    useEffect(() => {
      onMissChange?.(miss);
    }, [miss, onMissChange]);
    return (
      <>
        {heading ? <h2>{heading}</h2> : null}
        <div data-testid="tools-body">{query}</div>
      </>
    );
  },
}));

vi.mock("@/pages/toolbox/GuidelinesPage", () => ({
  GuidelinesPage: ({
    query = "",
    onMissChange,
  }: {
    query?: string;
    onMissChange?: (miss: boolean) => void;
  }) => {
    const miss = query.includes("zzz");
    useEffect(() => {
      onMissChange?.(miss);
    }, [miss, onMissChange]);
    return <div data-testid="prompts-body">{query}</div>;
  },
}));

import { AssemblyShelfPage } from "@/pages/toolbox/AssemblyShelf";

afterEach(cleanup);

describe("AssemblyShelf search", () => {
  it("没命中之后换一个词，工具区还会出来", async () => {
    render(
      <MemoryRouter>
        <AssemblyShelfPage />
      </MemoryRouter>,
    );
    const search = screen.getByRole("textbox", { name: "搜提示词、工具" });
    fireEvent.change(search, { target: { value: "zzz" } });
    expect(await screen.findByText("没有匹配「zzz」的条目。")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "工具" })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "工具", hidden: true }),
    ).toBeTruthy();

    fireEvent.change(search, { target: { value: "改文件" } });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "工具" })).toBeTruthy();
    });
    expect(screen.queryByText(/没有匹配/)).toBeNull();
    expect(screen.getByTestId("tools-body").textContent).toBe("改文件");
  });
});
