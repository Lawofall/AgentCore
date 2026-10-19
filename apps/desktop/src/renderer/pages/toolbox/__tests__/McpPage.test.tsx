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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/mcpServers", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/mcpServers")>();
  return {
    ...actual,
    listMcpServers: vi.fn(),
    createMcpServer: vi.fn(),
    updateMcpServer: vi.fn(),
    deleteMcpServer: vi.fn(),
    testMcpServer: vi.fn(),
    startMcpOAuth: vi.fn(),
  };
});

import { createMcpServer, listMcpServers } from "@/services/mcpServers";

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

beforeEach(() => {
  vi.mocked(listMcpServers).mockResolvedValue({ data: [], total: 0 });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("MCP 栏", () => {
  it("没有本机通道时仍可接远程插头", async () => {
    renderPage();
    expect(screen.queryByRole("navigation", { name: "工具箱" })).toBeNull();
    expect(await screen.findByText("还没有 MCP。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "添加 MCP" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "从 JSON 导入" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "添加 MCP" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "新建 MCP" }),
    ).toBeTruthy();
    expect(within(dialog).getByLabelText("地址")).toBeTruthy();
    expect(within(dialog).queryByLabelText("命令")).toBeNull();
  });

  it("列出账号远程插头并标需登录", async () => {
    vi.mocked(listMcpServers).mockResolvedValue({
      data: [
        {
          id: "linear",
          name: "Linear",
          url: "https://mcp.linear.app/mcp",
          auth_kind: "oauth",
          status: "needs_auth",
          has_token: false,
          last_error: "",
          created_at: "2026-01-01T00:00:00.000Z",
          updated_at: "2026-01-01T00:00:00.000Z",
        },
      ],
      total: 1,
    });
    renderPage();
    const list = await screen.findByTestId("mcp-list");
    expect(within(list).getByRole("button", { name: "Linear" })).toBeTruthy();
    expect(within(list).getByText("需登录")).toBeTruthy();
    fireEvent.click(within(list).getByRole("button", { name: "Linear" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("地址")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "登录" })).toBeTruthy();
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
    expect(screen.getByRole("tablist", { name: "接线" })).toBeTruthy();
    expect(screen.getByLabelText("命令")).toBeTruthy();
  });

  it("节标题与添加 MCP 同一行", async () => {
    stubApi(async () => ({ ok: true, servers: [FS] }));
    renderPage();
    const add = await screen.findByRole("button", { name: "添加 MCP" });
    const title = screen.getByRole("heading", { name: "MCP" });
    expect(title.parentElement).toBe(add.parentElement?.parentElement);
  });

  it("从 JSON 导入一条远程插头填进表", async () => {
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "从 JSON 导入" }),
    );
    const importer = await screen.findByTestId("mcp-import-dialog");
    fireEvent.change(within(importer).getByLabelText("MCP JSON"), {
      target: {
        value: '{"mcpServers":{"linear":{"url":"https://mcp.linear.app/mcp"}}}',
      },
    });
    fireEvent.click(within(importer).getByRole("button", { name: "导入" }));
    const dialog = await screen.findByTestId("mcp-dialog");
    expect(
      within(dialog).getByRole("heading", { name: "新建 MCP" }),
    ).toBeTruthy();
    expect(
      (within(dialog).getByLabelText("地址") as HTMLInputElement).value,
    ).toBe("https://mcp.linear.app/mcp");
    expect(createMcpServer).not.toHaveBeenCalled();
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
