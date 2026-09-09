import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { Capabilities } from "@/services/capabilities";
import type {
  McpApi,
  McpConfigResult,
  McpServerListItem,
} from "@shared/mcp-contract";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToolsPage } from "../ToolsPage";

vi.mock("@/services/capabilities", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/capabilities")>();
  return { ...actual, getCapabilities: vi.fn() };
});

vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({ data: undefined }),
}));

vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({ data: undefined }),
}));

const { getCapabilities } = await import("@/services/capabilities");

const catalog: Capabilities = {
  guidelines: {
    shared_base: "共享准则",
    worker_leaf: "叶子身份",
    worker_captain: "可再委派队员身份",
    ceo_addon: "CEO 附加",
    ceo: "CEO",
  },
  skills: [],
  tools: [
    {
      name: "web_search",
      face: "web",
      resident: true,
      summary: "联网检索",
      description: "联网检索",
      parameters: { type: "object", properties: {} },
      approval: "never",
      available_to: ["ceo", "worker"],
    },
  ],
};

function stubMcpApi(servers: McpServerListItem[] = []): McpApi {
  const listServers = vi.fn(
    async (): Promise<McpConfigResult> => ({ ok: true, servers }),
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

function renderPage(entry = APP_PATHS.toolbox.tools) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ToolsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
  vi.mocked(getCapabilities).mockResolvedValue(catalog);
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("工具页 · 插头卡", () => {
  it("无 mcpApi 时只列内置，不出现添加卡", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(screen.getByRole("heading", { name: /网络/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "添加连接器" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "连接器" })).toBeNull();
    expect(screen.queryByText("MCP")).toBeNull();
  });

  it("插头与出厂工具同款卡；不把插头报出的动作再铺一层", async () => {
    stubMcpApi([
      {
        id: "fs",
        name: "Filesystem",
        enabled: true,
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem"],
        runtimeStatus: "ready",
      },
      {
        id: "gh",
        name: "GitHub",
        enabled: true,
        command: "npx",
        args: [],
        runtimeStatus: "failed",
        runtimeError: "GITHUB_TOKEN 未配置",
      },
    ]);
    renderPage();

    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(
      await screen.findByRole("button", { name: "Filesystem" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "GitHub" })).toBeTruthy();
    expect(screen.getByText("已握手")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("GITHUB_TOKEN 未配置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "添加连接器" })).toBeTruthy();
    expect(screen.queryByText("mcp_fs_read_file")).toBeNull();
    expect(screen.queryByRole("heading", { name: /本机连接器/ })).toBeNull();
  });

  it("listServers 失败时诚实说明，不拆内置目录", async () => {
    const api = stubMcpApi();
    vi.mocked(api.listServers).mockResolvedValue({
      ok: false,
      error: { kind: "io", detail: "读配置失败" },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(await screen.findByText("读配置失败")).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
  });

  it("点添加连接器打开配置对话框，图鉴仍在", async () => {
    stubMcpApi();
    renderPage();
    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: "添加连接器" }));
    expect(screen.getByRole("heading", { name: "新建连接器" })).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
  });
});
