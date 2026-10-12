// @vitest-environment jsdom
/**
 * 装配页不放页内搜索。工具、信封、交代一直在；市场链留在名字行右侧。
 */

import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
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

vi.mock("@/pages/toolbox/AssemblyOverview", () => ({
  AssemblyOverview: ({
    renderNames,
  }: {
    renderNames?: (names: ReactNode, actions?: ReactNode) => ReactNode;
  }) => {
    const names = <div data-testid="assembly-overview" />;
    const actions = <button type="button">新建一份</button>;
    return renderNames ? renderNames(names, actions) : names;
  },
}));

vi.mock("@/components/tools/EnvelopeSwitchList", () => ({
  EnvelopeSwitchList: () => <div data-testid="envelopes" />,
}));

vi.mock("@/pages/toolbox/mcp/McpPage", () => ({
  McpRoute: () => null,
}));

vi.mock("@/components/tools/ToolSwitchList", () => ({
  ToolSwitchList: ({ heading }: { heading?: string }) =>
    heading ? <h2>{heading}</h2> : null,
}));

vi.mock("@/pages/toolbox/GuidelinesPage", () => ({
  GuidelinesPage: () => <div data-testid="prompts-body" />,
}));

import { AssemblyShelfPage } from "@/pages/toolbox/AssemblyShelf";

afterEach(cleanup);

describe("AssemblyShelf", () => {
  it("没有页内搜索，工具、信封和交代都在", () => {
    render(
      <MemoryRouter>
        <AssemblyShelfPage />
      </MemoryRouter>,
    );
    expect(
      screen.queryByRole("textbox", { name: "搜提示词、工具" }),
    ).toBeNull();
    expect(screen.queryByRole("textbox", { name: "搜提示词" })).toBeNull();
    expect(screen.getByRole("heading", { name: "工具" })).toBeTruthy();
    expect(screen.getByTestId("envelopes")).toBeTruthy();
    expect(screen.getByTestId("prompts-body")).toBeTruthy();
    const market = screen.getByRole("link", { name: "市场" });
    const create = screen.getByRole("button", { name: "新建一份" });
    expect(
      create.compareDocumentPosition(market) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
