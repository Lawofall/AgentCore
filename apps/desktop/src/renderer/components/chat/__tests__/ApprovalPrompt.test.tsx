// @vitest-environment jsdom
import { ApprovalCard } from "@/components/chat/ApprovalPrompt";
import { TooltipProvider } from "@/components/ui/tooltip";
import { patchConversationCache } from "@/hooks/useConversations";
import { setConversationPermissionAxes } from "@/services/permissionAxes";
import type { ApprovalView } from "@/stores/interactions";
import { useInteractionStore } from "@/stores/interactions";
import { usePermissionChangeStore } from "@/stores/permissionChanges";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [
    {
      id: "c1",
      permissionAxes: {
        file_write: "session",
        command: "auto",
        host: "ask",
      },
    },
  ],
  patchConversationCache: vi.fn(),
}));

vi.mock("@/services/permissionAxes", () => ({
  setConversationPermissionAxes: vi.fn(),
  matchRecipe: () => "less_interrupt",
  recipeToAxes: () => ({
    file_write: "session",
    command: "auto",
    host: "session",
  }),
}));

vi.mock("@/services/approvals", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/approvals")>();
  return { ...actual, decideApproval: vi.fn() };
});

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

afterEach(() => {
  cleanup();
  useInteractionStore.setState({ byId: new Map() });
});

beforeEach(() => {
  vi.mocked(setConversationPermissionAxes).mockReset();
  vi.mocked(patchConversationCache).mockReset();
});

function card(over: Partial<ApprovalView> = {}): ApprovalView {
  return {
    approvalId: "a1",
    conversationId: "c1",
    toolCallId: "a1",
    toolName: "terminal",
    arguments: { subcommand: "start", command: "pnpm dev" },
    resolving: false,
    ...over,
  };
}

function renderCard(approval: ApprovalView) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ApprovalCard approval={approval} onDecide={() => {}} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

/** Seed enough same-tool approvals so the「全放行」nudge appears. */
function seedSameToolApprovals(n: number, toolName = "terminal") {
  const byId = new Map();
  for (let i = 0; i < n; i++) {
    byId.set(`hist-${i}`, {
      id: `hist-${i}`,
      kind: "approval" as const,
      status: "resolved" as const,
      conversationId: "c1",
      messageId: "m1",
      payload: { tool_name: toolName, approval_id: `hist-${i}` },
    });
  }
  useInteractionStore.setState({ byId });
}

describe("ApprovalCard git headline", () => {
  it("shows push → remote for git push approvals", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "push", remote: "origin" },
      }),
    );
    expect(screen.getByText("push → origin")).toBeTruthy();
  });

  it("defaults push remote to origin when omitted", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "push" },
      }),
    );
    expect(screen.getByText("push → origin")).toBeTruthy();
  });

  it("shows commit + message snippet", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: {
          subcommand: "commit",
          message: "fix approval headline for push",
        },
      }),
    );
    expect(
      screen.getByText("commit fix approval headline for push"),
    ).toBeTruthy();
  });

  it("falls back to subcommand when no extra args", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "status" },
      }),
    );
    expect(screen.getByText("status")).toBeTruthy();
  });

  it("shows create_pr with title and head → base", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: {
          subcommand: "create_pr",
          title: "Add git SCM panel",
          head: "feat/scm",
          base: "main",
          remote: "origin",
        },
      }),
    );
    expect(
      screen.getByText("create_pr Add git SCM panel · feat/scm → main"),
    ).toBeTruthy();
  });

  it("shows add + paths from the real paths array", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "add", paths: ["src/a.ts", "src/b.ts"] },
      }),
    );
    expect(screen.getByText("add src/a.ts, src/b.ts")).toBeTruthy();
  });

  it("does not misread path/file_path for git add", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: {
          subcommand: "add",
          path: "wrong.txt",
          file_path: "also-wrong.txt",
          paths: ["real.txt"],
        },
      }),
    );
    expect(screen.getByText("add real.txt")).toBeTruthy();
    expect(screen.queryByText("add wrong.txt")).toBeNull();
  });

  it("falls back to bare add when paths missing", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "add" },
      }),
    );
    expect(screen.getByText("add")).toBeTruthy();
  });

  it("shows pull ← remote for git pull approvals", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "pull", remote: "upstream" },
      }),
    );
    expect(screen.getByText("pull ← upstream")).toBeTruthy();
  });

  it("defaults pull remote to origin when omitted", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "pull" },
      }),
    );
    expect(screen.getByText("pull ← origin")).toBeTruthy();
  });

  it("shows fetch ← remote", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "fetch", remote: "origin" },
      }),
    );
    expect(screen.getByText("fetch ← origin")).toBeTruthy();
  });

  it("shows show + ref", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "show", ref: "HEAD~1" },
      }),
    );
    expect(screen.getByText("show HEAD~1")).toBeTruthy();
  });

  it("shows blame + paths", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "blame", paths: ["README.md"] },
      }),
    );
    expect(screen.getByText("blame README.md")).toBeTruthy();
  });

  it("shows G2 stash / merge / rebase / cherry-pick / tag / remote headlines", () => {
    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "stash", action: "push" },
      }),
    );
    expect(screen.getByText("stash push")).toBeTruthy();
    cleanup();

    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "merge", ref: "develop" },
      }),
    );
    expect(screen.getByText("merge develop")).toBeTruthy();
    cleanup();

    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "rebase", branch: "main" },
      }),
    );
    expect(screen.getByText("rebase main")).toBeTruthy();
    cleanup();

    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "cherry-pick", commit: "abc1234" },
      }),
    );
    expect(screen.getByText("cherry-pick abc1234")).toBeTruthy();
    cleanup();

    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "tag", action: "create", name: "v1.0" },
      }),
    );
    expect(screen.getByText("tag v1.0")).toBeTruthy();
    cleanup();

    renderCard(
      card({
        toolName: "git",
        arguments: { subcommand: "remote", action: "add", name: "upstream" },
      }),
    );
    expect(screen.getByText("remote add upstream")).toBeTruthy();
  });
});

describe("ApprovalCard host headline", () => {
  it("shows shell + command for host action=shell", () => {
    renderCard(
      card({
        toolName: "host",
        arguments: { action: "shell", command: "Get-Process" },
      }),
    );
    expect(screen.getByText("本机 Host")).toBeTruthy();
    expect(screen.getByText("shell Get-Process")).toBeTruthy();
  });

  it("shows install_package manager + package_id", () => {
    renderCard(
      card({
        toolName: "host",
        arguments: {
          action: "install_package",
          manager: "winget",
          package_id: "Git.Git",
        },
      }),
    );
    expect(screen.getByText("install_package winget Git.Git")).toBeTruthy();
  });

  it("falls back to action when no extra args", () => {
    renderCard(
      card({
        toolName: "host",
        arguments: { action: "status" },
      }),
    );
    expect(screen.getByText("status")).toBeTruthy();
  });
});

describe("ApprovalCard delete_folder headline", () => {
  it("names the folder instead of showing a bare UUID", () => {
    renderCard(
      card({
        toolName: "delete_folder",
        arguments: {
          folder_id: "11111111-2222-4333-8444-555555555555",
          // 后端按权威名册补的实名字段（不是模型自报）。
          folder_name: "dogfood-dup",
        },
      }),
    );
    expect(screen.getByText("删除文件夹")).toBeTruthy();
    expect(
      screen.getByText("dogfood-dup · 11111111-2222-4333-8444-555555555555"),
    ).toBeTruthy();
  });

  it("falls back to the id when the roster lookup produced no name", () => {
    renderCard(
      card({
        toolName: "delete_folder",
        arguments: { folder_id: "11111111-2222-4333-8444-555555555555" },
      }),
    );
    expect(
      screen.getByText("11111111-2222-4333-8444-555555555555"),
    ).toBeTruthy();
  });
});

describe("ApprovalCard CTA (工具审批 A+B)", () => {
  it("execution tools put 本轮内都允许 as the primary button", () => {
    renderCard(card());
    const buttons = screen.getAllByRole("button");
    const labels = buttons.map((b) => b.textContent ?? "");
    const turnIdx = labels.findIndex((t) => t.includes("本轮内都允许"));
    const onceIdx = labels.findIndex((t) => t.includes("允许一次"));
    expect(turnIdx).toBeGreaterThanOrEqual(0);
    expect(onceIdx).toBeGreaterThanOrEqual(0);
    expect(turnIdx).toBeLessThan(onceIdx);
  });

  it("file tools keep 允许一次 before 本轮内都允许", () => {
    renderCard(
      card({
        toolName: "file_write",
        arguments: { path: "a.txt", content: "x" },
      }),
    );
    const buttons = screen.getAllByRole("button");
    const labels = buttons.map((b) => b.textContent ?? "");
    const turnIdx = labels.findIndex((t) => t.includes("本轮内都允许"));
    const onceIdx = labels.findIndex((t) => t.includes("允许一次"));
    expect(onceIdx).toBeLessThan(turnIdx);
  });

  it("点之前先说清「本轮」有多大：同类、含队员、一个回合可能几十次", () => {
    renderCard(card());
    const notice = screen.getByTestId("turn-grant-scope-notice").textContent;
    expect(notice).toContain("到这次回答结束前");
    expect(notice).toContain("队员");
    expect(notice).toContain("几十次");
  });

  it("文件类还要说出比按钮字面更宽的那部分（含 git 写入）", () => {
    renderCard(
      card({
        toolName: "file_write",
        arguments: { path: "a.txt", content: "x" },
      }),
    );
    const notice = screen.getByTestId("turn-grant-scope-notice").textContent;
    expect(notice).toContain("所有文件改动");
    expect(notice).toContain("git 写入");
  });

  it("熔断一次性卡没有轮内授权 → 不出现「本轮」范围说明", () => {
    renderCard(
      card({
        arguments: {
          command: "rm -rf /",
          force_one_shot: true,
        },
      }),
    );
    expect(screen.queryByTestId("turn-grant-scope-notice")).toBeNull();
  });

  it("switching to 全放行 patches cache and reloads permission change lines", async () => {
    seedSameToolApprovals(3);
    const managed = {
      file_write: "session" as const,
      command: "auto" as const,
      host: "session" as const,
    };
    vi.mocked(setConversationPermissionAxes).mockResolvedValue(managed);
    const load = vi.fn().mockResolvedValue(undefined);
    usePermissionChangeStore.setState({ load });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderCard(card());
    fireEvent.click(screen.getByRole("button", { name: "全放行" }));

    await waitFor(() => {
      expect(setConversationPermissionAxes).toHaveBeenCalledWith("c1", managed);
      expect(patchConversationCache).toHaveBeenCalledWith("c1", {
        permissionAxes: managed,
      });
      expect(load).toHaveBeenCalledWith("c1");
    });
  });
});

describe("ApprovalCard escalation tracks (熔断 vs 敏感读)", () => {
  it("force_one_shot fuse card: fuse copy, no turn-grant buttons", () => {
    renderCard(
      card({
        toolName: "terminal",
        arguments: {
          command: "rm -rf /",
          force_one_shot: true,
          rule_id: "destructive.rm_root",
          circuit_breaker_hint: "命中毁灭性命令启发式",
        },
      }),
    );
    expect(
      screen.getByText(/安全熔断升格审批（启发式兜底，并非完整拦截）/),
    ).toBeTruthy();
    expect(screen.getByText(/命中毁灭性命令启发式/)).toBeTruthy();
    expect(screen.queryByText(/敏感路径读升格审批/)).toBeNull();
    expect(screen.queryByRole("button", { name: /本轮内都允许/ })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /本轮内允许所有文件改动/ }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: /允许一次/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /拒绝/ })).toBeTruthy();
  });

  it("force_one_shot file-op fuse: also hides 本轮内允许所有文件改动", () => {
    renderCard(
      card({
        toolName: "file_write",
        arguments: {
          path: "a.txt",
          content: "x",
          force_one_shot: true,
          circuit_breaker_hint: "工作区顶层整树删除",
        },
      }),
    );
    expect(screen.getByText(/安全熔断升格审批/)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /本轮内允许所有文件改动/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /本轮内都允许/ })).toBeNull();
  });

  it("sensitive.path_read_ask: turn grants + sensitive copy, no fuse boilerplate", () => {
    renderCard(
      card({
        toolName: "file_read",
        arguments: {
          path: ".env",
          rule_id: "sensitive.path_read_ask",
          circuit_breaker_hint:
            "读取凭据类路径需确认\n键名预览：API_KEY, DATABASE_URL",
        },
      }),
    );
    expect(screen.getByText(/敏感路径读升格审批/)).toBeTruthy();
    expect(screen.getByText(/键名预览：API_KEY, DATABASE_URL/)).toBeTruthy();
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.getByRole("button", { name: /本轮内都允许/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /允许一次/ })).toBeTruthy();
  });

  it("allow_turn_grant without force_one_shot tracks as sensitive read", () => {
    renderCard(
      card({
        toolName: "file_read",
        arguments: {
          path: ".env.local",
          allow_turn_grant: true,
          circuit_breaker_hint: "敏感读需确认",
        },
      }),
    );
    expect(screen.getByText(/敏感路径读升格审批/)).toBeTruthy();
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.getByRole("button", { name: /本轮内都允许/ })).toBeTruthy();
  });

  it("circuit_breaker_hint alone does not hide turn grants or apply fuse copy", () => {
    renderCard(
      card({
        toolName: "terminal",
        arguments: {
          command: "echo hi",
          circuit_breaker_hint: "孤立 hint 不应当熔断",
        },
      }),
    );
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.queryByText(/敏感路径读升格审批/)).toBeNull();
    expect(screen.getByRole("button", { name: /本轮内都允许/ })).toBeTruthy();
  });

  it("force_one_shot wins over sensitive rule_id / allow_turn_grant", () => {
    renderCard(
      card({
        toolName: "file_read",
        arguments: {
          path: ".env",
          force_one_shot: true,
          rule_id: "sensitive.path_read_ask",
          allow_turn_grant: true,
          circuit_breaker_hint: "冲突时以熔断为准",
        },
      }),
    );
    expect(screen.getByText(/安全熔断升格审批/)).toBeTruthy();
    expect(screen.queryByText(/敏感路径读升格审批/)).toBeNull();
    expect(screen.queryByRole("button", { name: /本轮内都允许/ })).toBeNull();
  });
});
