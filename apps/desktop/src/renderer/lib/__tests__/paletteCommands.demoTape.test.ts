import { describe, expect, it, vi } from "vitest";
import { buildPaletteCommands, commandMatches } from "../paletteCommands";

vi.mock("@/services/demoTape", () => ({
  prepareDemoTapeAndOpen: vi.fn(),
  startDemoTapeAndOpen: vi.fn(),
}));

vi.mock("@/lib/newConversation", () => ({
  startNewConversation: vi.fn(),
}));

vi.mock("@/services/conversations", () => ({
  exportConversation: vi.fn(),
}));

vi.mock("@/services/terminalActions", () => ({
  openCurrentConversationTerminal: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock("@/lib/capabilities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../capabilities")>();
  return { ...actual, hasLocalFiles: vi.fn(() => false) };
});

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));

vi.mock("@/lib/openLocalFolder", () => ({
  pickAndOpenLocalFolder: vi.fn(),
}));

vi.mock("@/lib/composerChannelPreference", () => ({
  setComposerChannelPreference: vi.fn(),
}));

vi.mock("@/lib/bindLocalFolder", () => ({
  pickAndBindLocalFolder: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: {
    getState: vi.fn(() => ({ currentConversationId: null })),
  },
}));

const baseCtx = {
  navigate: vi.fn(),
  theme: "system" as const,
  sidebarCollapsed: false,
  openBookmarksInPalette: vi.fn(),
};

describe("paletteCommands narrow restriction", () => {
  it("hides toolbox and conversation admin when restrictNarrow", () => {
    const cmds = buildPaletteCommands({
      ...baseCtx,
      restrictNarrow: true,
      forceLightTheme: true,
    });
    expect(cmds.some((c) => c.id === "nav-toolbox")).toBe(false);
    expect(cmds.some((c) => c.id === "nav-whiteboard")).toBe(false);
    expect(cmds.some((c) => c.id === "nav-conversations")).toBe(false);
    expect(cmds.some((c) => c.id === "theme-dark")).toBe(false);
    expect(cmds.some((c) => c.id === "nav-files")).toBe(true);
    expect(cmds.some((c) => c.id === "nav-settings")).toBe(true);
    expect(cmds.some((c) => c.id === "toggle-diagnostic-mode")).toBe(false);
  });
});

describe("paletteCommands demo tape gate", () => {
  it("hides demo-tape commands when catalog is empty", () => {
    const cmds = buildPaletteCommands(baseCtx);
    expect(cmds.some((c) => c.id.startsWith("demo-tape-"))).toBe(false);
    expect(cmds.some((c) => c.id === "toggle-diagnostic-mode")).toBe(false);
  });

  it("injects prepare + autostart commands when server catalog is present", () => {
    const cmds = buildPaletteCommands({
      ...baseCtx,
      demoTapes: [
        {
          id: "lv-molihua-trademark",
          title: "LV诉茉莉奶白商标侵权案",
          user_prompt: "搜索下…",
          turn_count: 1,
        },
      ],
    });
    const prepare = cmds.find((c) => c.id === "demo-tape-lv-molihua-trademark");
    const autostart = cmds.find(
      (c) => c.id === "demo-tape-lv-molihua-trademark-autostart",
    );
    expect(prepare).toBeTruthy();
    expect(autostart).toBeTruthy();
    if (!prepare || !autostart) return;
    expect(prepare.title).toContain("演示回放");
    expect(prepare.title).not.toContain("立即开播");
    expect(prepare.hint).toContain("准备");
    expect(autostart.title).toContain("立即开播");
    expect(autostart.hint).toContain("一键");
    expect(commandMatches(prepare, "演示回放")).toBe(true);
    expect(commandMatches(prepare, "huifang")).toBe(true);
    expect(commandMatches(autostart, "立即开播")).toBe(true);
  });
});

describe("paletteCommands · 前往发现性", () => {
  it("includes 白板 /whiteboard and excludes /explore placeholder", () => {
    const cmds = buildPaletteCommands(baseCtx);
    const board = cmds.find((c) => c.id === "nav-whiteboard");
    expect(board).toBeTruthy();
    expect(board?.title).toBe("白板");
    expect(board?.category).toBe("前往");
    if (!board) return;
    expect(commandMatches(board, "baiban")).toBe(true);

    board?.run();
    expect(baseCtx.navigate).toHaveBeenCalledWith("/whiteboard");

    expect(cmds.some((c) => c.id.includes("explore"))).toBe(false);
    expect(
      cmds.some(
        (c) =>
          c.title.includes("探索") ||
          (c.keywords ?? []).some((k) => k.includes("explore")),
      ),
    ).toBe(false);
  });
});

describe("paletteCommands · 设置深链", () => {
  it("renames 外观 to 通用 but still answers the old query", () => {
    const cmds = buildPaletteCommands(baseCtx);
    const general = cmds.find((c) => c.id === "nav-settings-general");
    expect(general).toBeTruthy();
    if (!general) return;
    expect(general.title).toBe("设置 · 通用");

    general.run();
    expect(baseCtx.navigate).toHaveBeenCalledWith("/more/general");

    expect(commandMatches(general, "通用")).toBe(true);
    expect(commandMatches(general, "外观")).toBe(true);
    expect(commandMatches(general, "appearance")).toBe(true);
    expect(commandMatches(general, "theme")).toBe(true);

    expect(cmds.some((c) => c.id === "nav-settings-appearance")).toBe(false);
  });
});

describe("paletteCommands · 区外只读授权", () => {
  it("hides grant command without local FS", async () => {
    const { hasLocalFiles } = await import("../capabilities");
    vi.mocked(hasLocalFiles).mockReturnValue(false);
    const cmds = buildPaletteCommands(baseCtx);
    expect(cmds.some((c) => c.id === "grant-readonly-folder")).toBe(false);
    expect(cmds.some((c) => c.id === "import-to-cloud")).toBe(false);
    expect(cmds.some((c) => c.id === "borrow-to-cloud")).toBe(false);
    expect(cmds.some((c) => c.id === "connect-git")).toBe(false);
    expect(cmds.some((c) => c.id === "open-local-project")).toBe(false);
    expect(cmds.some((c) => c.id === "bind-local-folder")).toBe(false);
    expect(cmds.some((c) => c.id === "new-local-conversation")).toBe(false);
  });

  it("injects grant command on desktop FS (hint only — no blank picker)", async () => {
    const { hasLocalFiles } = await import("../capabilities");
    const { notifyError } = await import("@/lib/toast");
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    const cmds = buildPaletteCommands(baseCtx);
    const grant = cmds.find((c) => c.id === "grant-readonly-folder");
    expect(grant).toBeTruthy();
    expect(grant?.title).toContain("授权本机目录");
    if (!grant) return;
    expect(commandMatches(grant, "zhuomian")).toBe(true);
    grant.run();
    expect(notifyError).toHaveBeenCalledWith(
      "请在对话中说明要授权的本机目录（命令面板不再打开系统选文件夹）",
    );
  });

  it("injects connect-git, import-to-cloud, and 打开本机文件夹 on desktop FS", async () => {
    const { hasLocalFiles } = await import("../capabilities");
    const { useFoldersStore } = await import("@/stores/folders");
    const { pickAndOpenLocalFolder } = await import("@/lib/openLocalFolder");
    const { setComposerChannelPreference } = await import(
      "@/lib/composerChannelPreference"
    );
    const openConnectGit = vi.spyOn(
      useFoldersStore.getState(),
      "openConnectGit",
    );
    const openImportToCloud = vi.spyOn(
      useFoldersStore.getState(),
      "openImportToCloud",
    );
    const openBorrowToCloud = vi.spyOn(
      useFoldersStore.getState(),
      "openBorrowToCloud",
    );
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    const cmds = buildPaletteCommands(baseCtx);
    expect(cmds.some((c) => c.id === "bind-local-folder")).toBe(false);
    expect(cmds.some((c) => c.id === "new-local-conversation")).toBe(false);

    const connectCmd = cmds.find((c) => c.id === "connect-git");
    expect(connectCmd?.title).toBe("从 Git 克隆");
    expect(connectCmd?.hint).toContain("推荐");
    if (!connectCmd) return;
    expect(commandMatches(connectCmd, "kelong")).toBe(true);
    connectCmd.run();
    expect(openConnectGit).toHaveBeenCalled();
    expect(setComposerChannelPreference).toHaveBeenCalledWith("cloud");

    const importCmd = cmds.find((c) => c.id === "import-to-cloud");
    expect(importCmd?.title).toBe("导入本机文件夹到「我的文件」");
    expect(importCmd?.hint).toContain("推荐");
    if (!importCmd) return;
    expect(commandMatches(importCmd, "daoru")).toBe(true);
    importCmd.run();
    expect(openImportToCloud).toHaveBeenCalled();

    const borrowCmd = cmds.find((c) => c.id === "borrow-to-cloud");
    expect(borrowCmd?.title).toBe("云上做完再写入");
    expect(borrowCmd?.hint).toBeTruthy();
    expect(borrowCmd?.hint).not.toContain("推荐");
    if (!borrowCmd) return;
    expect(commandMatches(borrowCmd, "yunshang")).toBe(true);
    borrowCmd.run();
    expect(openBorrowToCloud).toHaveBeenCalled();
    expect(setComposerChannelPreference).toHaveBeenCalledWith("cloud");

    const localCmd = cmds.find((c) => c.id === "open-local-project");
    expect(localCmd?.title).toBe("打开本机文件夹");
    expect(localCmd?.hint).toContain("≠离线");
    if (!localCmd) return;
    expect(commandMatches(localCmd, "benji")).toBe(true);
    localCmd.run();
    expect(setComposerChannelPreference).toHaveBeenCalledWith(
      "local_traditional",
    );
    expect(pickAndOpenLocalFolder).toHaveBeenCalledWith(baseCtx.navigate);
  });
});
