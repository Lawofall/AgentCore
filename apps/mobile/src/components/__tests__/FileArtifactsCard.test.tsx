// @vitest-environment jsdom
import { resetArtifactListingMetaInflight } from "@/lib/artifactListingMeta";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FileArtifactsCard } from "../FileArtifactsCard";

const navigate = vi.fn();
const { listWorkspaceFiles, listWorkspaceFilesByWs } = vi.hoisted(() => ({
  listWorkspaceFiles: vi.fn(),
  listWorkspaceFilesByWs: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock("@/api/turnFilesDiff", () => ({
  getTurnFilesDiff: vi.fn().mockResolvedValue({
    messageId: "m1",
    baselineSnapshotId: null,
    available: false,
    changes: [],
    total: 0,
    added: 0,
    modified: 0,
    deleted: 0,
  }),
}));

vi.mock("@/api/workspace", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/workspace")>("@/api/workspace");
  return { ...actual, listWorkspaceFiles };
});

vi.mock("@/api/workspaces", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/workspaces")>(
      "@/api/workspaces",
    );
  return { ...actual, listWorkspaceFilesByWs };
});

beforeEach(() => {
  navigate.mockClear();
  resetArtifactListingMetaInflight();
  listWorkspaceFiles.mockReset();
  listWorkspaceFilesByWs.mockReset();
  listWorkspaceFiles.mockResolvedValue({ entries: [], truncated: false });
  listWorkspaceFilesByWs.mockResolvedValue({ entries: [], truncated: false });
});

describe("FileArtifactsCard acceptance labels", () => {
  it("通过行不打已验收，未通过仍标，且不显示写入/编辑", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "ok.md",
              name: "ok.md",
              acceptance: "accepted",
            },
            {
              path: "bad.md",
              name: "bad.md",
              acceptance: "rejected",
              acceptanceReason: "citations_unverified",
              acceptanceDetail: "缺引用",
            },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText("已验收")).toBeNull();
    expect(screen.getByText("未通过")).toBeTruthy();
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
  });

  it("全员通过时无已验收徽章", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          messageId="m1"
          artifacts={[
            { path: "a.md", name: "a.md", acceptance: "accepted" },
            { path: "b.md", name: "b.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText("已验收")).toBeNull();
    expect(screen.getByText("a.md")).toBeTruthy();
    expect(screen.getByText("b.md")).toBeTruthy();
  });

  it("write/edit tool rows omit op badges", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          messageId="m1"
          artifacts={[
            { path: "src/main.ts", name: "main.ts", op: "write" },
            { path: "src/a.ts", name: "a.ts", op: "edit" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
  });
});

describe("FileArtifactsCard stage labels", () => {
  it("AgentCore/文档/research/debate 路径显示约定文档标签，普通路径零噪音", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "AgentCore/文档/research/brief.md",
              name: "brief.md",
              op: "write",
            },
            {
              path: "AgentCore/文档/debate/round.md",
              name: "round.md",
              op: "write",
            },
            { path: "notes.txt", name: "notes.txt", op: "write" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("调研约定文档")).toBeTruthy();
    expect(screen.getByText("辩论产物")).toBeTruthy();
    expect(
      screen.getByTitle(
        "在文件页查看约定文档 AgentCore/文档/research/brief.md",
      ),
    ).toBeTruthy();
    expect(screen.getByTitle("在工作区查看 notes.txt")).toBeTruthy();
  });
});

describe("FileArtifactsCard 查看改动", () => {
  it("shows 查看改动 when conversationId+messageId present and expands review", async () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          messageId="m1"
          artifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "write",
              change: {
                kind: "write",
                content: "x",
                mode: "overwrite",
              },
            },
          ]}
          reviewArtifacts={[
            {
              path: "a.ts",
              name: "a.ts",
              op: "write",
              change: {
                kind: "write",
                content: "x",
                mode: "overwrite",
              },
            },
          ]}
        />
      </MemoryRouter>,
    );
    const btn = screen.getByLabelText("查看改动");
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    expect(await screen.findByText(/工具参数侧预览/)).toBeTruthy();
  });

  it("hides 查看改动 without messageId and without change previews", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[{ path: "a.ts", name: "a.ts", acceptance: "accepted" }]}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByLabelText("查看改动")).toBeNull();
  });
});

describe("FileArtifactsCard open routing", () => {
  it("opens conversation files when no workspaceId", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            { path: "notes.md", name: "notes.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle("在工作区查看 notes.md"));
    expect(navigate).toHaveBeenCalledWith("/c/c1/files", {
      state: { openPath: "notes.md" },
    });
  });

  it("opens workspace files desk when workspaceId is set", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            {
              path: "version-a-clean.html",
              name: "version-a-clean.html",
              acceptance: "accepted",
              workspaceId: "folder:proj-1",
            },
          ]}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle("在工作区查看 version-a-clean.html"));
    expect(navigate).toHaveBeenCalledWith(
      `/files/${encodeURIComponent("folder:proj-1")}`,
      {
        state: {
          openPath: "version-a-clean.html",
          fromConversationId: "c1",
        },
      },
    );
  });

  it("does not render auto-folder landing copy on the card", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            { path: "a.md", name: "a.md", acceptance: "accepted" },
            { path: "b.md", name: "b.md", acceptance: "accepted" },
            { path: "c.md", name: "c.md", acceptance: "accepted" },
            { path: "d.md", name: "d.md", acceptance: "accepted" },
            { path: "e.md", name: "e.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("本回合产出文件")).toBeTruthy();
    expect(screen.queryByText("文件已存到新建的文件夹")).toBeNull();
    expect(screen.queryByText("已为这次对话新建文件夹")).toBeNull();
  });
});

describe("FileArtifactsCard list size/mtime", () => {
  it("shows mtime only on a row when the conversation list hits the path", async () => {
    listWorkspaceFiles.mockResolvedValue({
      entries: [
        {
          path: "notes.md",
          is_dir: false,
          size_bytes: 12000,
          mtime_ms: Date.now() - 1000,
        },
      ],
      truncated: false,
    });
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            { path: "notes.md", name: "notes.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(await screen.findByText("刚刚")).toBeTruthy();
    expect(screen.queryByText("12 KB")).toBeNull();
    expect(screen.queryByText("12 KB · 刚刚")).toBeNull();
    expect(listWorkspaceFiles).toHaveBeenCalledWith("c1");
    expect(listWorkspaceFilesByWs).not.toHaveBeenCalled();
  });

  it("looks up workspaceId artifacts on the existing workspace list", async () => {
    listWorkspaceFilesByWs.mockResolvedValue({
      entries: [
        {
          path: "version-a-clean.html",
          is_dir: false,
          size_bytes: 2048,
          mtime_ms: Date.now() - 1000,
        },
      ],
      truncated: false,
    });
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            {
              path: "version-a-clean.html",
              name: "version-a-clean.html",
              acceptance: "accepted",
              workspaceId: "folder:proj-1",
            },
          ]}
        />
      </MemoryRouter>,
    );
    expect(await screen.findByText("刚刚")).toBeTruthy();
    expect(screen.queryByText("2.0 KB")).toBeNull();
    expect(listWorkspaceFilesByWs).toHaveBeenCalledWith("folder:proj-1");
    expect(listWorkspaceFiles).not.toHaveBeenCalled();
  });

  it("leaves the row blank when the path misses or mtime is null", async () => {
    listWorkspaceFiles.mockResolvedValue({
      entries: [
        {
          path: "other.md",
          is_dir: false,
          size_bytes: 12000,
          mtime_ms: Date.now() - 1000,
        },
        {
          path: "notes.md",
          is_dir: false,
          size_bytes: 12000,
          mtime_ms: null,
        },
      ],
      truncated: false,
    });
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId="c1"
          artifacts={[
            { path: "notes.md", name: "notes.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenCalledWith("c1"));
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(screen.queryByText("刚刚")).toBeNull();
    expect(screen.queryByText("12 KB · 刚刚")).toBeNull();
    expect(screen.queryByText("12 KB")).toBeNull();
  });

  it("does not fetch when there is no conversation or workspace desk", () => {
    render(
      <MemoryRouter>
        <FileArtifactsCard
          conversationId={null}
          artifacts={[
            { path: "notes.md", name: "notes.md", acceptance: "accepted" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(listWorkspaceFiles).not.toHaveBeenCalled();
    expect(listWorkspaceFilesByWs).not.toHaveBeenCalled();
  });
});
