// @vitest-environment jsdom
import {
  WorkspaceModeMenu,
  type WorkspaceModeState,
} from "@/components/workspace/WorkspaceModeControl";
import type { EffectiveWorkspace } from "@/lib/workspaceEffectiveMode";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => true,
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [{ id: "c-cloud", folderId: "f1" }],
}));

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyActionError: vi.fn(),
}));

const registerMergeLanding = vi.fn(async (..._args: unknown[]) => ({
  ok: true as const,
  root: { id: "root-1", name: "desk" },
}));
const mergeBackToLanding = vi.fn(async (..._args: unknown[]) => ({
  ok: true as const,
}));
type LandingPeek = {
  rootId: string;
  rootName: string | null;
  missing: boolean;
} | null;
const peekMergeLanding = vi.fn<(...args: unknown[]) => LandingPeek>(() => null);

vi.mock("@/services/cloudDeskExit", () => ({
  registerMergeLanding: (...args: unknown[]) => registerMergeLanding(...args),
  mergeBackToLanding: (...args: unknown[]) => mergeBackToLanding(...args),
  peekMergeLanding: (...args: unknown[]) => peekMergeLanding(...args),
}));

vi.mock("@/stores/folders", () => ({
  useFoldersStore: {
    getState: () => ({
      openImportToCloud: vi.fn(),
      openConnectGit: vi.fn(),
    }),
  },
}));

function cloudState(
  overrides?: Partial<WorkspaceModeState>,
): WorkspaceModeState {
  const effective: EffectiveWorkspace = {
    isLocal: false,
    rootId: null,
    rootName: null,
    rootMissing: false,
    viaContainer: false,
    folderName: "云项目",
    viaFolder: true,
  };
  return {
    binding: {
      mode: "cloud",
      scope: "folder",
      rootId: null,
      source: "explicit",
    },
    roots: [{ id: "root-1", name: "desk" }],
    effective,
    refresh: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  registerMergeLanding.mockClear();
  mergeBackToLanding.mockClear();
  peekMergeLanding.mockReturnValue(null);
});

afterEach(() => {
  cleanup();
});

describe("WorkspaceModeMenu · cloud desk §7.6 exits", () => {
  it("chip only keeps 合回到本机; export / import / git / artifacts stay off this menu", () => {
    render(<WorkspaceModeMenu state={cloudState()} conversationId="c-cloud" />);

    expect(screen.getByText("合回到本机")).toBeTruthy();
    expect(screen.queryByText(/不可改绑/)).toBeNull();
    expect(screen.queryByText("导出 ZIP")).toBeNull();
    expect(screen.queryByText("导出到本机文件夹")).toBeNull();
    expect(screen.queryByText("登记合回落点")).toBeNull();
    expect(screen.queryByText("更换合回落点")).toBeNull();
    expect(screen.queryByText("只合回产物")).toBeNull();
    expect(screen.queryByText("导入到「我的文件」")).toBeNull();
    expect(screen.queryByText("从 Git 克隆")).toBeNull();
    expect(screen.queryByText("遗留：先改云拷贝再合回")).toBeNull();
  });

  it("shows 更换合回落点 only when landing is registered", () => {
    peekMergeLanding.mockReturnValue({
      rootId: "root-1",
      rootName: "desk",
      missing: false,
    });
    render(<WorkspaceModeMenu state={cloudState()} conversationId="c-cloud" />);
    expect(screen.getByText("更换合回落点")).toBeTruthy();
    expect(screen.getByText("当前 · desk")).toBeTruthy();
  });

  it("register landing click invokes cloudDeskExit", async () => {
    peekMergeLanding.mockReturnValue({
      rootId: "root-1",
      rootName: "desk",
      missing: false,
    });
    render(<WorkspaceModeMenu state={cloudState()} conversationId="c-cloud" />);
    fireEvent.click(screen.getByText("更换合回落点"));
    await waitFor(() => {
      expect(registerMergeLanding).toHaveBeenCalledWith("c-cloud");
    });
  });

  it("merge back click invokes cloudDeskExit with roots", async () => {
    const state = cloudState();
    render(<WorkspaceModeMenu state={state} conversationId="c-cloud" />);
    fireEvent.click(screen.getByText("合回到本机"));
    await waitFor(() => {
      expect(mergeBackToLanding).toHaveBeenCalledWith("c-cloud", state.roots);
    });
  });
});
