// @vitest-environment jsdom
/**
 * 草稿态「在哪工作」（双模式工作区 §5.1）。
 *
 * 第一屏只选地方；新建 / Git / 本机三选收进「新建或加入…」。
 * 全菜单只有一条分隔线（文件夹列表 ↔ 新建或加入）。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { pickLocalFolderRoot } from "@/lib/bindLocalFolder";
import { isBorrowActive, set } from "@/lib/borrowOriginalPreference";
import { startBorrowToCloudJob } from "@/lib/borrowToCloudJob";
import { setComposerChannelPreference } from "@/lib/composerChannelPreference";
import { startImportToCloudJob } from "@/lib/importToCloudJob";
import { openLocalFolderFromRoot } from "@/lib/openLocalFolder";
import { uiSet } from "@/lib/uiStorage";
import type { FolderMeta } from "@/services/folders";
import { useFoldersStore } from "@/stores/folders";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const grouped = vi.hoisted(() => ({
  value: { folders: [], conversations: [] } as {
    folders: FolderMeta[];
    conversations: {
      id?: string;
      folderId?: string | null;
      updatedAt: string;
    }[];
  },
}));

const sharedWithMe = vi.hoisted(() => ({
  value: [] as FolderMeta[],
}));

vi.mock("@/hooks/useConversations", () => ({
  useGroupedConversations: () => ({ data: grouped.value }),
  getConversations: () => grouped.value.conversations,
}));

vi.mock("@/hooks/useFolderSharing", () => ({
  useSharedWithMeFolders: () => ({ data: sharedWithMe.value }),
}));

vi.mock("@/lib/bindLocalFolder", () => ({
  pickLocalFolderRoot: vi.fn(),
  notifyLocalPickerFailure: vi.fn(),
}));

vi.mock("@/lib/openLocalFolder", () => ({
  openLocalFolderFromRoot: vi.fn(),
  pickAndOpenLocalFolder: vi.fn(),
}));

vi.mock("@/lib/importToCloudJob", () => ({
  startImportToCloudJob: vi.fn(() => true),
  isImportToCloudJobRunning: () => false,
  cancelImportToCloudJob: vi.fn(),
}));

vi.mock("@/lib/borrowToCloudJob", () => ({
  startBorrowToCloudJob: vi.fn(() => true),
  isBorrowToCloudJobRunning: () => false,
  cancelBorrowToCloudJob: vi.fn(),
}));

const boundEffective = {
  isLocal: false,
  rootId: null,
  rootName: null,
  rootMissing: false,
  viaContainer: false,
  folderName: "Acme",
  viaFolder: true,
};

vi.mock("@/components/workspace/WorkspaceModeControl", () => ({
  useWorkspaceModeState: (id: string | null) =>
    id
      ? {
          binding: {
            mode: "cloud",
            scope: "folder",
            rootId: null,
            source: null,
          },
          roots: [],
          effective: boundEffective,
          refresh: async () => {},
        }
      : null,
  WorkspaceModeMenu: () => <div data-testid="ws-menu" />,
  WorkspaceModeTrigger: () => <span>Acme</span>,
}));

import { ComposerWorkspaceChip } from "../ComposerWorkspaceChip";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
});

/** `hasLocalFiles()` = fsApi preload present and not the web runtime. */
function setHasLocalDisk(present: boolean) {
  (
    window as unknown as { fsApi?: { removeRoot?: ReturnType<typeof vi.fn> } }
  ).fsApi = present ? { removeRoot: vi.fn() } : undefined;
}

function cloudFolder(
  id: string,
  name: string,
  parentRelPath?: string | null,
): FolderMeta {
  return {
    id,
    name,
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    parentRelPath: parentRelPath ?? null,
    relPath: parentRelPath ? `${parentRelPath}/${name}` : name,
  };
}

function localFolder(
  id: string,
  name: string,
  localSubpath: string | null,
): FolderMeta {
  return { id, name, mode: "local", localRootId: "root-1", localSubpath };
}

/** Opens the draft chip's pick view; returns the popover content. */
function openPicker(): HTMLElement {
  render(
    <MemoryRouter>
      <TooltipProvider>
        <ComposerWorkspaceChip conversationId={null} />
      </TooltipProvider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByLabelText("在哪工作"));
  return screen.getByRole("dialog");
}

/** Elements actually drawing a horizontal rule (exact class token, not `border-border`). */
function rules(menu: HTMLElement): Element[] {
  return [menu, ...menu.querySelectorAll("*")].filter((el) =>
    [...el.classList].some((c) => c === "border-t" || c === "border-b"),
  );
}

function precedes(a: Element, b: Element): boolean {
  return Boolean(
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

function expectImportConfirm(folderName: string) {
  expect(
    screen.getByRole("heading", { name: "复制到云上当新家" }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      `把「${folderName}」复制到「我的文件」。之后改云上这份，电脑上的原件不再跟着变。`,
    ),
  ).toBeTruthy();
  expect(screen.queryByText("导入到「我的文件」")).toBeNull();
  expect(startImportToCloudJob).not.toHaveBeenCalled();
}

function expectBorrowConfirm(folderName: string) {
  expect(
    screen.getByRole("heading", { name: "先在云上做，原件先不动" }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      `把「${folderName}」复制到云上做这一单。电脑上的原件先不动，做完再决定写不写回。`,
    ),
  ).toBeTruthy();
  expect(screen.queryByText("云上做完再写入")).toBeNull();
  expect(startBorrowToCloudJob).not.toHaveBeenCalled();
}

function confirmCloudCopyStart() {
  fireEvent.click(screen.getByRole("button", { name: "开始" }));
}

beforeEach(() => {
  grouped.value = { folders: [], conversations: [] };
  sharedWithMe.value = [];
  setHasLocalDisk(true);
  setComposerChannelPreference("cloud");
  useFoldersStore.setState({ draftWorkspaceIntent: { kind: "quick_cloud" } });
  vi.mocked(pickLocalFolderRoot).mockReset();
  vi.mocked(openLocalFolderFromRoot).mockReset();
  vi.mocked(startImportToCloudJob).mockReset().mockReturnValue(true);
  vi.mocked(startBorrowToCloudJob).mockReset().mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  setHasLocalDisk(false);
  uiSet("borrow-original", undefined);
});

describe("DraftChip pick view · 选地方", () => {
  it("第一屏只有快速对话、文件夹和新建或加入，不铺六条动词", () => {
    const menu = within(openPicker());
    expect(menu.getByRole("button", { name: "快速对话" })).toBeTruthy();
    expect(menu.getByRole("button", { name: "新建或加入…" })).toBeTruthy();
    expect(menu.getByText("了解区别")).toBeTruthy();

    for (const buried of [
      "从本机导入",
      "云上做完再写入",
      "从 Git 克隆",
      "打开本机文件夹",
      "新建文件夹",
      "从本机加入",
    ]) {
      expect(menu.queryByRole("button", { name: buried })).toBeNull();
    }
    expect(menu.queryByText("在「我的文件」里新建文件夹")).toBeNull();
  });

  it("全菜单只有一条分隔线，落在文件夹列表与新建或加入之间", () => {
    const content = openPicker();
    const menu = within(content);

    const dividers = rules(content);
    expect(dividers).toHaveLength(1);
    const separator = dividers[0];

    expect(precedes(menu.getByText("我的文件"), separator)).toBe(true);
    expect(precedes(separator, menu.getByText("新建或加入…"))).toBe(true);
    expect(precedes(separator, menu.getByText("了解区别"))).toBe(true);
  });

  it("新建或加入展开新建、Git、从本机加入", () => {
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    expect(menu.getByRole("button", { name: "返回" })).toBeTruthy();
    expect(menu.getByText("新建或加入")).toBeTruthy();
    expect(menu.queryByText("在哪工作")).toBeNull();
    expect(menu.getByRole("button", { name: "新建文件夹" })).toBeTruthy();
    expect(menu.getByRole("button", { name: "从 Git 克隆" })).toBeTruthy();
    expect(menu.getByRole("button", { name: "从本机加入" })).toBeTruthy();
  });

  it("从本机加入选完路径后三选一；直接改走已选根", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyRepo" },
    });
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    await act(async () => {
      fireEvent.click(menu.getByRole("button", { name: "从本机加入" }));
    });
    expect(menu.getByRole("button", { name: /直接改这个文件夹/ })).toBeTruthy();
    expect(menu.getByRole("button", { name: /复制到云上当新家/ })).toBeTruthy();
    expect(
      menu.getByRole("button", { name: /先在云上做，原件先不动/ }),
    ).toBeTruthy();

    fireEvent.click(menu.getByRole("button", { name: /直接改这个文件夹/ }));
    expect(openLocalFolderFromRoot).toHaveBeenCalledWith(
      { id: "root-1", name: "MyRepo" },
      expect.any(Function),
    );
  });

  it("复制到云上当新家先确认再开传，不走完整导入框", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyRepo" },
    });
    const openImport = vi.spyOn(
      useFoldersStore.getState(),
      "openImportToCloud",
    );
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    await act(async () => {
      fireEvent.click(menu.getByRole("button", { name: "从本机加入" }));
    });
    fireEvent.click(menu.getByRole("button", { name: /复制到云上当新家/ }));
    expect(openImport).not.toHaveBeenCalled();
    expectImportConfirm("MyRepo");
    confirmCloudCopyStart();
    expect(startImportToCloudJob).toHaveBeenCalledWith({
      root: { id: "root-1", name: "MyRepo" },
      ownsRoot: true,
      folderName: "MyRepo",
    });
  });

  it("取消轻量确认不上传，并丢掉新授权的本机根", async () => {
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyRepo" },
    });
    const removeRoot = vi.fn();
    (
      window as unknown as { fsApi?: { removeRoot?: ReturnType<typeof vi.fn> } }
    ).fsApi = { removeRoot };
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    await act(async () => {
      fireEvent.click(menu.getByRole("button", { name: "从本机加入" }));
    });
    fireEvent.click(menu.getByRole("button", { name: /复制到云上当新家/ }));
    expectImportConfirm("MyRepo");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(startImportToCloudJob).not.toHaveBeenCalled();
    expect(removeRoot).toHaveBeenCalledWith("root-1");
  });

  it("先在云上做先确认再开传，不打推荐；上次只打在直接改", async () => {
    setComposerChannelPreference("local_traditional");
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyRepo" },
    });
    const openBorrow = vi.spyOn(
      useFoldersStore.getState(),
      "openBorrowToCloud",
    );
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    expect(
      within(menu.getByRole("button", { name: "从本机加入" })).queryByText(
        "上次",
      ),
    ).toBeNull();
    await act(async () => {
      fireEvent.click(menu.getByRole("button", { name: "从本机加入" }));
    });
    const borrow = menu.getByRole("button", { name: /先在云上做，原件先不动/ });
    expect(within(borrow).queryByText("推荐")).toBeNull();
    expect(within(borrow).queryByText("上次")).toBeNull();
    expect(
      within(menu.getByRole("button", { name: /直接改这个文件夹/ })).getByText(
        "上次",
      ),
    ).toBeTruthy();
    fireEvent.click(borrow);
    expect(openBorrow).not.toHaveBeenCalled();
    expectBorrowConfirm("MyRepo");
    confirmCloudCopyStart();
    expect(startBorrowToCloudJob).toHaveBeenCalledWith({
      root: { id: "root-1", name: "MyRepo" },
      folderName: "MyRepo",
    });
  });

  it("草稿落到借用中的云文件夹时标明原件尚未改动", () => {
    grouped.value = {
      folders: [cloudFolder("f1", "Demo")],
      conversations: [],
    };
    set("f1", {
      rootId: "root-1",
      originalName: "Repo",
      promoted: false,
    });
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "folder", folderId: "f1" },
    });
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ComposerWorkspaceChip conversationId={null} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText("Demo")).toBeTruthy();
    expect(screen.getByText("原件尚未改动")).toBeTruthy();
  });

  it("无本机盘：第一屏直接新建文件夹，没有本机三选", () => {
    setHasLocalDisk(false);
    const content = openPicker();
    const menu = within(content);

    expect(menu.getByRole("button", { name: "新建文件夹" })).toBeTruthy();
    expect(menu.queryByText("新建或加入…")).toBeNull();
    expect(menu.queryByText("从本机导入")).toBeNull();
    expect(menu.queryByText("从本机加入")).toBeNull();
    expect(menu.queryByText("云上做完再写入")).toBeNull();
    expect(menu.queryByText("从 Git 克隆")).toBeNull();
    expect(menu.queryByText("打开本机文件夹")).toBeNull();
    expect(rules(content)).toHaveLength(1);
  });

  it("点本机文件夹进入三选；直接改只改草稿，不新开会话", () => {
    grouped.value = {
      folders: [localFolder("f-repo", "MyRepo", null)],
      conversations: [],
    };
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: /MyRepo/ }));
    expect(menu.getByRole("button", { name: /直接改这个文件夹/ })).toBeTruthy();
    expect(menu.getByRole("button", { name: /复制到云上当新家/ })).toBeTruthy();
    expect(
      menu.getByRole("button", { name: /先在云上做，原件先不动/ }),
    ).toBeTruthy();
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "quick_cloud",
    });

    fireEvent.click(menu.getByRole("button", { name: /直接改这个文件夹/ }));
    expect(openLocalFolderFromRoot).not.toHaveBeenCalled();
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "f-repo",
    });
  });

  it("点本机文件夹后复制到云上，确认后再开传且不交出根", () => {
    grouped.value = {
      folders: [localFolder("f-repo", "MyRepo", null)],
      conversations: [],
    };
    const openImport = vi.spyOn(
      useFoldersStore.getState(),
      "openImportToCloud",
    );
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: /MyRepo/ }));
    fireEvent.click(menu.getByRole("button", { name: /复制到云上当新家/ }));
    expect(openImport).not.toHaveBeenCalled();
    expectImportConfirm("MyRepo");
    confirmCloudCopyStart();
    expect(startImportToCloudJob).toHaveBeenCalledWith({
      root: { id: "root-1", name: "MyRepo" },
      ownsRoot: false,
      folderName: "MyRepo",
    });
  });

  it("点本机文件夹后先在云上做，确认后再开传且不交出根", () => {
    grouped.value = {
      folders: [localFolder("f-repo", "MyRepo", null)],
      conversations: [],
    };
    const openBorrow = vi.spyOn(
      useFoldersStore.getState(),
      "openBorrowToCloud",
    );
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: /MyRepo/ }));
    fireEvent.click(
      menu.getByRole("button", { name: /先在云上做，原件先不动/ }),
    );
    expect(openBorrow).not.toHaveBeenCalled();
    expectBorrowConfirm("MyRepo");
    confirmCloudCopyStart();
    expect(startBorrowToCloudJob).toHaveBeenCalledWith({
      root: { id: "root-1", name: "MyRepo" },
      folderName: "MyRepo",
    });
  });

  it("确认后若上传已在进行，丢掉新授权的本机根", async () => {
    vi.mocked(startImportToCloudJob).mockReturnValue(false);
    vi.mocked(pickLocalFolderRoot).mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "MyRepo" },
    });
    const removeRoot = vi.fn();
    (
      window as unknown as { fsApi?: { removeRoot?: ReturnType<typeof vi.fn> } }
    ).fsApi = { removeRoot };
    const openImport = vi.spyOn(
      useFoldersStore.getState(),
      "openImportToCloud",
    );
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: "新建或加入…" }));
    await act(async () => {
      fireEvent.click(menu.getByRole("button", { name: "从本机加入" }));
    });
    fireEvent.click(menu.getByRole("button", { name: /复制到云上当新家/ }));
    expect(openImport).not.toHaveBeenCalled();
    expectImportConfirm("MyRepo");
    confirmCloudCopyStart();
    expect(removeRoot).toHaveBeenCalledWith("root-1");
  });

  it("点云文件夹仍立刻选中，不进三选", () => {
    grouped.value = {
      folders: [cloudFolder("f-cloud", "云文件夹")],
      conversations: [],
    };
    const menu = within(openPicker());
    fireEvent.click(menu.getByRole("button", { name: /云文件夹/ }));
    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "f-cloud",
    });
    expect(menu.queryByText("直接改这个文件夹")).toBeNull();
  });

  it("成员桌出现在与我共享分区", () => {
    grouped.value = {
      folders: [cloudFolder("f-own", "我的项目")],
      conversations: [],
    };
    sharedWithMe.value = [
      {
        ...cloudFolder("f-shared", "队友桌"),
        myRole: "editor",
      },
    ];
    const menu = within(openPicker());
    expect(menu.getByText("我的文件")).toBeTruthy();
    expect(menu.getByText("与我共享")).toBeTruthy();
    expect(menu.getByRole("button", { name: /我的项目/ })).toBeTruthy();
    expect(menu.getByRole("button", { name: /队友桌/ })).toBeTruthy();
    expect(
      within(menu.getByRole("button", { name: /队友桌/ })).getByTitle(
        "与我共享",
      ),
    ).toBeTruthy();
  });
});

describe("BoundChip · 借用原件", () => {
  function renderBound() {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ComposerWorkspaceChip conversationId="c1" />
        </TooltipProvider>
      </MemoryRouter>,
    );
  }

  it("读到 folderId 且借用中时标明原件尚未改动", () => {
    grouped.value = {
      folders: [],
      conversations: [
        { id: "c1", folderId: "f1", updatedAt: "2026-01-01T00:00:00Z" },
      ],
    };
    set("f1", {
      rootId: "root-1",
      originalName: "Repo",
      promoted: false,
    });
    expect(isBorrowActive("f1")).toBe(true);
    renderBound();
    expect(screen.getByText("原件尚未改动")).toBeTruthy();
    expect(screen.getByLabelText(/原件尚未改动/)).toBeTruthy();
  });

  it("无 folderId 或已升格不标", () => {
    grouped.value = {
      folders: [],
      conversations: [
        { id: "c1", folderId: null, updatedAt: "2026-01-01T00:00:00Z" },
      ],
    };
    set("f1", {
      rootId: "root-1",
      originalName: "Repo",
      promoted: false,
    });
    renderBound();
    expect(screen.queryByText("原件尚未改动")).toBeNull();

    cleanup();
    grouped.value = {
      folders: [],
      conversations: [
        { id: "c1", folderId: "f1", updatedAt: "2026-01-01T00:00:00Z" },
      ],
    };
    set("f1", {
      rootId: "root-1",
      originalName: "Repo",
      promoted: true,
    });
    renderBound();
    expect(screen.queryByText("原件尚未改动")).toBeNull();
  });
});

describe("folderLocationHint · 通道图标与祖先路径", () => {
  it("顶层云/本机行无副标题，用图标区分", () => {
    grouped.value = {
      folders: [
        cloudFolder("f-cloud", "AgentCore"),
        localFolder("f-board", "白板", "白板"),
      ],
      conversations: [],
    };
    const menu = within(openPicker());

    const cloudRow = menu.getByRole("button", { name: /^AgentCore$/ });
    expect(within(cloudRow).getByTitle("我的文件")).toBeTruthy();
    expect(within(cloudRow).queryByText("我的文件")).toBeNull();

    const localRow = menu.getByRole("button", { name: /^白板$/ });
    expect(within(localRow).getByTitle("本机文件夹")).toBeTruthy();
    expect(within(localRow).queryByText("本机文件夹")).toBeNull();
    expect(menu.queryByText("本机 · 白板")).toBeNull();
  });

  it("嵌套本机 subpath 只留文件夹之上的那段，不加「本机 ·」", () => {
    grouped.value = {
      folders: [localFolder("f-web", "web", "apps/web")],
      conversations: [],
    };
    const menu = within(openPicker());

    expect(menu.getByText("apps")).toBeTruthy();
    expect(menu.queryByText("本机 · apps")).toBeNull();
    expect(menu.queryByText("本机文件夹")).toBeNull();
  });

  it("嵌套云行只写祖先路径，不加「我的文件 ·」", () => {
    grouped.value = {
      folders: [cloudFolder("f-icon", "图标", "设计")],
      conversations: [],
    };
    const menu = within(openPicker());

    expect(menu.getByText("图标")).toBeTruthy();
    expect(menu.getByText("设计")).toBeTruthy();
    expect(menu.queryByText("我的文件 · 设计")).toBeNull();
    expect(
      within(menu.getByRole("button", { name: /图标/ })).queryByText(
        "我的文件",
      ),
    ).toBeNull();
  });

  it("没有 subpath 的本机行不写通道副标题", () => {
    grouped.value = {
      folders: [localFolder("f-repo", "MyRepo", null)],
      conversations: [],
    };
    const menu = within(openPicker());

    expect(menu.getByText("MyRepo")).toBeTruthy();
    expect(menu.queryByText("本机文件夹")).toBeNull();
    expect(
      within(menu.getByRole("button", { name: /^MyRepo$/ })).getByTitle(
        "本机文件夹",
      ),
    ).toBeTruthy();
  });
});
