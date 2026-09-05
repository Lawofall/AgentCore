// @vitest-environment jsdom
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { McpApi, McpConfigResult } from "@shared/mcp-contract";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

describe("连接器页 · 统一页头", () => {
  it("主 CTA 进页头动作位，返回工具箱并挂本页标题", async () => {
    const api = stubMcpApi();
    const { container } = renderPage();
    await waitFor(() => expect(api.listServers).toHaveBeenCalled());

    const header = container.querySelector("header");
    expect(screen.getAllByRole("link", { name: "工具箱" })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 1, name: "连接器" }),
    ).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "工具箱能力" })).toBeNull();
    expect(
      header?.contains(screen.getByRole("button", { name: "添加 Server" })),
    ).toBe(true);
  });

  it("页头与内容区都无说明书", async () => {
    const api = stubMcpApi();
    renderPage();
    await waitFor(() => expect(api.listServers).toHaveBeenCalled());
    expect(screen.queryByText(/配置本机 stdio MCP Server/)).toBeNull();
  });

  it("无 mcpApi 的降级分支只留页头那一份返回链接", () => {
    renderPage();

    expect(screen.getAllByRole("link", { name: "工具箱" })).toHaveLength(1);
    expect(
      screen.getByRole("heading", { level: 1, name: "连接器" }),
    ).toBeTruthy();
    expect(screen.getByText(/本机 MCP 仅桌面端可用/)).toBeTruthy();
  });
});

describe("连接器页 · 可恢复失败", () => {
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
    expect(screen.getByText("spawn failed").className).toContain(
      "text-destructive",
    );
  });
});
