// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FileBrowser, type FileBrowserSource } from "../FileBrowser";

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access: "t" }),
}));

const STAGE_ENTRIES = [
  { path: "AAA", is_dir: true },
  { path: "AgentCore", is_dir: true },
  { path: "AgentCore/文档", is_dir: true },
  { path: "AgentCore/文档/工作稿", is_dir: true },
  { path: "AgentCore/文档/research", is_dir: true },
  { path: "AgentCore/文档/research/a.md", is_dir: false },
  { path: "AgentCore/文档/research/b.md", is_dir: false },
  { path: "AgentCore/文档/debate", is_dir: true },
  { path: "AgentCore/文档/debate/x.md", is_dir: false },
  { path: "AgentCore/文档/reviews", is_dir: true },
  { path: "AgentCore/文档/已迁入记忆", is_dir: true },
  { path: "合同", is_dir: true },
  { path: "src", is_dir: true },
];

function Harness({
  source,
  initialCwd = "",
}: {
  source: FileBrowserSource;
  initialCwd?: string;
}) {
  const [cwd, setCwd] = useState(initialCwd);
  return (
    <MemoryRouter>
      <FileBrowser source={source} cwd={cwd} onCwdChange={setCwd} />
    </MemoryRouter>
  );
}

function stageSource(): FileBrowserSource {
  return {
    list: async () => ({ entries: STAGE_ENTRIES, truncated: false }),
    download: vi.fn(),
  };
}

describe("FileBrowser .agentcore 抽屉", () => {
  it("根上钉顶呈现名，打开后是四个稿夹，不露文档壳", async () => {
    render(<Harness source={stageSource()} />);

    expect(await screen.findByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("AgentCore")).toBeNull();
    expect(screen.queryByText("文档")).toBeNull();
    expect(screen.queryByText("工作稿")).toBeNull();
    expect(screen.queryByText("已迁入记忆")).toBeNull();

    const names = screen
      .getAllByText(/^(AAA|\.agentcore|合同|src)$/)
      .map((el) => el.textContent);
    expect(names[0]).toBe(".agentcore");

    fireEvent.click(screen.getByText(".agentcore"));

    await waitFor(() => {
      expect(screen.getByText("工作稿")).toBeTruthy();
      expect(screen.getByText("调研约定文档 · 2 件")).toBeTruthy();
      expect(screen.getByText("辩论产物 · 1 件")).toBeTruthy();
    });
    expect(screen.getByText("reviews")).toBeTruthy();
    expect(screen.queryByText("文档")).toBeNull();
    expect(screen.queryByText("已迁入记忆")).toBeNull();
    expect(screen.queryByText(/src.*件/)).toBeNull();

    const crumbs = screen
      .getAllByRole("button")
      .map((b) => b.textContent)
      .filter((t) => t === "根目录" || t === ".agentcore");
    expect(crumbs).toContain(".agentcore");
    expect(screen.queryByRole("button", { name: "AgentCore" })).toBeNull();
    expect(screen.queryByRole("button", { name: "文档" })).toBeNull();
  });

  it("搜索认呈现名 .agentcore", async () => {
    render(<Harness source={stageSource()} />);
    await screen.findByText(".agentcore");

    fireEvent.change(screen.getByPlaceholderText("搜索当前目录"), {
      target: { value: ".agentcore" },
    });
    expect(screen.getByText(".agentcore")).toBeTruthy();
    expect(screen.queryByText("AAA")).toBeNull();
    expect(screen.queryByText("文档")).toBeNull();
  });

  it("进入稿夹后面包屑仍是 .agentcore / research，不出现 文档/", async () => {
    render(<Harness source={stageSource()} />);
    fireEvent.click(await screen.findByText(".agentcore"));
    fireEvent.click(await screen.findByText("research"));

    await waitFor(() => {
      expect(screen.getByText("a.md")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: ".agentcore" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "文档" })).toBeNull();
    expect(screen.queryByRole("button", { name: "AgentCore" })).toBeNull();
    expect(screen.queryByText("已迁入记忆")).toBeNull();
  });

  it("盘上无 AgentCore 时不挂虚拟抽屉、不挂文件夹设定条目", async () => {
    const source: FileBrowserSource = {
      list: async () => ({
        entries: [{ path: "报告.md", is_dir: false }],
        truncated: false,
      }),
      download: vi.fn(),
    };
    render(<Harness source={source} />);
    expect(await screen.findByText("报告.md")).toBeTruthy();
    expect(screen.queryByText(".agentcore")).toBeNull();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("本文件夹设定")).toBeNull();
  });
});
