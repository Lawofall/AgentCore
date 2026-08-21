import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/browserSessions", () => ({
  listBrowserSessions: vi.fn().mockResolvedValue({
    sessions: [],
    activeSessionId: null,
  }),
  closeBrowserSession: vi.fn(),
}));

const detachLocalBrowserHost = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/detachLocalBrowserHost", () => ({
  detachLocalBrowserHost: (...args: unknown[]) =>
    detachLocalBrowserHost(...args),
}));

import { listBrowserSessions } from "@/services/browserSessions";
import { useBrowserSessionsStore } from "../browserSessions";
import { useConversationStore } from "../conversation";
import { type ExecutionPlan, useExecutionStore } from "../execution";
import {
  CHANGES_TAB_ID,
  type DetailTab,
  SIDE_PANEL_DEFAULT_WIDTH,
  SIDE_PANEL_MAX_FLOATS,
  SIDE_PANEL_MAX_TABS,
  SIDE_PANEL_MIN_WIDTH,
  TEAM_BROWSER_TAB_ID,
  TEAM_TERMINAL_TAB_ID,
  WORKSPACE_TAB_ID,
  browserDismissKey,
  contentDetailTabId,
  dismissFocusedFloat,
  entryFileTab,
  fileTabId,
  runDetailTabId,
  sidePanelFocusTabId,
  sidePanelMaxWidth,
  simpleTurnDetailTabId,
  terminalDismissKey,
  useSidePanelStore,
} from "../sidePanel";
import { nextDockActiveAfterFloat } from "../sidePanel/float";

const listMock = vi.mocked(listBrowserSessions);

const panel = () => useSidePanelStore.getState();
const exec = () => useExecutionStore.getState();
// Each turn's execution + focus lives in its own message slot (§9.3); this suite
// drives one message.
const MID = "msg-1";
const tabId = (runId: string) => runDetailTabId(MID, runId);

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "分析对比 React 和 Vue",
  agents: [{ id: "agent-1", role: "研究员" }],
  runs: [{ id: "run-1", agentId: "agent-1", task: "研究", dependsOn: [] }],
};

const runDetail = (runId: string): DetailTab => ({
  kind: "run",
  id: runDetailTabId(MID, runId),
  title: runId,
  messageId: MID,
  runId,
});

beforeEach(() => {
  // The store hydrates from localStorage at import; pin a known baseline so each
  // test starts from a closed, default-width panel sitting on the 工作区 home tab.
  useSidePanelStore.setState({
    open: false,
    width: 400,
    tabs: [],
    activeTabId: WORKSPACE_TAB_ID,
    floats: [],
    focusSurface: { type: "dock" },
    changesFocusMessageId: null,
    dismissedContexts: new Set(),
    pendingBadge: 0,
  });
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({ currentConversationId: "conv-test" });
  useBrowserSessionsStore.setState({ pages: [], activePageId: null });
  listMock.mockReset();
  listMock.mockResolvedValue({ sessions: [], activeSessionId: null });
  detachLocalBrowserHost.mockClear();
});

describe("draft cannot reveal side panel", () => {
  beforeEach(() => {
    useConversationStore.setState({ currentConversationId: null });
  });

  it("openPanel / showWorkspace / toggle(open) are no-ops without a conversation", () => {
    panel().openPanel();
    expect(panel().open).toBe(false);
    panel().showWorkspace();
    expect(panel().open).toBe(false);
    panel().togglePanel();
    expect(panel().open).toBe(false);
  });

  it("openTab with default reveal does not open the dock on draft", () => {
    panel().openTab(runDetail("run-1"));
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);
    expect(panel().open).toBe(false);
  });

  it("togglePanel can still close if the dock was somehow open", () => {
    useSidePanelStore.setState({ open: true });
    panel().togglePanel();
    expect(panel().open).toBe(false);
  });
});

describe("setWidth", () => {
  it("clamps below the minimum", () => {
    panel().setWidth(100);
    expect(panel().width).toBe(SIDE_PANEL_MIN_WIDTH);
  });

  it("clamps above the dynamic (window-relative) maximum", () => {
    panel().setWidth(9999);
    expect(panel().width).toBe(sidePanelMaxWidth());
  });

  it("rounds and keeps an in-range value", () => {
    panel().setWidth(421.6);
    expect(panel().width).toBe(422);
  });
});

describe("reclampWidth（窗口缩小后收敛到新上限）", () => {
  it("collapses an over-wide width down to the current max", () => {
    // 直接注入越界宽度（绕过 setWidth 的 clamp），模拟「窗口缩小后旧宽度已超新上限」。
    useSidePanelStore.setState({ width: sidePanelMaxWidth() + 400 });
    panel().reclampWidth();
    expect(panel().width).toBe(sidePanelMaxWidth());
  });

  it("is a no-op while the current width still fits", () => {
    const before = panel().width;
    panel().reclampWidth();
    expect(panel().width).toBe(before);
  });
});

describe("cycleWidth（双击手柄在三档间循环）", () => {
  it("cycles 默认 → 最大 → 最小 → 默认", () => {
    panel().setWidth(SIDE_PANEL_DEFAULT_WIDTH);
    panel().cycleWidth();
    expect(panel().width).toBe(sidePanelMaxWidth());
    panel().cycleWidth();
    expect(panel().width).toBe(SIDE_PANEL_MIN_WIDTH);
    panel().cycleWidth();
    expect(panel().width).toBe(SIDE_PANEL_DEFAULT_WIDTH);
  });
});

describe("openTab", () => {
  it("opens the panel, appends and activates the run tab", () => {
    panel().openTab(runDetail("run-1"));
    expect(panel().open).toBe(true);
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });

  it("dedups by id and updates the title in place", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab({ ...runDetail("run-1"), title: "研究员" });
    expect(panel().tabs).toHaveLength(1);
    expect(panel().tabs[0].title).toBe("研究员");
  });

  it("activate:false keeps the current active tab", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"), { activate: false });
    expect(panel().tabs).toHaveLength(2);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });

  it("caps the strip at the maximum, dropping the oldest", () => {
    for (let i = 0; i < SIDE_PANEL_MAX_TABS + 2; i++) {
      panel().openTab(runDetail(`run-${i}`));
    }
    expect(panel().tabs).toHaveLength(SIDE_PANEL_MAX_TABS);
    // run-0 and run-1 were pushed out; run-2 is now the oldest.
    expect(panel().tabs[0].id).toBe(tabId("run-2"));
  });
});

describe("closeTab", () => {
  it("falls back to the neighbour run tab (next, else previous)", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().openTab(runDetail("run-3"));
    panel().setActiveTab(tabId("run-2"));
    panel().closeTab(tabId("run-2"));
    // Removing the active middle tab lands on its successor (run-3).
    expect(panel().tabs.map((t) => t.id)).toEqual([
      tabId("run-1"),
      tabId("run-3"),
    ]);
    expect(panel().activeTabId).toBe(tabId("run-3"));
  });

  it("falls back to the 工作区 home when the last run tab is closed", () => {
    panel().openTab(runDetail("run-1"));
    panel().closeTab(tabId("run-1"));
    expect(panel().tabs).toHaveLength(0);
    // The home tab is always there, so the panel stays open.
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("falls back to the 工作区 home when the last run tab is closed inside a debate room", () => {
    panel().openTab(runDetail("run-1"));
    panel().closeTab(tabId("run-1"));
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("keeps the active tab when a different tab is closed", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().setActiveTab(tabId("run-2"));
    panel().closeTab(tabId("run-1"));
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-2")]);
    expect(panel().activeTabId).toBe(tabId("run-2"));
  });
});

describe("reorderContentTabs", () => {
  it("reorders matching tabs at their original index slots", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().openTab(runDetail("run-3"));
    panel().reorderContentTabs([
      tabId("run-3"),
      tabId("run-1"),
      tabId("run-2"),
    ]);
    expect(panel().tabs.map((t) => t.id)).toEqual([
      tabId("run-3"),
      tabId("run-1"),
      tabId("run-2"),
    ]);
  });

  it("reorders a contiguous subset while keeping outsiders fixed", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().openTab(runDetail("run-3"));
    panel().openTab(runDetail("run-4"));
    // Only reorder the middle pair: slots of run-2/run-3 become run-3/run-2.
    panel().reorderContentTabs([tabId("run-3"), tabId("run-2")]);
    expect(panel().tabs.map((t) => t.id)).toEqual([
      tabId("run-1"),
      tabId("run-3"),
      tabId("run-2"),
      tabId("run-4"),
    ]);
  });

  it("keeps floating tabs' relative slots when reordering docked ids", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().openTab(runDetail("run-3"));
    panel().floatTab(tabId("run-2"));
    // Reorder docked run-1 / run-3; floating run-2 stays in its index slot.
    panel().reorderContentTabs([tabId("run-3"), tabId("run-1")]);
    expect(panel().tabs.map((t) => t.id)).toEqual([
      tabId("run-3"),
      tabId("run-2"),
      tabId("run-1"),
    ]);
    expect(panel().isFloating(tabId("run-2"))).toBe(true);
  });

  it("is a no-op for unknown / duplicate / length-mismatch ids", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    const before = panel().tabs.map((t) => t.id);

    panel().reorderContentTabs([tabId("run-2"), "ghost"]);
    expect(panel().tabs.map((t) => t.id)).toEqual(before);

    panel().reorderContentTabs([tabId("run-1"), tabId("run-1")]);
    expect(panel().tabs.map((t) => t.id)).toEqual(before);

    // orderedIds longer than matching tabs in state
    panel().reorderContentTabs([
      tabId("run-1"),
      tabId("run-2"),
      tabId("run-3"),
    ]);
    expect(panel().tabs.map((t) => t.id)).toEqual(before);
  });
});

describe("togglePanel", () => {
  it("opens, then closes (keeping the active tab)", () => {
    panel().showRunDetail(MID, "run-1");
    panel().togglePanel();
    expect(panel().open).toBe(false);
    expect(panel().activeTabId).toBe(tabId("run-1"));
    panel().togglePanel();
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });
});

describe("openPanel", () => {
  it("reveals the panel without changing the active tab", () => {
    // openPanel reveals the dock without yanking the user off a run-detail tab
    // they're reading.
    panel().openTab(runDetail("run-1"));
    panel().togglePanel(); // close it, keeping run-1 active
    expect(panel().open).toBe(false);
    panel().openPanel();
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });
});

describe("showWorkspace", () => {
  it("reveals the panel on the 工作区 home tab", () => {
    panel().showWorkspace();
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("returns to the home tab from an active run tab without dropping it", () => {
    panel().openTab(runDetail("run-1"));
    panel().showWorkspace();
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
    // The run tab is preserved in the strip, just no longer active.
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);
  });
});

describe("showRunDetail", () => {
  it("pins a run, reveals it, and activates its tab", () => {
    exec().startExecution(plan, MID);
    panel().showWorkspace();
    panel().showRunDetail(MID, "run-1", "研究员");
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(tabId("run-1"));
    expect(panel().tabs[0].title).toBe("研究员");
  });

  it("reuses one tab for a revision chain and switches runId in place", () => {
    exec().startExecution(plan, MID);
    exec().recordFrames(
      [
        {
          t: 1,
          kind: "run_started",
          agentId: "agent-1",
          runId: "run-1",
          parentRunId: null,
          runKind: "agent",
          continuesRunId: null,
        },
        {
          t: 2,
          kind: "run_completed",
          runId: "run-1",
          agentId: "agent-1",
          outputSummary: "done",
          durationMs: 1,
        },
        {
          t: 3,
          kind: "run_started",
          agentId: "run-1_rev1",
          runId: "run-1_rev1",
          parentRunId: null,
          runKind: "agent",
          continuesRunId: "run-1",
        },
        {
          t: 4,
          kind: "run_started",
          agentId: "run-1_rev2",
          runId: "run-1_rev2",
          parentRunId: null,
          runKind: "agent",
          continuesRunId: "run-1",
        },
      ],
      MID,
    );

    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showRunDetail(MID, "run-1_rev1", "研究员");
    panel().showRunDetail(MID, "run-1_rev2", "研究员");

    expect(panel().tabs).toHaveLength(1);
    expect(panel().tabs[0].id).toBe(tabId("run-1"));
    expect(panel().activeTabId).toBe(tabId("run-1"));
    const tab = panel().tabs[0];
    expect(tab.kind).toBe("run");
    if (tab.kind === "run") {
      expect(tab.runId).toBe("run-1_rev2");
    }
  });

  it("keeps separate tabs for unrelated (non-revision) runs", () => {
    const multi: ExecutionPlan = {
      ...plan,
      agents: [...plan.agents, { id: "agent-2", role: "评论员" }],
      runs: [
        ...plan.runs,
        { id: "run-2", agentId: "agent-2", task: "评论", dependsOn: [] },
      ],
    };
    exec().startExecution(multi, MID);
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showRunDetail(MID, "run-2", "评论员");
    expect(panel().tabs.map((t) => t.id)).toEqual([
      tabId("run-1"),
      tabId("run-2"),
    ]);
  });
});

describe("showContentDetail", () => {
  it("pins an endpoint bubble as a content tab, reveals + activates it", () => {
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    const id = contentDetailTabId(MID, "answer-msg");
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(id);
    const tab = panel().tabs[0];
    expect(tab.kind).toBe("content");
    expect(tab.title).toBe("最终回答");
    // The content tab carries the bubble to render + which endpoint it is (drives
    // the tab icon), not a runId.
    if (tab.kind === "content") {
      expect(tab.contentMessageId).toBe("answer-msg");
      expect(tab.endpoint).toBe("answer");
    }
  });

  it("coexists with run tabs and dedups by its own id", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    // One run tab + one content tab; the re-open dedups rather than appends.
    expect(panel().tabs).toHaveLength(2);
    expect(panel().tabs.map((t) => t.kind)).toEqual(["run", "content"]);
  });
});

describe("showSimpleTurnDetail", () => {
  it("pins a simple-turn Q&A tab, reveals + activates it", () => {
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1", "直接回答");
    const id = simpleTurnDetailTabId(MID);
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(id);
    const tab = panel().tabs[0];
    expect(tab.kind).toBe("simple-turn");
    expect(tab.title).toBe("直接回答");
    if (tab.kind === "simple-turn") {
      expect(tab.promptMessageId).toBe("user-1");
      expect(tab.answerMessageId).toBe("asst-1");
    }
  });

  it("defaults the title to 对话 and dedups by turn id", () => {
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1");
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1", "更新标题");
    expect(panel().tabs).toHaveLength(1);
    expect(panel().tabs[0].title).toBe("更新标题");
    expect(panel().tabs[0].id).toBe(simpleTurnDetailTabId(MID));
  });

  it("coexists with run tabs", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1");
    expect(panel().tabs.map((t) => t.kind)).toEqual(["run", "simple-turn"]);
  });
});

describe("closeContentTabs", () => {
  it("drops content tabs but keeps run tabs, re-activating a survivor", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    // The content tab is active; closing content tabs falls back to the run tab.
    panel().closeContentTabs();
    expect(panel().tabs.map((t) => t.kind)).toEqual(["run"]);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });

  it("also drops simple-turn Q&A tabs (same reading-context cleanup)", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1");
    panel().closeContentTabs();
    expect(panel().tabs.map((t) => t.kind)).toEqual(["run"]);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });

  it("falls back to the 工作区 home when no detail tab survives", () => {
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    panel().closeContentTabs();
    expect(panel().tabs).toHaveLength(0);
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("is a no-op when there are no content tabs", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().closeContentTabs();
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);
    expect(panel().activeTabId).toBe(tabId("run-1"));
  });
});

describe("closeConversationScopedTabs（切对话卸作用域内容 tab）", () => {
  it("unloads run / file / content / simple-turn; keeps terminal / browser shells", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showFile("src/a.ts", "a.ts");
    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    panel().showSimpleTurnDetail(MID, "user-1", "asst-1");
    panel().openTerminalTab();
    panel().showBrowser();
    panel().showChanges(MID);
    useSidePanelStore.setState({ open: true, width: 480 });

    panel().closeConversationScopedTabs();

    expect(
      panel()
        .tabs.map((t) => t.kind)
        .sort(),
    ).toEqual(["browser", "terminal"]);
    expect(
      panel()
        .tabs.map((t) => t.id)
        .sort(),
    ).toEqual([TEAM_BROWSER_TAB_ID, TEAM_TERMINAL_TAB_ID]);
    // Fixed tabs are not in `tabs`; active 改动 must survive.
    expect(panel().activeTabId).toBe(CHANGES_TAB_ID);
    expect(panel().open).toBe(true);
    expect(panel().width).toBe(480);
  });

  it("falls back to a surviving shell when the active scoped tab is dropped", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().openTerminalTab();
    expect(panel().activeTabId).toBe(TEAM_TERMINAL_TAB_ID);
    panel().setActiveTab(tabId("run-1"));
    panel().closeConversationScopedTabs();
    expect(panel().tabs.map((t) => t.id)).toEqual([TEAM_TERMINAL_TAB_ID]);
    expect(panel().activeTabId).toBe(TEAM_TERMINAL_TAB_ID);
  });

  it("falls back to the 工作区 home when no shell survives", () => {
    panel().showRunDetail(MID, "run-1", "研究员");
    panel().showFile("src/a.ts", "a.ts");
    panel().closeConversationScopedTabs();
    expect(panel().tabs).toHaveLength(0);
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("strips float entries for unloaded tabs without touching clearFloats semantics", () => {
    panel().openTab(runDetail("run-1"));
    panel().showFile("src/a.ts", "a.ts");
    panel().floatTab(tabId("run-1"));
    panel().floatTab(fileTabId("src/a.ts"));
    panel().floatTab(WORKSPACE_TAB_ID);
    panel().openTerminalTab({ activate: false });
    panel().closeConversationScopedTabs();
    // Scoped floats gone; workspace float left for clearFloats.
    expect(panel().tabs.map((t) => t.id)).toEqual([TEAM_TERMINAL_TAB_ID]);
    expect(panel().floats.map((f) => f.tabId)).toEqual([WORKSPACE_TAB_ID]);
    expect(panel().focusSurface).toEqual({
      type: "float",
      tabId: WORKSPACE_TAB_ID,
    });
  });

  it("is a no-op when only shells / nothing scoped is open", () => {
    panel().openTerminalTab();
    panel().showBrowser();
    const before = panel().tabs.map((t) => t.id);
    panel().closeConversationScopedTabs();
    expect(panel().tabs.map((t) => t.id)).toEqual(before);
    expect(panel().activeTabId).toBe(TEAM_BROWSER_TAB_ID);
  });
});

describe("showChanges / showFile / openTerminalTab（方案 B 顶栏 IA）", () => {
  it("showChanges reveals the panel on the 改动 tab and stores focus", () => {
    panel().showChanges(MID);
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(CHANGES_TAB_ID);
    expect(panel().changesFocusMessageId).toBe(MID);
  });

  it("clearChangesFocus drops deep-link focus only", () => {
    panel().showChanges(MID);
    panel().clearChangesFocus();
    expect(panel().changesFocusMessageId).toBeNull();
    expect(panel().activeTabId).toBe(CHANGES_TAB_ID);
  });

  it("showFile opens a File content tab (path reference) instead of workspace swap", () => {
    panel().showFile("src/a.ts", "a.ts");
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(fileTabId("src/a.ts"));
    expect(panel().tabs[0]).toMatchObject({
      kind: "file",
      path: "src/a.ts",
      name: "a.ts",
    });
  });

  it("showFile with workspaceId scopes tab identity to that desk", () => {
    panel().showFile("notes.md", "notes.md", "folder:landed");
    panel().showFile("notes.md", "notes.md", "folder:other");
    panel().showFile("notes.md", "notes.md");
    const fileTabs = panel().tabs.filter((t) => t.kind === "file");
    expect(fileTabs).toHaveLength(3);
    expect(fileTabs.map((t) => t.id)).toEqual([
      fileTabId("notes.md", "folder:landed"),
      fileTabId("notes.md", "folder:other"),
      fileTabId("notes.md"),
    ]);
    expect(fileTabs[0]).toMatchObject({
      kind: "file",
      path: "notes.md",
      workspaceId: "folder:landed",
    });
  });

  it("entry channel File tab identity does not collide with disk path", () => {
    const path = "project/f1/profile";
    panel().showFile(path, "画像.md");
    panel().openTab(entryFileTab({ channel: "memory", path, name: "画像.md" }));
    const fileTabs = panel().tabs.filter((t) => t.kind === "file");
    expect(fileTabs).toHaveLength(2);
    expect(fileTabs.map((t) => t.id)).toEqual([
      fileTabId(path),
      fileTabId(path, null, "memory"),
    ]);
    expect(fileTabs[1]).toMatchObject({
      kind: "file",
      channel: "memory",
      path,
    });
    expect(fileTabs[0].id).not.toBe(fileTabs[1].id);
  });

  it("openTab same id replaces File tab title (entry rename)", () => {
    const tab = entryFileTab({
      channel: "document",
      path: "doc-1",
      name: "旧.md",
    });
    panel().openTab(tab);
    panel().openTab({ ...tab, title: "新.md", name: "新.md" });
    const fileTabs = panel().tabs.filter((t) => t.kind === "file");
    expect(fileTabs).toHaveLength(1);
    expect(fileTabs[0]).toMatchObject({
      id: tab.id,
      title: "新.md",
      name: "新.md",
      channel: "document",
    });
  });

  it("openFileTab without path creates an untitled empty file tab", () => {
    panel().openFileTab();
    const tab = panel().tabs[0];
    expect(tab.kind).toBe("file");
    expect(tab.title).toBe("文件");
    if (tab.kind === "file") {
      expect(tab.path).toBe("");
    }
  });

  it("openTerminalTab dedups to a single terminal hub tab", () => {
    const a = panel().openTerminalTab();
    const b = panel().openTerminalTab();
    expect(a).toBe(TEAM_TERMINAL_TAB_ID);
    expect(b).toBe(TEAM_TERMINAL_TAB_ID);
    expect(panel().tabs.filter((t) => t.kind === "terminal")).toHaveLength(1);
    expect(panel().activeTabId).toBe(TEAM_TERMINAL_TAB_ID);
  });

  it("openTerminalTab collapses legacy multi-instance terminal tabs", () => {
    useSidePanelStore.setState({
      tabs: [
        {
          kind: "terminal",
          id: "terminal:t1",
          title: "终端",
          sessionId: "pty-a",
        },
        {
          kind: "terminal",
          id: "terminal:t2",
          title: "终端",
          sessionId: "pty-b",
        },
      ],
      activeTabId: "terminal:t2",
    });
    const id = panel().openTerminalTab({ activate: false, reveal: false });
    expect(id).toBe(TEAM_TERMINAL_TAB_ID);
    expect(panel().tabs.filter((t) => t.kind === "terminal")).toHaveLength(1);
    expect(panel().tabs[0]).toMatchObject({
      id: TEAM_TERMINAL_TAB_ID,
      sessionId: "pty-b",
    });
    expect(panel().activeTabId).toBe(TEAM_TERMINAL_TAB_ID);
  });

  it("bindTerminalSession updates the hub tab reference in place", () => {
    const id = panel().openTerminalTab();
    panel().bindTerminalSession(id, "pty-1", "终端 1");
    const tab = panel().tabs.find((t) => t.id === TEAM_TERMINAL_TAB_ID);
    expect(tab?.kind).toBe("terminal");
    if (tab?.kind === "terminal") {
      expect(tab.sessionId).toBe("pty-1");
      expect(tab.title).toBe("终端 1");
    }
  });

  it("closeTab on terminal hub dismisses auto-surface for this conversation", () => {
    useConversationStore.setState({ currentConversationId: "conv-term" });
    panel().openTerminalTab();
    panel().closeTab(TEAM_TERMINAL_TAB_ID);
    expect(panel().tabs.some((t) => t.kind === "terminal")).toBe(false);
    expect(
      panel().isAutoSurfaceDismissed(terminalDismissKey("conv-term")),
    ).toBe(true);
  });

  it("openTerminalTab clears a prior terminal dismiss", () => {
    useConversationStore.setState({ currentConversationId: "conv-term" });
    panel().dismissAutoSurface(terminalDismissKey("conv-term"));
    panel().openTerminalTab();
    expect(
      panel().isAutoSurfaceDismissed(terminalDismissKey("conv-term")),
    ).toBe(false);
  });

  it("clearTerminalPreferredSession drops matching hub binding", () => {
    const id = panel().openTerminalTab();
    panel().bindTerminalSession(id, "pty-1");
    panel().clearTerminalPreferredSession("pty-1");
    const tab = panel().tabs.find((t) => t.id === TEAM_TERMINAL_TAB_ID);
    expect(tab?.kind).toBe("terminal");
    if (tab?.kind === "terminal") {
      expect(tab.sessionId).toBeNull();
    }
  });
});

describe("showBrowser（浏览器壳 · 可关内容 tab）", () => {
  it("closeTab on browser hub dismisses auto-surface for this conversation", () => {
    useConversationStore.setState({ currentConversationId: "conv-browser" });
    panel().showBrowser();
    panel().closeTab(TEAM_BROWSER_TAB_ID);
    expect(panel().tabs.some((t) => t.kind === "browser")).toBe(false);
    expect(
      panel().isAutoSurfaceDismissed(browserDismissKey("conv-browser")),
    ).toBe(true);
  });

  it("showBrowser clears a prior browser dismiss", () => {
    useConversationStore.setState({ currentConversationId: "conv-browser" });
    panel().dismissAutoSurface(browserDismissKey("conv-browser"));
    panel().showBrowser();
    expect(
      panel().isAutoSurfaceDismissed(browserDismissKey("conv-browser")),
    ).toBe(false);
  });

  it("reveals the panel and opens/activates the browser content tab", () => {
    panel().showBrowser();
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(TEAM_BROWSER_TAB_ID);
    expect(panel().tabs[0]).toMatchObject({
      kind: "browser",
      id: TEAM_BROWSER_TAB_ID,
    });
  });

  it("dedups the browser tab on re-reveal", () => {
    panel().showBrowser();
    panel().showBrowser();
    expect(panel().tabs.filter((t) => t.kind === "browser")).toHaveLength(1);
    expect(panel().activeTabId).toBe(TEAM_BROWSER_TAB_ID);
  });

  it("leaves the browser tab in the background when a run tab is drilled after it", () => {
    panel().showBrowser();
    panel().showRunDetail(MID, "run-1", "研究员");
    expect(panel().activeTabId).toBe(tabId("run-1"));
    expect(panel().tabs.some((t) => t.id === TEAM_BROWSER_TAB_ID)).toBe(true);
  });

  it("clears pendingBadge on reveal (matches other reveal paths)", () => {
    panel().incrementPendingBadge();
    panel().showBrowser();
    expect(panel().pendingBadge).toBe(0);
  });

  it("creates a blank local page when none exist (after hydrate finds no server)", async () => {
    useConversationStore.setState({ currentConversationId: "conv-browser" });
    panel().showBrowser();
    await vi.waitFor(() => {
      expect(
        useBrowserSessionsStore.getState().pagesFor("conv-browser"),
      ).toHaveLength(1);
    });
    expect(
      useBrowserSessionsStore.getState().pagesFor("conv-browser")[0]?.title,
    ).toBe("新标签页");
    expect(listMock).toHaveBeenCalledWith("conv-browser");
  });

  it("activates hydrated server page instead of ensuring a blank", async () => {
    useConversationStore.setState({ currentConversationId: "conv-server" });
    listMock.mockResolvedValue({
      sessions: [
        {
          sessionId: "sess-1",
          conversationId: "conv-server",
          hostKind: "local",
          control: "agent",
          runId: null,
          createdAt: 1,
          lastUsed: 1,
          url: "https://example.com/",
          title: "Example",
        },
      ],
      activeSessionId: "sess-1",
    });
    panel().showBrowser();
    await vi.waitFor(() => {
      expect(useBrowserSessionsStore.getState().activePageId).toBe(
        "browser-server:sess-1",
      );
    });
    const pages = useBrowserSessionsStore.getState().pagesFor("conv-server");
    expect(pages.some((p) => p.serverSessionId === "sess-1")).toBe(true);
    expect(pages.every((p) => p.serverSessionId)).toBe(true); // no blank forced
  });
});

describe("auto-surface dismiss + pending badge", () => {
  it("clearAutoSurfaceDismiss removes a context", () => {
    panel().dismissAutoSurface("debate:msg-1");
    expect(panel().isAutoSurfaceDismissed("debate:msg-1")).toBe(true);
    panel().clearAutoSurfaceDismiss("debate:msg-1");
    expect(panel().isAutoSurfaceDismissed("debate:msg-1")).toBe(false);
  });

  it("incrementPendingBadge accumulates while panel stays closed", () => {
    panel().incrementPendingBadge();
    panel().incrementPendingBadge();
    expect(panel().pendingBadge).toBe(2);
  });

  it("clears pendingBadge when opening via showWorkspace / openPanel", () => {
    panel().incrementPendingBadge();
    panel().showWorkspace();
    expect(panel().pendingBadge).toBe(0);

    panel().incrementPendingBadge();
    panel().openPanel();
    expect(panel().pendingBadge).toBe(0);
  });

  it("clears pendingBadge when togglePanel opens the dock", () => {
    panel().incrementPendingBadge();
    panel().togglePanel();
    expect(panel().open).toBe(true);
    expect(panel().pendingBadge).toBe(0);
  });

  it("keeps pendingBadge when togglePanel closes the dock", () => {
    panel().showWorkspace();
    panel().incrementPendingBadge();
    panel().togglePanel();
    expect(panel().open).toBe(false);
    expect(panel().pendingBadge).toBe(1);
  });
});

describe("Local browser detach on dock / browser tab close", () => {
  it("closeTab(browser) calls hide before state change", () => {
    panel().showBrowser();
    panel().closeTab(TEAM_BROWSER_TAB_ID);
    expect(detachLocalBrowserHost).toHaveBeenCalledTimes(1);
    expect(panel().tabs.some((t) => t.kind === "browser")).toBe(false);
  });

  it("closePanel / togglePanel(关) call hide only when browser is still docked", () => {
    panel().openPanel();
    panel().closePanel();
    expect(detachLocalBrowserHost).not.toHaveBeenCalled();

    panel().showBrowser();
    detachLocalBrowserHost.mockClear();
    panel().closePanel();
    expect(detachLocalBrowserHost).toHaveBeenCalledTimes(1);

    panel().showBrowser();
    detachLocalBrowserHost.mockClear();
    panel().togglePanel(); // close
    expect(detachLocalBrowserHost).toHaveBeenCalledTimes(1);
  });
});

describe("应用内浮窗（§十 · Move / float·dock / 上限 8）", () => {
  it("nextDockActiveAfterFloat prefers 工作区 when it is still docked", () => {
    expect(
      nextDockActiveAfterFloat(
        { activeTabId: tabId("run-1"), tabs: [], floats: [] },
        tabId("run-1"),
      ),
    ).toBe(WORKSPACE_TAB_ID);
  });

  it("floatTab Moves a run out of the dock and focuses the float", () => {
    panel().openTab(runDetail("run-1"));
    expect(panel().floatTab(tabId("run-1"))).toBe(true);
    expect(panel().isFloating(tabId("run-1"))).toBe(true);
    expect(panel().floats).toHaveLength(1);
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);
    expect(panel().focusSurface).toEqual({
      type: "float",
      tabId: tabId("run-1"),
    });
    expect(sidePanelFocusTabId(panel())).toBe(tabId("run-1"));
    // Dock active leaves the floated tab (Move). 工作区仍停靠 → 回工作区，不优先改动。
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("dockTab pins a float back and activates it in the dock", () => {
    panel().openTab(runDetail("run-1"));
    panel().floatTab(tabId("run-1"));
    panel().closePanel();
    panel().dockTab(tabId("run-1"));
    expect(panel().isFloating(tabId("run-1"))).toBe(false);
    expect(panel().floats).toHaveLength(0);
    expect(panel().open).toBe(true);
    expect(panel().activeTabId).toBe(tabId("run-1"));
    expect(panel().focusSurface).toEqual({ type: "dock" });
  });

  it("rejects float for terminal / browser / content / simple-turn", () => {
    panel().openTerminalTab();
    expect(panel().floatTab(TEAM_TERMINAL_TAB_ID)).toBe(false);

    panel().showBrowser();
    expect(panel().floatTab(TEAM_BROWSER_TAB_ID)).toBe(false);

    panel().showContentDetail(MID, "answer-msg", "最终回答", "answer");
    expect(panel().floatTab(contentDetailTabId(MID, "answer-msg"))).toBe(false);

    panel().showSimpleTurnDetail(MID, "user-1", "asst-1");
    expect(panel().floatTab(simpleTurnDetailTabId(MID))).toBe(false);

    expect(panel().floats).toHaveLength(0);
  });

  it("allows float for run / workspace / file / changes", () => {
    panel().openTab(runDetail("run-1"));
    panel().showFile("src/a.ts", "a.ts");
    expect(panel().floatTab(tabId("run-1"))).toBe(true);
    expect(panel().floatTab(fileTabId("src/a.ts"))).toBe(true);
    expect(panel().floatTab(WORKSPACE_TAB_ID)).toBe(true);
    expect(panel().floatTab(CHANGES_TAB_ID)).toBe(true);
    expect(
      panel()
        .floats.map((f) => f.tabId)
        .sort(),
    ).toEqual(
      [
        CHANGES_TAB_ID,
        WORKSPACE_TAB_ID,
        fileTabId("src/a.ts"),
        tabId("run-1"),
      ].sort(),
    );
  });

  it("rejects a 9th float until one is docked or destroyed", () => {
    for (let i = 0; i < SIDE_PANEL_MAX_FLOATS; i++) {
      panel().openTab(runDetail(`run-${i}`));
      expect(panel().floatTab(tabId(`run-${i}`))).toBe(true);
    }
    panel().openTab(runDetail("run-extra"));
    expect(panel().floatTab(tabId("run-extra"))).toBe(false);
    expect(panel().floats).toHaveLength(SIDE_PANEL_MAX_FLOATS);

    panel().dockTab(tabId("run-0"));
    expect(panel().floatTab(tabId("run-extra"))).toBe(true);
    expect(panel().floats).toHaveLength(SIDE_PANEL_MAX_FLOATS);
  });

  it("closePanel / togglePanel keep floats and do not destroy them", () => {
    panel().openTab(runDetail("run-1"));
    panel().floatTab(tabId("run-1"));
    panel().floatTab(WORKSPACE_TAB_ID);
    panel().closePanel();
    expect(panel().open).toBe(false);
    expect(
      panel()
        .floats.map((f) => f.tabId)
        .sort(),
    ).toEqual([WORKSPACE_TAB_ID, tabId("run-1")].sort());
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-1")]);

    panel().openPanel();
    panel().togglePanel(); // close
    expect(panel().floats).toHaveLength(2);
    expect(panel().isFloating(tabId("run-1"))).toBe(true);
  });

  it("floating tabs are not evicted by the closable 12-cap", () => {
    panel().openTab(runDetail("run-float"));
    panel().floatTab(tabId("run-float"));
    for (let i = 0; i < SIDE_PANEL_MAX_TABS; i++) {
      panel().openTab(runDetail(`run-${i}`));
    }
    expect(panel().tabs).toHaveLength(SIDE_PANEL_MAX_TABS);
    expect(panel().isFloating(tabId("run-float"))).toBe(true);
    expect(panel().tabs.some((t) => t.id === tabId("run-float"))).toBe(true);
    // Oldest docked (run-0) was dropped to make room; float survives.
    expect(panel().tabs.some((t) => t.id === tabId("run-0"))).toBe(false);
  });

  it("workspace detach does not destroy; destroyFloat rejects fixed tabs", () => {
    expect(panel().floatTab(WORKSPACE_TAB_ID)).toBe(true);
    expect(panel().destroyFloat(WORKSPACE_TAB_ID)).toBe(false);
    expect(panel().isFloating(WORKSPACE_TAB_ID)).toBe(true);

    panel().floatTab(CHANGES_TAB_ID);
    expect(panel().destroyFloat(CHANGES_TAB_ID)).toBe(false);
    expect(panel().isFloating(CHANGES_TAB_ID)).toBe(true);

    panel().dockTab(WORKSPACE_TAB_ID);
    expect(panel().isFloating(WORKSPACE_TAB_ID)).toBe(false);
    expect(panel().activeTabId).toBe(WORKSPACE_TAB_ID);
  });

  it("destroyFloat removes a closable float tab entirely", () => {
    panel().openTab(runDetail("run-1"));
    panel().floatTab(tabId("run-1"));
    expect(panel().destroyFloat(tabId("run-1"))).toBe(true);
    expect(panel().floats).toHaveLength(0);
    expect(panel().tabs).toHaveLength(0);
  });

  it("clearFloats wipes floats and floating content tabs (切对话 API)", () => {
    panel().openTab(runDetail("run-1"));
    panel().showFile("src/a.ts", "a.ts");
    panel().floatTab(tabId("run-1"));
    panel().floatTab(fileTabId("src/a.ts"));
    panel().floatTab(WORKSPACE_TAB_ID);
    panel().openTab(runDetail("run-docked"));
    panel().clearFloats();
    expect(panel().floats).toHaveLength(0);
    expect(panel().tabs.map((t) => t.id)).toEqual([tabId("run-docked")]);
    expect(panel().focusSurface).toEqual({ type: "dock" });
  });

  it("openTab on a floating tab focuses the float without opening a dock copy", () => {
    panel().openTab(runDetail("run-1"));
    panel().floatTab(tabId("run-1"));
    panel().closePanel();
    panel().openTab({ ...runDetail("run-1"), title: "研究员" });
    expect(panel().open).toBe(false);
    expect(panel().tabs).toHaveLength(1);
    expect(panel().tabs[0].title).toBe("研究员");
    expect(panel().focusSurface).toEqual({
      type: "float",
      tabId: tabId("run-1"),
    });
  });

  it("focusFloat / focusDock express the highlight focus surface", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().floatTab(tabId("run-1"));
    // Dock active stayed on run-2 (it was already active when run-1 floated).
    expect(panel().activeTabId).toBe(tabId("run-2"));
    panel().focusDock();
    expect(sidePanelFocusTabId(panel())).toBe(tabId("run-2"));
    panel().focusFloat(tabId("run-1"));
    expect(sidePanelFocusTabId(panel())).toBe(tabId("run-1"));
    expect(panel().floats[0]?.layout.zIndex).toBeGreaterThan(0);
  });

  it("focusFloat is a no-op when that float is already the focus surface", () => {
    panel().openTab(runDetail("run-1"));
    panel().openTab(runDetail("run-2"));
    panel().floatTab(tabId("run-1"));
    panel().floatTab(tabId("run-2"));
    const before = panel().floats;
    const zBefore = before.map((f) => f.layout.zIndex);
    panel().focusFloat(tabId("run-2"));
    expect(panel().floats).toBe(before);
    expect(panel().floats.map((f) => f.layout.zIndex)).toEqual(zBefore);
  });

  it("dismissFocusedFloat docks the focused float (Esc / Ctrl+J)", () => {
    panel().openTab(runDetail("run-1"));
    panel().floatTab(tabId("run-1"));
    expect(dismissFocusedFloat()).toBe(true);
    expect(panel().floats).toHaveLength(0);
    expect(panel().activeTabId).toBe(tabId("run-1"));
    expect(dismissFocusedFloat()).toBe(false);
  });
});
