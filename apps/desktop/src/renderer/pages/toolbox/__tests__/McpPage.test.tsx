import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { McpPage } from "@/pages/toolbox/mcp/McpPage";
import type { McpServerListItem } from "@shared/mcp-contract";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const FS: McpServerListItem = {
  id: "fs",
  name: "Filesystem",
  enabled: true,
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-filesystem"],
  runtimeStatus: "ready",
};

const GH: McpServerListItem = {
  id: "gh",
  name: "GitHub",
  enabled: true,
  command: "npx",
  args: [],
  runtimeStatus: "failed",
  runtimeError: "GITHUB_TOKEN 未配置",
};

function stubApi(
  listServers: () => Promise<
    | { ok: true; servers: McpServerListItem[] }
    | { ok: false; error: { kind: string; detail: string } }
  >,
) {
  vi.stubGlobal("mcpApi", {
    runOp: vi.fn(),
    listServers: vi.fn(listServers),
    upsertServer: vi.fn(),
    removeServer: vi.fn(),
    setServerEnabled: vi.fn(),
    testServer: vi.fn(),
  });
}

function renderPage(path: string = APP_PATHS.toolbox.mcp) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <McpPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("MCP 栏", () => {
  it("没有本机通道时不假装已接上", () => {
    renderPage();
    expect(screen.queryByRole("navigation", { name: "工具箱" })).toBeNull();
    expect(screen.getByText("MCP 只在桌面本机可用。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "添加 MCP" })).toBeNull();
  });

  it("列出 Server，不铺报出的动作", async () => {
    stubApi(async () => ({ ok: true, servers: [FS, GH] }));
    renderPage();
    expect(screen.queryByRole("navigation", { name: "工具箱" })).toBeNull();
    const list = await screen.findByTestId("mcp-list");
    expect(
      within(list).getByRole("button", { name: "Filesystem" }),
    ).toBeTruthy();
    expect(within(list).getByText("GitHub")).toBeTruthy();
    expect(within(list).getByText("已握手")).toBeTruthy();
    expect(within(list).getByText("失败")).toBeTruthy();
    expect(within(list).getByText("GITHUB_TOKEN 未配置")).toBeTruthy();
    expect(screen.queryByText("mcp_fs_read_file")).toBeNull();
    expect(screen.queryByText("npx")).toBeNull();
    expect(within(list).queryByRole("button", { name: "添加 MCP" })).toBeNull();
    fireEvent.click(within(list).getByRole("button", { name: "Filesystem" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "编辑 MCP" }),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("button", { name: "测试握手" }),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    fireEvent.click(await screen.findByRole("button", { name: "GitHub" }));
    const badges = screen.getAllByText("失败");
    expect(badges.some((node) => node.className.includes("destructive"))).toBe(
      true,
    );
  });

  it("点添加 MCP 打开新建表单", async () => {
    stubApi(async () => ({ ok: true, servers: [] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "添加 MCP" }));
    expect(screen.getByRole("heading", { name: "新建 MCP" })).toBeTruthy();
  });

  it("节标题与添加 MCP 同一行", async () => {
    stubApi(async () => ({ ok: true, servers: [FS] }));
    renderPage();
    const add = await screen.findByRole("button", { name: "添加 MCP" });
    const title = screen.getByRole("heading", { name: "MCP" });
    expect(title.parentElement).toBe(add.parentElement?.parentElement);
  });

  it("listServers 失败时诚实说明", async () => {
    stubApi(async () => ({
      ok: false,
      error: { kind: "io", detail: "读配置失败" },
    }));
    renderPage();
    expect(await screen.findByText("读配置失败")).toBeTruthy();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("读配置失败");
    expect(alert.className).toContain("text-muted-foreground");
    expect(alert.className).not.toContain("destructive");
    expect(screen.queryByTestId("mcp-list")).toBeNull();
  });
});
