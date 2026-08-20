// @vitest-environment jsdom
/**
 * 文件 tab「我的文件」：云端工作区按种类分组；本机过滤；空组不渲染标题。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { listWorkspaces } = vi.hoisted(() => ({
  listWorkspaces: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
}));

vi.mock("@/api/workspaces", () => ({
  listWorkspaces,
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => navigate };
});

import type { WorkspaceSummary } from "@/api/workspaces";
import { WorkspacesPage } from "@/pages/WorkspacesPage";

function ws(
  over: Partial<WorkspaceSummary> & Pick<WorkspaceSummary, "wsId" | "name">,
): WorkspaceSummary {
  return {
    location: "cloud",
    hasFiles: true,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listWorkspaces.mockResolvedValue([]);
});

afterEach(cleanup);

describe("WorkspacesPage · 我的文件", () => {
  it("三组标题只在有条目时出现；不列裸聊 scratch", async () => {
    listWorkspaces.mockResolvedValue([
      ws({ wsId: "folder:f1", name: "设计稿" }),
      ws({ wsId: "conv:c1", name: "一次快速对话" }),
    ]);

    render(<WorkspacesPage />);

    expect(await screen.findByText("设计稿")).toBeTruthy();
    expect(screen.queryByText("一次快速对话")).toBeNull();
    expect(screen.getByText("我的文件")).toBeTruthy();
    expect(screen.getByText("全局设定")).toBeTruthy();
    expect(screen.getByText("会进模型的条目 · 常驻 / 按需")).toBeTruthy();
    expect(screen.queryByText("规则")).toBeNull();

    expect(screen.getByText("文件夹")).toBeTruthy();
    expect(screen.queryByText("对话产物")).toBeNull();
    expect(screen.queryByText("共享空间")).toBeNull();
  });

  it("本机工作区被过滤，不出现在列表", async () => {
    listWorkspaces.mockResolvedValue([
      ws({ wsId: "folder:f1", name: "云端项目" }),
      ws({
        wsId: "folder:local",
        name: "本机仓库",
        location: "local",
      }),
      ws({ wsId: "shared:s1", name: "团队盘" }),
    ]);

    render(<WorkspacesPage />);

    expect(await screen.findByText("云端项目")).toBeTruthy();
    expect(screen.getByText("团队盘")).toBeTruthy();
    expect(screen.queryByText("本机仓库")).toBeNull();
    expect(screen.getByText("文件夹")).toBeTruthy();
    expect(screen.getByText("共享空间")).toBeTruthy();
    expect(screen.queryByText("对话产物")).toBeNull();
  });

  it("全空显示「还没有工作区」", async () => {
    listWorkspaces.mockResolvedValue([]);

    render(<WorkspacesPage />);

    expect(await screen.findByText("还没有工作区")).toBeTruthy();
    expect(
      screen.getByText("在对话里产出文件后，会以文件夹出现在这里。"),
    ).toBeTruthy();
    expect(screen.queryByText("还没有云端工作区")).toBeNull();
    expect(screen.queryByText("文件夹")).toBeNull();
    expect(screen.queryByText("对话产物")).toBeNull();
    expect(screen.queryByText("共享空间")).toBeNull();
  });

  it("仅有本机时显示「还没有云端工作区」", async () => {
    listWorkspaces.mockResolvedValue([
      ws({
        wsId: "local-ws",
        name: "本机仓库",
        location: "local",
      }),
    ]);

    render(<WorkspacesPage />);

    expect(await screen.findByText("还没有云端工作区")).toBeTruthy();
    expect(
      screen.getByText(
        "本地工作区请在桌面端查看。云端产出的文件会以文件夹出现在这里。",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("本机仓库")).toBeNull();
    expect(screen.queryByText("还没有工作区")).toBeNull();
  });

  it("load failure is a generic .error line, not a needs-you bar", async () => {
    listWorkspaces.mockRejectedValue(new Error("加载工作区失败"));
    render(<WorkspacesPage />);
    expect(await screen.findByText("加载工作区失败")).toBeTruthy();
    const line = screen.getByText("加载工作区失败").closest(".error");
    expect(line?.className.split(/\s+/)).toEqual(
      expect.arrayContaining(["error"]),
    );
    expect(line?.className).not.toMatch(/\b(bar|inline-actions|needs-you)\b/);
  });
});
