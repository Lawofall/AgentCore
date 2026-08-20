// @vitest-environment jsdom
/**
 * 开工卡两态：确认态三键（取消 / 调整 / 授权开工·开赛），嘱咐可选且跟 continue；
 * 调整态只有必填意见 + 交回修订 + 返回，不渲染开工；提交中保留表单、CTA loading。
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumePrompt } from "../ResumePrompt";
import { clearTeamPreviewKickoffDraft } from "../resume/teamPreviewKickoffDraft";

const submitInteraction = vi.fn().mockResolvedValue("ok");
const notifyError = vi.fn();

const notifySubmitInteractionResult = vi.fn();

vi.mock("@/services/interactionSubmit", () => ({
  submitInteraction: (...args: unknown[]) => submitInteraction(...args),
  notifySubmitInteractionResult: (...args: unknown[]) =>
    notifySubmitInteractionResult(...args),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: (...args: unknown[]) => notifyError(...args),
}));

vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({
    data: {
      models: [
        {
          id: "ceo-flash",
          display_name: "CEO Flash",
          origin: "platform",
          available: true,
        },
        {
          id: "worker-pro",
          display_name: "Worker Pro",
          origin: "platform",
          available: true,
        },
      ],
      current: { id: "ceo-flash", origin: "platform" },
    },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({
    data: { providers: [], platform_available: true },
    isLoading: false,
    isError: false,
  }),
}));

const pendingRef: { current: unknown[] } = { current: [] };
const interactionById = new Map<
  string,
  { kind?: string; payload?: Record<string, unknown>; status?: string }
>();

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: "c1" }),
}));

vi.mock("@/stores/pausedTurns", () => ({
  usePausedTurnStore: (sel: (s: { pending: unknown[] }) => unknown) =>
    sel({ pending: pendingRef.current }),
}));

vi.mock("@/stores/interactions", async () => {
  const actual = await vi.importActual<typeof import("@/stores/interactions")>(
    "@/stores/interactions",
  );
  return {
    ...actual,
    useInteractionStore: (
      sel: (s: {
        byId: Map<
          string,
          { kind?: string; payload?: Record<string, unknown>; status?: string }
        >;
      }) => unknown,
    ) => sel({ byId: interactionById }),
  };
});

function openKickoffNote() {
  fireEvent.click(screen.getByRole("button", { name: /加一句嘱咐/ }));
}

function enterAdjust(note: string) {
  fireEvent.click(screen.getByRole("button", { name: "调整" }));
  fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
    target: { value: note },
  });
}

function makeTeamPreview(over: Record<string, unknown> = {}) {
  return {
    messageId: "m1",
    conversationId: "c1",
    checkpointId: "cp1",
    kind: "team_preview",
    userMessage: "组团做定价",
    userMessageId: "u1",
    steps: [],
    pending: [],
    workers: [
      {
        run_id: "r1",
        role: "研究员",
        task: "调研",
        depends_on: [],
      },
    ],
    tools: ["file_write", "code_execute"],
    primitive: "delegate",
    motion: "",
    form: "",
    sides: [],
    maxRounds: 0,
    thorough: true,
    question: "",
    assumptions: [],
    questions: [],
    intent: "kickoff",
    origin: "server",
    ...over,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  pendingRef.current = [];
  interactionById.clear();
  clearTeamPreviewKickoffDraft("c1", "cp1");
  clearTeamPreviewKickoffDraft("c1", "cp2");
});

beforeEach(() => {
  pendingRef.current = [makeTeamPreview()];
  submitInteraction.mockReset();
  submitInteraction.mockResolvedValue("ok");
  notifyError.mockReset();
});

describe("ResumePrompt · team_preview delegate", () => {
  it("后端 headline 优先展示在导语", () => {
    pendingRef.current = [
      makeTeamPreview({ headline: "MVP主流程 · 预计 1 人" }),
    ];
    render(<ResumePrompt />);
    expect(screen.getByText("MVP主流程 · 预计 1 人")).toBeTruthy();
  });

  it("首版无版本标记", () => {
    pendingRef.current = [makeTeamPreview({ revision: 1 })];
    render(<ResumePrompt />);
    expect(screen.queryByTestId("team-preview-revision")).toBeNull();
    expect(screen.queryByTestId("team-preview-revision-version")).toBeNull();
    expect(screen.queryByText("第 1 版")).toBeNull();
    expect(screen.queryByText("按你的意见修订")).toBeNull();
  });

  it("第 2 版显示版本 + 意见", () => {
    pendingRef.current = [
      makeTeamPreview({
        revision: 2,
        revisedFrom: "cp-prev",
        revisionNote: "改成两人，先做竞品",
      }),
    ];
    render(<ResumePrompt />);
    expect(
      screen.getByTestId("team-preview-revision-version").textContent,
    ).toBe("第 2 版");
    expect(screen.getByText("按你的意见修订")).toBeTruthy();
    expect(
      screen.getByTestId("team-preview-revision-note").textContent,
    ).toContain("改成两人，先做竞品");
  });

  it("上一版缺失时不画 diff", () => {
    pendingRef.current = [
      makeTeamPreview({
        revision: 2,
        revisedFrom: "cp-prev",
        revisionNote: "改成两人，先做竞品",
        workers: [
          { run_id: "r1", role: "研究员", task: "调研", depends_on: [] },
          { run_id: "r2", role: "撰写员", task: "写报告", depends_on: [] },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.getByText("第 2 版")).toBeTruthy();
    expect(screen.getByText("改成两人，先做竞品")).toBeTruthy();
    expect(screen.queryByTestId("team-preview-revision-changes")).toBeNull();
    expect(screen.queryByText("相对上一版")).toBeNull();
    expect(screen.queryByText("无变化")).toBeNull();
    expect(screen.queryByText("新增 撰写员")).toBeNull();
  });

  it("上一版在本地时画出成员增删", () => {
    interactionById.set("cp-prev", {
      kind: "team_preview",
      status: "resolved",
      payload: {
        primitive: "delegate",
        workers: [
          { run_id: "r1", role: "研究员", task: "调研", depends_on: [] },
        ],
      },
    });
    pendingRef.current = [
      makeTeamPreview({
        revision: 2,
        revisedFrom: "cp-prev",
        revisionNote: "加一个撰写",
        workers: [
          { run_id: "r1", role: "研究员", task: "调研", depends_on: [] },
          { run_id: "r2", role: "撰写员", task: "写报告", depends_on: [] },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.getByTestId("team-preview-revision-changes")).toBeTruthy();
    expect(screen.getByText("相对上一版")).toBeTruthy();
    expect(screen.getByText("新增 撰写员")).toBeTruthy();
  });

  it("全员同桌时冷拍板分工表不画工作区", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "调研",
            task: "读甲",
            depends_on: [],
            target_folder_name: "本会话工作区",
          },
          {
            run_id: "r2",
            role: "撰写",
            task: "读乙",
            depends_on: [],
            target_folder_name: "本会话工作区",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.getByText("调研")).toBeTruthy();
    expect(screen.getByText("撰写")).toBeTruthy();
    expect(screen.queryByText(/工作区 ·/)).toBeNull();
  });

  it("队员坐不同桌时冷拍板分工表不画工作区", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "甲",
            task: "读甲",
            depends_on: [],
            target_folder_id: "f1",
            target_folder_name: "云端甲",
          },
          {
            run_id: "r2",
            role: "乙",
            task: "读乙",
            depends_on: [],
            target_folder_id: "f2",
            target_folder_name: "云端乙",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.getByText("甲")).toBeTruthy();
    expect(screen.getByText("乙")).toBeTruthy();
    expect(screen.queryByText(/工作区 ·/)).toBeNull();
  });

  it("三按钮：左取消 + 中调整 + 右授权并开工；无逐次审批 / 停止", () => {
    render(<ResumePrompt />);
    expect(screen.queryByText("等你确认 · 确认后才会开工")).toBeNull();
    expect(screen.getByText("预计 1 人开工")).toBeTruthy();
    const cancel = screen.getByRole("button", { name: "取消" });
    const adjust = screen.getByRole("button", { name: "调整" });
    const primary = screen.getByRole("button", { name: "授权并开工" });
    expect(
      cancel.compareDocumentPosition(adjust) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      adjust.compareDocumentPosition(primary) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByText("停止")).toBeNull();
    expect(screen.queryByText("逐次审批开工")).toBeNull();
    expect(screen.getByText("将授权的执行能力")).toBeTruthy();
    expect(screen.getByRole("button", { name: /加一句嘱咐/ })).toBeTruthy();
    expect(screen.queryByPlaceholderText(/开工时注入全体队员/)).toBeNull();
  });

  it("调整态不渲染开工按钮，且不提交", () => {
    render(<ResumePrompt />);
    fireEvent.click(screen.getByRole("button", { name: "调整" }));
    expect(screen.getByTestId("team-preview-adjust-note")).toBeTruthy();
    expect(screen.getByText("调整意见（必填）")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "授权并开工" })).toBeNull();
    expect(screen.queryByRole("button", { name: "开做" })).toBeNull();
    expect(screen.queryByRole("button", { name: /加一句嘱咐/ })).toBeNull();
    expect(submitInteraction).not.toHaveBeenCalled();
  });

  it("返回丢弃调整意见并回到确认态", () => {
    render(<ResumePrompt />);
    enterAdjust("改成两人，先做竞品");
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.getByRole("button", { name: "授权并开工" })).toBeTruthy();
    expect(screen.queryByTestId("team-preview-adjust-note")).toBeNull();
    expect(screen.getByRole("button", { name: /加一句嘱咐/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "调整" }));
    expect(
      (screen.getByTestId("team-preview-adjust-note") as HTMLTextAreaElement)
        .value,
    ).toBe("");
    expect(submitInteraction).not.toHaveBeenCalled();
  });

  it("调整态提交 adjust，不带修正字段", () => {
    render(<ResumePrompt />);
    enterAdjust("  改成两人，先做竞品  ");
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "adjust",
          note: "改成两人，先做竞品",
        }),
      }),
    );
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
    expect(cold.model_overrides).toBeUndefined();
  });

  it("adjust 提交中保留调整表单，不换等待卡；新卡到达后回到确认态", async () => {
    let resolveSubmit: (value: string) => void = () => {};
    submitInteraction.mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    const { rerender } = render(<ResumePrompt />);
    enterAdjust("改成两人，先做竞品");
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(screen.queryByTestId("team-preview-adjust-wait")).toBeNull();
    expect(screen.queryByText("CEO 正在按你的意见重排团队")).toBeNull();
    expect(screen.getByTestId("team-preview-adjust-note")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "交回修订" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.queryByRole("button", { name: "授权并开工" })).toBeNull();

    resolveSubmit("ok");
    await waitFor(() => {
      expect(
        (screen.getByRole("button", { name: "交回修订" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
    expect(screen.getByTestId("team-preview-adjust-note")).toBeTruthy();
    expect(screen.queryByTestId("team-preview-adjust-wait")).toBeNull();

    pendingRef.current = [makeTeamPreview({ checkpointId: "cp2" })];
    rerender(<ResumePrompt />);
    expect(screen.queryByTestId("team-preview-adjust-wait")).toBeNull();
    expect(screen.getByRole("button", { name: "授权并开工" })).toBeTruthy();
  });

  it("调整意见草稿在卸载后仍在调整态", () => {
    const { unmount } = render(<ResumePrompt />);
    enterAdjust("改成两人，先做竞品");
    unmount();
    render(<ResumePrompt />);
    expect(
      (screen.getByTestId("team-preview-adjust-note") as HTMLTextAreaElement)
        .value,
    ).toBe("改成两人，先做竞品");
    expect(screen.queryByRole("button", { name: "授权并开工" })).toBeNull();
  });

  it("调整态 Enter 提交 adjust；确认态 Enter 不开工", () => {
    render(<ResumePrompt />);
    openKickoffNote();
    fireEvent.change(screen.getByPlaceholderText(/开工时注入全体队员/), {
      target: { value: "先做公开竞品" },
    });
    fireEvent.keyDown(screen.getByTestId("team-preview-note"), {
      key: "Enter",
    });
    expect(submitInteraction).not.toHaveBeenCalled();

    enterAdjust("改成两人");
    fireEvent.keyDown(screen.getByTestId("team-preview-adjust-note"), {
      key: "Enter",
    });
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "adjust",
          note: "改成两人",
        }),
      }),
    );
  });

  it("收紧写盘后点调整仍不带 write_capability_overrides", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: "调研",
            depends_on: [],
            write_capability: "can_write_files",
            write_capability_label: "可改文件",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    fireEvent.click(
      screen.getByRole("button", { name: "研究员 收紧为仅文字" }),
    );
    enterAdjust("先改分工");
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.decision).toBe("adjust");
    expect(cold.note).toBe("先改分工");
    expect(cold.write_capability_overrides).toBeUndefined();
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.model_overrides).toBeUndefined();
  });

  it("确认态嘱咐跟 continue，不跟 adjust", () => {
    render(<ResumePrompt />);
    openKickoffNote();
    fireEvent.change(screen.getByPlaceholderText(/开工时注入全体队员/), {
      target: { value: "  先做公开竞品  " },
    });
    fireEvent.click(screen.getByText("授权并开工"));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "continue",
          note: "先做公开竞品",
        }),
      }),
    );
  });

  it("submitInteraction 非 ok 时 toast，不假成功", async () => {
    submitInteraction.mockResolvedValue("busy");
    render(<ResumePrompt />);
    fireEvent.click(screen.getByText("授权并开工"));
    await waitFor(() => {
      expect(notifySubmitInteractionResult).toHaveBeenCalledWith("busy");
    });
  });

  it("队员任务默认折叠为一行摘要，点击可展开全文", () => {
    const longTask =
      "第一行调研公开竞品定价\n第二行整理对比表\n第三行给出建议区间";
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: longTask,
            depends_on: [],
          },
        ],
      }),
    ];
    render(<ResumePrompt />);

    const toggle = screen.getByRole("button", { name: "展开 研究员 任务" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.querySelector(".line-clamp-1")).toBeTruthy();
    expect(toggle.querySelector(".whitespace-pre-wrap")).toBeNull();

    fireEvent.click(toggle);
    const opened = screen.getByRole("button", { name: "收起 研究员 任务" });
    expect(opened.getAttribute("aria-expanded")).toBe("true");
    expect(opened.querySelector(".whitespace-pre-wrap")).toBeTruthy();
    expect(opened.querySelector(".line-clamp-1")).toBeNull();
    expect(opened.textContent).toContain("第三行给出建议区间");
  });

  it("限高滚动壳：内容区可滚、CTA 钉底", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: Array.from({ length: 8 }, (_, i) => ({
          run_id: `r${i}`,
          role: `队员${i}`,
          task: `任务说明 ${i}\n补充细节很多很多很多`,
          depends_on: [],
        })),
      }),
    ];
    const { container } = render(<ResumePrompt />);
    const shell = Array.from(container.querySelectorAll("div")).find((el) =>
      el.className.includes("max-h-[min(60vh,36rem)]"),
    );
    expect(shell).toBeTruthy();
    expect(shell?.className).toContain("overflow-hidden");
    expect(shell?.className).toContain("flex-col");

    const scroll = Array.from(container.querySelectorAll("div")).find(
      (el) =>
        el.className.includes("overflow-y-auto") &&
        el.className.includes("min-h-0") &&
        el.className.includes("flex-1"),
    );
    expect(scroll).toBeTruthy();

    expect(screen.getByText("授权并开工")).toBeTruthy();
    expect(screen.getByRole("button", { name: /加一句嘱咐/ })).toBeTruthy();
    expect(screen.queryByPlaceholderText(/开工时注入全体队员/)).toBeNull();
  });

  it("确认面无纳入/排除开关；continue 不带 excluded_run_ids", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: "调研",
            depends_on: [],
            write_capability: "can_write_files",
            write_capability_label: "可改文件",
          },
          {
            run_id: "r2",
            role: "撰写员",
            task: "写报告",
            depends_on: [],
            write_capability: "text_only",
            write_capability_label: "仅文字报告",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByLabelText(/纳入本轮/)).toBeNull();
    expect(screen.queryByTestId("team-preview-dep-block-hint")).toBeNull();
    fireEvent.click(screen.getByText("授权并开工"));
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.decision).toBe("continue");
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
  });

  it("可改文件→仅文字：continue 带 write_capability_overrides", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: "调研",
            depends_on: [],
            write_capability: "can_write_files",
            write_capability_label: "可改文件",
          },
          {
            run_id: "r2",
            role: "撰写员",
            task: "写报告",
            depends_on: [],
            write_capability: "can_write_files",
            write_capability_label: "可改文件",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    fireEvent.click(
      screen.getByRole("button", { name: "研究员 收紧为仅文字" }),
    );
    fireEvent.click(screen.getByText("授权并开工"));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "continue",
          write_capability_overrides: [
            { run_id: "r1", capability: "text_only" },
          ],
        }),
      }),
    );
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.excluded_run_ids).toBeUndefined();
  });

  it("stop 不带修正字段", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: "调研",
            depends_on: [],
            write_capability: "can_write_files",
            write_capability_label: "可改文件",
          },
          {
            run_id: "r2",
            role: "撰写员",
            task: "写报告",
            depends_on: [],
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    fireEvent.click(
      screen.getByRole("button", { name: "研究员 收紧为仅文字" }),
    );
    fireEvent.click(screen.getByText("取消"));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "stop",
        }),
      }),
    );
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
    expect(cold.model_overrides).toBeUndefined();
  });

  it("确认面无队员模型下拉；continue 不带 model_overrides", () => {
    pendingRef.current = [
      makeTeamPreview({
        workers: [
          {
            run_id: "r1",
            role: "研究员",
            task: "调研",
            depends_on: [],
            model: "ceo-flash",
            origin: "platform",
          },
          {
            run_id: "r2",
            role: "撰写员",
            task: "写报告",
            depends_on: [],
            model: "ceo-flash",
            origin: "platform",
          },
        ],
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.queryByTestId(/team-worker-model-/)).toBeNull();
    expect(screen.getAllByText("ceo-flash").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("授权并开工"));
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.decision).toBe("continue");
    expect(cold.model_overrides).toBeUndefined();
    expect(cold.excluded_run_ids).toBeUndefined();
  });
});

describe("ResumePrompt · team_preview debate", () => {
  beforeEach(() => {
    pendingRef.current = [
      makeTeamPreview({
        primitive: "debate",
        tools: [],
        workers: [],
        motion: "该不该上四天工作制？",
        sides: [
          { key: "pro", name: "正方", stance: "应推广" },
          { key: "con", name: "反方", stance: "暂缓" },
        ],
        maxRounds: 5,
      }),
    ];
  });

  it("三按钮：左取消 + 中调整 + 右授权开赛；无逐次审批 / 停止", () => {
    render(<ResumePrompt />);
    expect(screen.queryByText("等你确认 · 确认后才会开赛")).toBeNull();
    expect(screen.getByText("预计 2 方开赛")).toBeTruthy();
    const cancel = screen.getByRole("button", { name: "取消" });
    const adjust = screen.getByRole("button", { name: "调整" });
    const primary = screen.getByRole("button", { name: "授权开赛" });
    expect(
      cancel.compareDocumentPosition(adjust) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      adjust.compareDocumentPosition(primary) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByText("停止")).toBeNull();
    expect(screen.queryByText("逐次审批开工")).toBeNull();
    expect(screen.getByRole("button", { name: /加一句嘱咐/ })).toBeTruthy();
    expect(screen.queryByPlaceholderText(/开赛时注入各方/)).toBeNull();
    // cold Badge 与 hot DebateBody 共用 formatDebateBudgetLabel（含「上限」）
    expect(screen.getByText("认真辩透 · 上限 5 轮")).toBeTruthy();
  });

  it("辩论调整态不渲染开赛按钮，且不提交", () => {
    render(<ResumePrompt />);
    fireEvent.click(screen.getByRole("button", { name: "调整" }));
    expect(screen.getByTestId("team-preview-adjust-note")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "授权开赛" })).toBeNull();
    expect(submitInteraction).not.toHaveBeenCalled();
  });

  it("辩论调整态提交 adjust，不带修正字段", () => {
    render(<ResumePrompt />);
    enterAdjust("改辩题，先谈成本");
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "adjust",
          note: "改辩题，先谈成本",
        }),
      }),
    );
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
    expect(cold.model_overrides).toBeUndefined();
  });

  it("主按钮带嘱咐发 continue；辩论未改模型不附修正字段", () => {
    render(<ResumePrompt />);
    openKickoffNote();
    fireEvent.change(screen.getByPlaceholderText(/开赛时注入各方/), {
      target: { value: "最关心成本谁买单" },
    });
    fireEvent.click(screen.getByText("授权开赛"));
    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        cold: expect.objectContaining({
          decision: "continue",
          note: "最关心成本谁买单",
        }),
      }),
    );
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
    expect(cold.model_overrides).toBeUndefined();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("确认面无辩手/裁判模型下拉；有 run_id 时 continue 仍不带 model_overrides", () => {
    pendingRef.current = [
      makeTeamPreview({
        primitive: "debate",
        tools: [],
        workers: [],
        motion: "该不该上四天工作制？",
        sides: [
          {
            key: "pro",
            name: "正方",
            stance: "应推广",
            run_id: "side-pro",
            model: "ceo-flash",
            origin: "platform",
          },
          {
            key: "con",
            name: "反方",
            stance: "暂缓",
            run_id: "side-con",
            model: "ceo-flash",
            origin: "platform",
          },
        ],
        moderatorRunId: "mod-1",
        moderatorModel: "ceo-flash",
        moderatorOrigin: "platform",
        maxRounds: 5,
      }),
    ];
    render(<ResumePrompt />);
    expect(screen.queryByTestId(/team-worker-model-/)).toBeNull();
    expect(screen.queryByTestId(/debate-moderator-/)).toBeNull();
    fireEvent.click(screen.getByText("授权开赛"));
    const cold = submitInteraction.mock.calls[0][0].cold as Record<
      string,
      unknown
    >;
    expect(cold.decision).toBe("continue");
    expect(cold.model_overrides).toBeUndefined();
    expect(cold.excluded_run_ids).toBeUndefined();
    expect(cold.write_capability_overrides).toBeUndefined();
  });

  it("开工卡不再提供 research_first 第三键（庭前取证内化）", () => {
    render(<ResumePrompt />);
    expect(screen.queryByText("先多视角调研再辩")).toBeNull();

    pendingRef.current = [
      makeTeamPreview({
        primitive: "debate",
        tools: [],
        workers: [],
        motion: "该不该上四天工作制？",
        sides: [
          { key: "pro", name: "正方", stance: "应推广" },
          { key: "con", name: "反方", stance: "暂缓" },
        ],
        maxRounds: 5,
      }),
    ];
    cleanup();
    render(<ResumePrompt />);
    expect(screen.queryByText("先多视角调研再辩")).toBeNull();
    expect(screen.getByText("授权开赛")).toBeTruthy();
  });
});
