// @vitest-environment jsdom
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { McpApi, McpConfigResult } from "@shared/mcp-contract";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectorsPage } from "../ConnectorsPage";

function stubMcpApi(): McpApi {
  const listServers = vi.fn(
    async (): Promise<McpConfigResult> => ({ ok: true, servers: [] }),
  );
  const api = {
    runOp: vi.fn(),
    listServers,
    upsertServer: vi.fn(),
    removeServer: vi.fn(),
    setServerEnabled: vi.fn(),
    testServer: vi.fn(),
  } as unknown as McpApi;
  vi.stubGlobal("mcpApi", api);
  return api;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.connectors]}>
      <ConnectorsPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("连接器 · 图鉴卡", () => {
  it("空列表是添加卡，不是空态插画或页头", async () => {
    const api = stubMcpApi();
    renderPage();
    await waitFor(() => expect(api.listServers).toHaveBeenCalled());

    expect(screen.getByRole("button", { name: "添加连接器" })).toBeTruthy();
    expect(screen.queryByText("还没有连接器")).toBeNull();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("link", { name: "工具箱" })).toBeNull();
  });

  it("页头与内容区都无说明书", async () => {
    const api = stubMcpApi();
    renderPage();
    await waitFor(() => expect(api.listServers).toHaveBeenCalled());
    expect(screen.queryByText(/配置本机 stdio MCP Server/)).toBeNull();
    expect(screen.queryByText(/stdio 命令/)).toBeNull();
  });

  it("无 mcpApi 时不渲染", () => {
    renderPage();
    expect(screen.queryByRole("button", { name: "添加连接器" })).toBeNull();
    expect(screen.queryByText(/本机 MCP 仅桌面端可用/)).toBeNull();
  });
});

describe("连接器 · 可恢复失败", () => {
  it("列表失败 role=alert 走 muted，不涂 destructive", async () => {
    const api = stubMcpApi();
    vi.mocked(api.listServers).mockResolvedValue({
      ok: false,
      error: { kind: "io", detail: "读配置失败" },
    });
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("读配置失败");
    expect(alert.className).toContain("text-muted-foreground");
    expect(alert.className).not.toContain("destructive");
  });

  it("keeps the MCP runtime 失败 Badge destructive", async () => {
    const api = stubMcpApi();
    vi.mocked(api.listServers).mockResolvedValue({
      ok: true,
      servers: [
        {
          id: "s1",
          name: "Filesystem",
          enabled: true,
          command: "npx",
          args: [],
          runtimeStatus: "failed",
          runtimeError: "spawn failed",
        },
      ],
    });
    renderPage();

    const badge = await screen.findByText("失败");
    expect(badge.className).toContain("destructive");
    expect(screen.getByText("spawn failed")).toBeTruthy();
  });

  it("点插头卡打开编辑对话框", async () => {
    const api = stubMcpApi();
    vi.mocked(api.listServers).mockResolvedValue({
      ok: true,
      servers: [
        {
          id: "s1",
          name: "Filesystem",
          enabled: true,
          command: "npx",
          args: ["-y", "fs"],
          runtimeStatus: "ready",
        },
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Filesystem" }));
    expect(screen.getByRole("heading", { name: "编辑连接器" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "测试握手" })).toBeTruthy();
  });
});
