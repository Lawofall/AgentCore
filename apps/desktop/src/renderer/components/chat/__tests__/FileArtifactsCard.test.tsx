import { FileArtifactsCard } from "@/components/chat/FileArtifactsCard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { workspaceKeys } from "@/lib/queryKeys";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// @vitest-environment jsdom
import {
  type RenderResult,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactElement, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

function renderCard(ui: ReactElement): RenderResult {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
    },
  });
  // TurnFileChangesReview → useConversationWorkspace → useWorkspaces
  client.setQueryData(workspaceKeys.list, []);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>{ui}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const { showFile, showChanges, openInAppPreview, openWorkspaceHtmlInBrowser } =
  vi.hoisted(() => ({
    showFile: vi.fn(),
    showChanges: vi.fn(),
    openInAppPreview: vi.fn(),
    openWorkspaceHtmlInBrowser: vi.fn(),
  }));

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: (_key: string | null, initial: boolean) =>
    useState(initial),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (
    sel: (s: { showFile: () => void; showChanges: () => void }) => unknown,
  ) => sel({ showFile, showChanges }),
}));

// 能力判定与对话侧栏同一套：卡直接问 useConversationFileSource 挂没挂 openInAppPreview。
vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: vi.fn(() => null),
}));
vi.mock("@/hooks/useWorkspaces", () => ({
  useConversationWorkspace: vi.fn(() => null),
}));
vi.mock("@/lib/openWorkspaceHtmlInBrowser", () => ({
  openWorkspaceHtmlInBrowser,
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => false),
}));
vi.mock("@/services/cloudDeskExit", () => ({
  mergeArtifactsOnlyToLanding: vi.fn(async () => ({ ok: true })),
}));

import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import { mergeArtifactsOnlyToLanding } from "@/services/cloudDeskExit";
import type { WorkspaceInfo } from "@/services/workspaces";

const sourceWithPreview = {
  openInAppPreview,
} as unknown as FileSource;

function sourceWithList(
  entriesByDir: Record<string, FileNode[]>,
): FileSource & { listDir: ReturnType<typeof vi.fn> } {
  const listDir = vi.fn(async (dir: string) => entriesByDir[dir] ?? []);
  return {
    id: "workspace:test",
    label: "工作区",
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listDir,
    read: async () => ({ kind: "text" as const, text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
  };
}

const sessionWs: WorkspaceInfo = {
  wsId: "folder:proj",
  name: "项目",
  location: "cloud",
  rootId: null,
  subpath: "",
  hasFiles: true,
};

describe("FileArtifactsCard acceptance labels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("通过行不打已验收，未通过仍标，且不显示写入/编辑", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
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
      />,
    );
    expect(screen.queryByText("已验收")).toBeNull();
    expect(screen.getByText("未通过")).toBeTruthy();
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
  });

  it("全员通过时无已验收徽章", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          { path: "a.md", name: "a.md", acceptance: "accepted" },
          { path: "b.md", name: "b.md", acceptance: "accepted" },
        ]}
      />,
    );
    expect(screen.queryByText("已验收")).toBeNull();
    expect(screen.getByText("a.md")).toBeTruthy();
    expect(screen.getByText("b.md")).toBeTruthy();
  });

  it("write/edit tool rows omit op badges", () => {
    renderCard(
      <FileArtifactsCard
        artifacts={[
          { path: "src/main.ts", name: "main.ts", op: "write" },
          {
            path: "src/a.ts",
            name: "a.ts",
            op: "edit",
            change: { kind: "edit", oldText: "a", newText: "b" },
          },
        ]}
      />,
    );
    expect(screen.queryByText("写入")).toBeNull();
    expect(screen.queryByText("编辑")).toBeNull();
  });

  it("卡上无写入归因入口", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        turnKey="msg-1"
        artifacts={[{ path: "ok.md", name: "ok.md", acceptance: "accepted" }]}
      />,
    );
    expect(screen.queryByLabelText("查看写入归因")).toBeNull();
    expect(screen.queryByText("查看写入归因")).toBeNull();
  });
});

describe("FileArtifactsCard 导出件主推 / 中间稿折叠", () => {
  const md = "抚养费起诉状-昝雯.md";
  const docx = "抚养费起诉状-昝雯.docx";
  const exported = [
    { path: md, name: md, acceptance: "accepted" as const, kind: "md" },
    {
      path: docx,
      name: docx,
      acceptance: "accepted" as const,
      kind: "docx",
      derivedFrom: md,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("Word 直接可点，源 md 收进中间稿折叠区（计数仍是 2）", () => {
    renderCard(<FileArtifactsCard conversationId="c1" artifacts={exported} />);

    expect(screen.getByTitle(`在工作区预览 ${docx}`)).toBeTruthy();
    expect(screen.queryByTitle(`在工作区预览 ${md}`)).toBeNull();
    expect(screen.getByText("中间稿 1 份")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("折叠 ≠ 删除：展开中间稿仍能打开源 md", () => {
    renderCard(<FileArtifactsCard conversationId="c1" artifacts={exported} />);

    fireEvent.click(screen.getByText("中间稿 1 份"));
    fireEvent.click(screen.getByTitle(`在工作区预览 ${md}`));
    expect(showFile).toHaveBeenCalledWith(md, md, undefined);
  });

  it("没自报派生关系：两份并列，无中间稿区", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          { path: "报告.md", name: "报告.md", acceptance: "accepted" },
          { path: "报告.docx", name: "报告.docx", acceptance: "accepted" },
        ]}
      />,
    );
    expect(screen.getByTitle("在工作区预览 报告.md")).toBeTruthy();
    expect(screen.getByTitle("在工作区预览 报告.docx")).toBeTruthy();
    expect(screen.queryByText(/中间稿/)).toBeNull();
  });
});

describe("FileArtifactsCard stage labels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("AgentCore/文档/research/debate 路径显示约定文档标签，普通路径零噪音", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
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
          { path: "src/main.ts", name: "main.ts", op: "write" },
        ]}
      />,
    );
    expect(screen.getByText("调研约定文档")).toBeTruthy();
    expect(screen.getByText("辩论产物")).toBeTruthy();
    expect(
      screen.getByTitle(
        "在文件页查看约定文档 AgentCore/文档/research/brief.md",
      ),
    ).toBeTruthy();
    expect(screen.getByTitle("在工作区预览 src/main.ts")).toBeTruthy();
    // 普通文件不应出现约定文档标签（仅两处约定标签）
    expect(screen.getAllByText(/约定文档|产物/).length).toBe(2);
  });
});

describe("FileArtifactsCard — HTML 产物直达完整预览", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("会话具备完整预览能力：点 HTML 行直达完整预览 tab，非 HTML 仍走 showFile", () => {
    vi.mocked(useConversationFileSource).mockReturnValue(sourceWithPreview);
    vi.mocked(useConversationWorkspace).mockReturnValue(sessionWs);
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          { path: "site/index.html", name: "index.html", op: "write" },
          { path: "data.csv", name: "data.csv", op: "write" },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle("打开完整预览 site/index.html"));
    expect(openWorkspaceHtmlInBrowser).toHaveBeenCalledWith(
      "c1",
      "site/index.html",
      "folder:proj",
    );
    expect(showFile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle("在工作区预览 data.csv"));
    expect(showFile).toHaveBeenCalledWith("data.csv", "data.csv", undefined);
    expect(openWorkspaceHtmlInBrowser).toHaveBeenCalledOnce();
  });

  it("artifact.workspaceId 优先于会话工作区 desk", () => {
    vi.mocked(useConversationFileSource).mockReturnValue(sourceWithPreview);
    vi.mocked(useConversationWorkspace).mockReturnValue(sessionWs);
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          {
            path: "site/index.html",
            name: "index.html",
            acceptance: "accepted",
            workspaceId: "folder:other",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle("打开完整预览 site/index.html"));
    expect(openWorkspaceHtmlInBrowser).toHaveBeenCalledWith(
      "c1",
      "site/index.html",
      "folder:other",
    );
  });

  it("非 HTML 产物带 workspaceId 时 showFile 跟落地桌；无则回退会话桌", () => {
    vi.mocked(useConversationFileSource).mockReturnValue(sourceWithPreview);
    vi.mocked(useConversationWorkspace).mockReturnValue(sessionWs);
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          {
            path: "notes.md",
            name: "notes.md",
            op: "write",
            workspaceId: "folder:landed",
          },
          { path: "readme.md", name: "readme.md", op: "write" },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle("在工作区预览 notes.md"));
    expect(showFile).toHaveBeenCalledWith(
      "notes.md",
      "notes.md",
      "folder:landed",
    );

    fireEvent.click(screen.getByTitle("在工作区预览 readme.md"));
    expect(showFile).toHaveBeenCalledWith("readme.md", "readme.md", undefined);
  });

  it("无能力（本地会话 / web）：HTML 行回落 showFile 进文件视图", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          { path: "site/index.html", name: "index.html", op: "write" },
        ]}
      />,
    );

    fireEvent.click(screen.getByTitle("在工作区预览 site/index.html"));
    expect(showFile).toHaveBeenCalledWith(
      "site/index.html",
      "index.html",
      undefined,
    );
    expect(openWorkspaceHtmlInBrowser).not.toHaveBeenCalled();
  });
});

describe("FileArtifactsCard — A1 查看改动", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("有 change 预览时显示「查看改动」，点击聚焦右坞改动 tab", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        turnKey="msg-1"
        artifacts={[
          {
            path: "src/a.ts",
            name: "a.ts",
            op: "edit",
            change: { kind: "edit", oldText: "a", newText: "b" },
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByLabelText("查看改动"));
    expect(showChanges).toHaveBeenCalledWith("msg-1");
    expect(screen.queryByText(/改动已写入工作区/)).toBeNull();
  });

  it("无 change 预览时不显示「查看改动」", () => {
    renderCard(
      <FileArtifactsCard
        artifacts={[{ path: "src/a.ts", name: "a.ts", op: "write" }]}
      />,
    );
    expect(screen.queryByLabelText("查看改动")).toBeNull();
  });
});

describe("FileArtifactsCard — 清单不按历史 promoted 分组", () => {
  // 工作稿是约定目录（`stageDirs`），落在它下面的行走「在文件页查看」入口而非工作区预览。
  const WORKROOM = "AgentCore/文档/工作稿";
  const product = {
    path: "起诉状.docx",
    name: "起诉状.docx",
    acceptance: "accepted" as const,
    promotedFrom: `${WORKROOM}/起诉状.docx`,
  };
  const material = {
    path: `${WORKROOM}/取证清单.md`,
    name: "取证清单.md",
    acceptance: "accepted" as const,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("已验收平铺同一列表，无成品/过程材料标题；归位件只显示新路径", () => {
    renderCard(
      <FileArtifactsCard conversationId="c1" artifacts={[material, product]} />,
    );

    expect(screen.queryByText("成品")).toBeNull();
    expect(screen.queryByText("过程材料")).toBeNull();

    // 归位是移动：只显示新路径，.agentcore 里的旧路径已失效。
    const productRow = screen.getByTitle("在工作区预览 起诉状.docx");
    expect(
      screen.queryByTitle(`在文件页查看约定文档 ${WORKROOM}/起诉状.docx`),
    ).toBeNull();
    fireEvent.click(productRow);
    expect(showFile).toHaveBeenCalledWith(
      "起诉状.docx",
      "起诉状.docx",
      undefined,
    );

    const materialRow = screen.getByTitle(
      `在文件页查看约定文档 ${WORKROOM}/取证清单.md`,
    );
    expect(productRow.closest("ul")).toBe(materialRow.closest("ul"));
  });

  it("未通过单独一列，不混进已验收列表", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          product,
          material,
          {
            path: "报告.md",
            name: "报告.md",
            acceptance: "rejected" as const,
            acceptanceDetail: "缺引用",
          },
        ]}
      />,
    );

    expect(screen.getByText("未通过")).toBeTruthy();
    const rejectedList = screen
      .getByTitle("在工作区预览 报告.md")
      .closest("ul");
    const acceptedList = screen
      .getByTitle("在工作区预览 起诉状.docx")
      .closest("ul");
    expect(rejectedList).not.toBe(acceptedList);
    expect(acceptedList).toBe(
      screen
        .getByTitle(`在文件页查看约定文档 ${WORKROOM}/取证清单.md`)
        .closest("ul"),
    );
  });
});

describe("FileArtifactsCard — 产出卡只列文件", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("没有落点告知条或新建文件夹文案", () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        turnKey="msg-1"
        artifacts={[{ path: "notes.md", name: "notes.md", op: "write" }]}
      />,
    );

    expect(screen.getByText("本回合产出文件")).toBeTruthy();
    expect(screen.getByTitle("在工作区预览 notes.md")).toBeTruthy();
    expect(screen.queryByText("文件已存到新建的文件夹")).toBeNull();
    expect(screen.queryByText("已为这次对话新建文件夹")).toBeNull();
    expect(screen.queryByTestId("auto-folder-notice")).toBeNull();
    expect(screen.queryByTestId("auto-folder-notice-card")).toBeNull();
  });
});

describe("FileArtifactsCard — 工作区 list 修改时间", () => {
  const HOUR = 3_600_000;
  const NOW = new Date("2026-08-14T12:00:00").getTime();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(hasLocalFiles).mockReturnValue(false);
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(null);
  });

  it("list 命中的行只显示修改时间（与文件树同一套文案）", async () => {
    vi.setSystemTime(NOW);
    const src = sourceWithList({
      "": [
        {
          path: "notes.md",
          name: "notes.md",
          isDir: false,
          sizeBytes: 2048,
          mtimeMs: NOW - HOUR,
        },
      ],
    });
    vi.mocked(useConversationFileSource).mockReturnValue(src);

    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[{ path: "notes.md", name: "notes.md", op: "write" }]}
      />,
    );

    expect(await screen.findByText("11:00")).toBeTruthy();
    expect(screen.queryByText(/KB/)).toBeNull();
    expect(screen.queryByText(/\d+ B\b/)).toBeNull();
    expect(src.listDir).toHaveBeenCalledWith("");
    vi.useRealTimers();
  });

  it("嵌套路径问父目录 list，不新造接口", async () => {
    vi.setSystemTime(NOW);
    const src = sourceWithList({
      src: [
        {
          path: "src/main.ts",
          name: "main.ts",
          isDir: false,
          sizeBytes: 10,
          mtimeMs: NOW - HOUR,
        },
      ],
    });
    vi.mocked(useConversationFileSource).mockReturnValue(src);

    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[{ path: "src/main.ts", name: "main.ts", op: "write" }]}
      />,
    );

    expect(await screen.findByText("11:00")).toBeTruthy();
    expect(screen.queryByText(/10 B/)).toBeNull();
    expect(src.listDir).toHaveBeenCalledWith("src");
    expect(src.listDir).not.toHaveBeenCalledWith("");
    vi.useRealTimers();
  });

  it("对不上或缺字段就不占位，不拿 0 B / 未知冒充", async () => {
    const src = sourceWithList({
      "": [
        {
          path: "other.md",
          name: "other.md",
          isDir: false,
          sizeBytes: 99,
          mtimeMs: NOW,
        },
        {
          path: "empty.md",
          name: "empty.md",
          isDir: false,
          sizeBytes: null,
          mtimeMs: null,
        },
      ],
    });
    vi.mocked(useConversationFileSource).mockReturnValue(src);

    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          { path: "missing.md", name: "missing.md", op: "write" },
          { path: "empty.md", name: "empty.md", op: "write" },
        ]}
      />,
    );

    await waitFor(() => expect(src.listDir).toHaveBeenCalled());
    expect(screen.queryByText(/0 B/)).toBeNull();
    expect(screen.queryByText(/未知/)).toBeNull();
    expect(screen.queryByText(/99 B/)).toBeNull();
  });

  it("别的落地桌不套用会话 list 的数字", async () => {
    const src = sourceWithList({
      "": [
        {
          path: "notes.md",
          name: "notes.md",
          isDir: false,
          sizeBytes: 2048,
          mtimeMs: NOW,
        },
      ],
    });
    vi.mocked(useConversationFileSource).mockReturnValue(src);
    vi.mocked(useConversationWorkspace).mockReturnValue(sessionWs);

    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          {
            path: "notes.md",
            name: "notes.md",
            op: "write",
            workspaceId: "folder:other",
          },
        ]}
      />,
    );

    expect(screen.getByTitle("在工作区预览 notes.md")).toBeTruthy();
    await act(async () => {
      await Promise.resolve();
    });
    expect(src.listDir).not.toHaveBeenCalled();
    expect(screen.queryByText(/2.0 KB/)).toBeNull();
  });
});

describe("FileArtifactsCard — 合回产物", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useConversationFileSource).mockReturnValue(null);
    vi.mocked(useConversationWorkspace).mockReturnValue(sessionWs);
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    window.fsApi = {
      listRoots: vi.fn().mockResolvedValue([{ id: "root-1", name: "desk" }]),
    } as unknown as typeof window.fsApi;
  });

  it("桌面云会话有通过产物时显示，点击按本卡路径合回", async () => {
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[
          {
            path: "out/a.md",
            name: "a.md",
            acceptance: "accepted",
            workspaceId: "folder:proj",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "合回产物" }));
    await waitFor(() => {
      expect(mergeArtifactsOnlyToLanding).toHaveBeenCalledWith(
        "c1",
        [{ id: "root-1", name: "desk" }],
        [{ path: "out/a.md", workspaceId: "folder:proj" }],
      );
    });
  });

  it("本机工作区或非桌面不显示", () => {
    vi.mocked(useConversationWorkspace).mockReturnValue({
      ...sessionWs,
      location: "local",
    });
    renderCard(
      <FileArtifactsCard
        conversationId="c1"
        artifacts={[{ path: "out/a.md", name: "a.md", acceptance: "accepted" }]}
      />,
    );
    expect(screen.queryByRole("button", { name: "合回产物" })).toBeNull();
  });
});
