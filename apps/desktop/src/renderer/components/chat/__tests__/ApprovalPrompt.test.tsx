// @vitest-environment jsdom
import { ApprovalCard } from "@/components/chat/ApprovalPrompt";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ApprovalView } from "@/stores/interactions";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("shows restart_service Audiosrv without a service field", () => {
    renderCard(
      card({
        toolName: "host",
        arguments: { action: "restart_service" },
      }),
    );
    expect(screen.getByText("restart_service Audiosrv")).toBeTruthy();
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
  it("eyebrow is 请求执行 and title is the tool label with the object", () => {
    renderCard(
      card({
        toolName: "file_delete",
        arguments: { path: "审批卡演示.txt" },
      }),
    );
    expect(screen.getByText("请求执行")).toBeTruthy();
    expect(screen.getByText("删除文件")).toBeTruthy();
    expect(screen.getByText("审批卡演示.txt")).toBeTruthy();
    expect(screen.queryByText("Agent 请求执行")).toBeNull();
    expect(screen.queryByRole("button", { name: "看手册说明" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /查看参数|查看内容|查看代码/ }),
    ).toBeNull();
  });

  it("execution tools put 允许一次 first, 本轮内都允许 second", () => {
    renderCard(card());
    const buttons = screen.getAllByRole("button");
    const labels = buttons.map((b) => b.textContent ?? "");
    const turnIdx = labels.findIndex((t) => t.includes("本轮内都允许"));
    const onceIdx = labels.findIndex((t) => t.includes("允许一次"));
    expect(turnIdx).toBeGreaterThanOrEqual(0);
    expect(onceIdx).toBeGreaterThanOrEqual(0);
    expect(onceIdx).toBeLessThan(turnIdx);
    const denyIdx = labels.findIndex((t) => t.includes("拒绝"));
    expect(denyIdx).toBeGreaterThan(turnIdx);
    expect(
      screen.getByRole("button", { name: /本轮内都允许/ }).className,
    ).toContain("border-border");
  });

  it("file tools keep 允许一次 before 本轮内都允许", () => {
    renderCard(
      card({
        toolName: "write",
        arguments: { file_path: "a.txt", content: "x" },
      }),
    );
    const buttons = screen.getAllByRole("button");
    const labels = buttons.map((b) => b.textContent ?? "");
    const turnIdx = labels.findIndex((t) => t.includes("本轮内都允许"));
    const onceIdx = labels.findIndex((t) => t.includes("允许一次"));
    expect(onceIdx).toBeLessThan(turnIdx);
    const denyIdx = labels.findIndex((t) => t.includes("拒绝"));
    expect(denyIdx).toBeGreaterThan(turnIdx);
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
    expect(screen.queryByText(/读取凭据文件需确认/)).toBeNull();
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
        toolName: "write",
        arguments: {
          file_path: "a.txt",
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
        toolName: "read",
        arguments: {
          file_path: ".env",
          rule_id: "sensitive.path_read_ask",
          circuit_breaker_hint:
            "读取凭据类路径需确认\n键名预览：API_KEY, DATABASE_URL",
        },
      }),
    );
    expect(screen.getByText(/读取凭据文件需确认/)).toBeTruthy();
    expect(screen.getByText(/键名预览：API_KEY, DATABASE_URL/)).toBeTruthy();
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.getByRole("button", { name: /本轮内都允许/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /允许一次/ })).toBeTruthy();
  });

  it("allow_turn_grant without force_one_shot tracks as sensitive read", () => {
    renderCard(
      card({
        toolName: "read",
        arguments: {
          file_path: ".env.local",
          allow_turn_grant: true,
          circuit_breaker_hint: "敏感读需确认",
        },
      }),
    );
    expect(screen.getByText(/读取凭据文件需确认/)).toBeTruthy();
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
    expect(screen.queryByText(/读取凭据文件需确认/)).toBeNull();
    expect(screen.getByRole("button", { name: /本轮内都允许/ })).toBeTruthy();
  });

  it("force_one_shot wins over sensitive rule_id / allow_turn_grant", () => {
    renderCard(
      card({
        toolName: "read",
        arguments: {
          file_path: ".env",
          force_one_shot: true,
          rule_id: "sensitive.path_read_ask",
          allow_turn_grant: true,
          circuit_breaker_hint: "冲突时以熔断为准",
        },
      }),
    );
    expect(screen.getByText(/安全熔断升格审批/)).toBeTruthy();
    expect(screen.queryByText(/读取凭据文件需确认/)).toBeNull();
    expect(screen.queryByRole("button", { name: /本轮内都允许/ })).toBeNull();
  });
});

describe("ApprovalCard extra payload (omit restatements of the headline)", () => {
  it("does not dump JSON when the headline already is the only payload", () => {
    renderCard(card());
    expect(screen.queryByText(/subcommand/)).toBeNull();
  });

  it("shows write body under the path, not as JSON", () => {
    renderCard(
      card({
        toolName: "write",
        arguments: { file_path: "a.txt", content: "hello body" },
      }),
    );
    expect(screen.getByText("a.txt")).toBeTruthy();
    expect(screen.getByText("hello body")).toBeTruthy();
    expect(screen.getByText("1 行")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
    expect(screen.queryByText(/"content"/)).toBeNull();
  });

  it("clamps a long write body until 展开", () => {
    const content = [
      "# 示例文件",
      "",
      "这是刚写进工作区的一个演示文件，用来看看效果。",
      "",
      "## 能放什么",
      "- 列表、层级标题",
      "- 表格",
    ].join("\n");
    renderCard(
      card({
        toolName: "write",
        arguments: { file_path: "示例.md", content },
      }),
    );
    expect(screen.getByText("7 行")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /查看参数|查看内容|查看代码/ }),
    ).toBeNull();
    const pre = document.querySelector("pre");
    expect(pre?.className).toContain("line-clamp-3");
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByRole("button", { name: "收起" })).toBeTruthy();
    expect(document.querySelector("pre")?.className).toContain("max-h-40");
    expect(document.querySelector("pre")?.className).not.toContain(
      "line-clamp-3",
    );
  });

  it("shows code_execute source as a code preview", () => {
    renderCard(
      card({
        toolName: "code_execute",
        arguments: { code: "print(1)" },
      }),
    );
    expect(screen.getByText("print(1)")).toBeTruthy();
    expect(document.querySelector("pre")).toBeTruthy();
    expect(document.querySelector(".code-block")).toBeNull();
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
  });

  it("uses run command as headline", () => {
    renderCard(
      card({
        toolName: "run",
        arguments: { command: "pytest tests" },
      }),
    );
    expect(screen.getByText("pytest tests")).toBeTruthy();
  });

  it("shows file_move as source → destination on the title line", () => {
    renderCard(
      card({
        toolName: "file_move",
        arguments: { source: "a.txt", destination: "b.txt" },
      }),
    );
    expect(screen.getByText("a.txt → b.txt")).toBeTruthy();
    expect(screen.queryByText(/"destination"/)).toBeNull();
  });

  it("shows permanent delete as a badge, not a JSON dump", () => {
    renderCard(
      card({
        toolName: "file_delete",
        arguments: { path: "gone.txt", permanent: true },
      }),
    );
    expect(screen.getByText("永久删除")).toBeTruthy();
    expect(screen.queryByText(/permanent/)).toBeNull();
  });

  it("shows edit as a compact red/green summary", () => {
    renderCard(
      card({
        toolName: "edit",
        arguments: {
          file_path: "a.txt",
          old_string: "alpha",
          new_string: "beta",
        },
      }),
    );
    expect(screen.getByText("- alpha")).toBeTruthy();
    expect(screen.getByText("+ beta")).toBeTruthy();
    expect(screen.queryByText("原文")).toBeNull();
    expect(screen.queryByText("替换为")).toBeNull();
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
    expect(screen.queryByText(/"old_string"/)).toBeNull();
  });

  it("expands a long edit to labeled previews", () => {
    const oldString = ["line a", "line b", "line c", "line d"].join("\n");
    const newString = ["line a", "line x", "line c", "line d"].join("\n");
    renderCard(
      card({
        toolName: "edit",
        arguments: {
          file_path: "a.txt",
          old_string: oldString,
          new_string: newString,
        },
      }),
    );
    expect(screen.getByText("- line a")).toBeTruthy();
    expect(screen.getByText("+ line a")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByText("原文")).toBeTruthy();
    expect(screen.getByText("替换为")).toBeTruthy();
    const pres = [...document.querySelectorAll("pre")].map(
      (el) => el.textContent,
    );
    expect(pres.some((t) => t?.includes("line b"))).toBe(true);
    expect(pres.some((t) => t?.includes("line x"))).toBe(true);
  });

  it("renders leftover flags as labeled rows, not a JSON dump", () => {
    renderCard(
      card({
        toolName: "write",
        arguments: {
          file_path: "a.txt",
          content: "hello body",
          overwrite: true,
        },
      }),
    );
    expect(screen.getByText("覆盖")).toBeTruthy();
    expect(screen.getByText("是")).toBeTruthy();
    expect(screen.queryByText(/"overwrite"/)).toBeNull();
  });
});
