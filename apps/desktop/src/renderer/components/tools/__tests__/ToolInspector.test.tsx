import type { CapabilityTool } from "@/services/capabilities";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ToolInspector, toolFaceSource } from "../ToolInspector";

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

describe("ToolInspector", () => {
  it("第一面是说明书：干什么、谁能用、要不要批、要填什么", () => {
    render(<ToolInspector tool={tool} />);
    expect(screen.getByRole("heading", { name: "web_search" })).toBeTruthy();
    expect(screen.getByText("开场即用")).toBeTruthy();
    expect(screen.getByText(/全员/)).toBeTruthy();
    expect(screen.getByText(/自动执行/)).toBeTruthy();
    expect(screen.getByText("检索词")).toBeTruthy();
    expect(screen.getByText("query")).toBeTruthy();
    expect(screen.getByText("要填")).toBeTruthy();
    expect(screen.getByText("max_results")).toBeTruthy();
    expect(screen.queryByTestId("tool-face-source")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("源码面仍是模型看见的 schema", () => {
    render(<ToolInspector tool={tool} view="source" />);
    const source = screen.getByTestId("tool-face-source").textContent ?? "";
    expect(source).toBe(toolFaceSource(tool));
    expect(source).toContain('"name": "web_search"');
    expect(source).toContain("检索词");
    expect(screen.queryByTestId("tool-face-guide")).toBeNull();
  });

  it("空 properties 说明没有要填的参数", () => {
    render(
      <ToolInspector
        tool={{ ...tool, parameters: { type: "object", properties: {} } }}
      />,
    );
    expect(screen.getByText("没有要填的参数")).toBeTruthy();
    expect(screen.queryByTestId("tool-face-source")).toBeNull();
  });

  it("shows a capability hint", () => {
    render(<ToolInspector tool={tool} capabilityHint="未确认工具调用" />);
    expect(screen.getByText("未确认工具调用")).toBeTruthy();
  });
});
