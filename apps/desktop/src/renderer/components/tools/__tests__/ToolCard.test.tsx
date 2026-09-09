import type { CapabilityTool } from "@/services/capabilities";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ToolCard } from "../ToolCard";

afterEach(cleanup);

const tool: CapabilityTool = {
  name: "web_search",
  face: "web",
  resident: true,
  summary: "联网检索",
  description:
    "联网检索：给出查询词，返回带出处的结果摘要。一次只搜 2–3 个核心词。",
  parameters: {
    type: "object",
    properties: {
      query: { type: "string", description: "检索词" },
      max_results: { type: "integer", description: "结果数量上限" },
    },
    required: ["query"],
  },
  approval: "never",
  available_to: ["ceo", "worker"],
};

describe("ToolCard", () => {
  it("keeps parameters in a details dialog, not a fold", () => {
    render(<ToolCard tool={tool} />);
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByText("联网检索")).toBeTruthy();
    expect(screen.getByText("开场即用")).toBeTruthy();
    expect(screen.getByText("全员")).toBeTruthy();
    expect(screen.getByText("自动执行")).toBeTruthy();
    expect(screen.queryByText(/给出查询词/)).toBeNull();
    expect(screen.queryByText("query")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "web_search" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/给出查询词/)).toBeTruthy();
    expect(within(dialog).getByText("query")).toBeTruthy();
    expect(within(dialog).getByText("检索词")).toBeTruthy();
    expect(within(dialog).getByText("max_results")).toBeTruthy();
  });

  it("shows 该工具无调用参数 in the dialog when the schema has no properties", () => {
    render(
      <ToolCard
        tool={{ ...tool, parameters: { type: "object", properties: {} } }}
      />,
    );
    expect(screen.queryByText("该工具无调用参数")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "web_search" }));
    expect(
      within(screen.getByRole("dialog")).getByText("该工具无调用参数"),
    ).toBeTruthy();
  });

  it("shows a capability hint on the tile", () => {
    render(<ToolCard tool={tool} capabilityHint="未确认工具调用" />);
    expect(screen.getByText("未确认工具调用")).toBeTruthy();
  });

  it("connector source is a subtitle, not an MCP badge", () => {
    render(<ToolCard tool={tool} source="Filesystem" />);
    expect(screen.getByText("Filesystem")).toBeTruthy();
    expect(screen.queryByText("MCP")).toBeNull();
  });
});
