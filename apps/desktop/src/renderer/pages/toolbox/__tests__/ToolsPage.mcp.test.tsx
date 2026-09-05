import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { Capabilities } from "@/services/capabilities";
import { useStandingInboxStore } from "@/stores/standingInbox";
import type { McpApi, McpOpResult } from "@shared/mcp-contract";
// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
      category: "research",
      description: "联网检索",
      parameters: { type: "object", properties: {} },
      approval: "never",
      available_to: ["ceo", "worker"],
    },
  ],
  packs: [],
};

function stubMcpApi(runOp: McpApi["runOp"]): McpApi {
  const api = {
    runOp,
    listServers: vi.fn(),
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
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.tools]}>
      <ToolsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  useStandingInboxStore.setState({ badge: 0 });
  vi.mocked(getCapabilities).mockReset();
  vi.mocked(getCapabilities).mockResolvedValue(catalog);
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("工具页 · MCP 并陈", () => {
  it("无 mcpApi 时只列内置，不假装有本机连接器", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(screen.queryByText("本机连接器")).toBeNull();
    expect(screen.queryByText(/去连接器增删启停/)).toBeNull();
  });

  it("已启用 MCP 与内置同页并陈；失败 Server 不列假工具", async () => {
    stubMcpApi(
      vi.fn(
        async (): Promise<McpOpResult> => ({
          ok: true,
          value: {
            servers: [
              {
                id: "fs",
                name: "Filesystem",
                status: "ready",
                tools: [
                  {
                    name: "read_file",
                    description: "Read a file",
                    inputSchema: {
                      type: "object",
                      properties: {
                        path: { type: "string", description: "Path" },
                      },
                      required: ["path"],
                    },
                  },
                ],
              },
              {
                id: "gh",
                name: "GitHub",
                status: "failed",
                error: "GITHUB_TOKEN 未配置",
                tools: [{ name: "create_issue" }],
              },
            ],
          },
        }),
      ),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(await screen.findByText("mcp_fs_read_file")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /本机连接器/ })).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "去连接器增删启停" })
        .getAttribute("href"),
    ).toBe(APP_PATHS.toolbox.connectors);
    expect(screen.getByText("MCP")).toBeTruthy();
    expect(screen.getByText("Filesystem")).toBeTruthy();
    expect(screen.getByText("GitHub")).toBeTruthy();
    expect(screen.getByText("未列出")).toBeTruthy();
    expect(screen.getByText("GITHUB_TOKEN 未配置")).toBeTruthy();
    expect(screen.queryByText("mcp_gh_create_issue")).toBeNull();
    expect(screen.queryByText("create_issue")).toBeNull();
  });

  it("list_tools 失败时诚实说明，不拆内置目录", async () => {
    stubMcpApi(
      vi.fn(
        async (): Promise<McpOpResult> => ({
          ok: false,
          error: { kind: "io", detail: "读配置失败" },
        }),
      ),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("web_search")).toBeTruthy());
    expect(await screen.findByText("读配置失败")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});
