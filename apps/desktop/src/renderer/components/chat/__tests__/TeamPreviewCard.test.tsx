// @vitest-environment jsdom
/**
 * 开工卡被动记录：默认一行结论收起，点开才看队员明细；
 * resolved 摘要文案与各 decision label 对齐；pending 不占时间线。
 */

import { conversationKeys } from "@/lib/queryKeys";
import type { TeamPreviewDisplay } from "@/stores/conversation";
import { type ExecutionPlan, useExecutionStore } from "@/stores/execution";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  type RenderResult,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TeamPreviewCard } from "../TeamPreviewCard";

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: (_key: string | null, initial: boolean) => {
    const { useState } = require("react");
    return useState(initial);
  },
}));

function makePreview(
  overrides: Partial<TeamPreviewDisplay> = {},
): TeamPreviewDisplay {
  return {
    id: "tp-1",
    primitive: "delegate",
    workers: [
      {
        run_id: "r1",
        role: "研究员",
        task: "调研竞品定价策略与公开资料",
        depends_on: [],
      },
      {
        run_id: "r2",
        role: "撰写员",
        task: "基于调研写定价建议",
        depends_on: ["r1"],
      },
    ],
    tools: [],
    motion: "",
    form: "",
    sides: [],
    maxRounds: 0,
    thorough: true,
    status: "resolved",
    decision: "continue",
    note: "",
    ...overrides,
  };
}

function renderCard(ui: ReactElement): RenderResult {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
    },
  });
  client.setQueryData(conversationKeys.grouped, {
    folders: [],
    conversations: [],
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const MID = "msg-tp-debate-host";

const debatePlan: ExecutionPlan = {
  id: "exec-debate-host",
  planType: "multi_agent",
  taskSummary: "辩论",
  agents: [
    { id: "a", role: "正方" },
    { id: "b", role: "反方" },
  ],
  runs: [
    { id: "r1", agentId: "a", task: "立论", dependsOn: [] },
    { id: "r2", agentId: "b", task: "反驳", dependsOn: [] },
  ],
};

function makeDebatePreview(
  overrides: Partial<TeamPreviewDisplay> = {},
): TeamPreviewDisplay {
  return makePreview({
    primitive: "debate",
    workers: [],
    motion: "该不该上四天工作制？",
    form: "debate",
    sides: [
      { key: "pro", name: "正方", stance: "应推广" },
      { key: "con", name: "反方", stance: "暂缓" },
    ],
    maxRounds: 5,
    thorough: true,
    status: "resolved",
    decision: "continue",
    note: "",
    ...overrides,
  });
}

afterEach(() => {
  cleanup();
  useExecutionStore.setState({ byId: {} });
  vi.restoreAllMocks();
});

describe("TeamPreviewCard", () => {
  it("resolved 默认收起为一行结论，不含队员任务全文", () => {
    renderCard(<TeamPreviewCard preview={makePreview()} />);

    const toggle = screen.getByRole("button", {
      name: /已授权开工 · 首波已放行 · 2 人/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("研究员")).toBeNull();
    expect(screen.queryByText("调研竞品定价策略与公开资料")).toBeNull();
  });

  it("点击展开后显示队员角色、任务与依赖", () => {
    renderCard(<TeamPreviewCard preview={makePreview()} />);

    fireEvent.click(
      screen.getByRole("button", {
        name: /已授权开工 · 首波已放行 · 2 人/,
      }),
    );

    expect(
      screen
        .getByRole("button", {
          name: /已授权开工 · 首波已放行 · 2 人/,
        })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.getByText("撰写员")).toBeTruthy();
    expect(screen.getByText("调研竞品定价策略与公开资料")).toBeTruthy();
    expect(screen.getByText("基于调研写定价建议")).toBeTruthy();
    expect(screen.getByText("依赖 1 步")).toBeTruthy();
    expect(screen.queryByText("辩论")).toBeNull();
  });

  it("resolved 展开后显示备注 note", () => {
    renderCard(
      <TeamPreviewCard
        preview={makePreview({ note: "先做公开竞品，不做内部访谈" })}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: /已授权开工 · 嘱咐已注入队员 · 2 人/,
      }),
    );
    expect(screen.getByText("先做公开竞品，不做内部访谈")).toBeTruthy();
  });

  it("pending 不占时间线（对齐 ask_user；可操作面只在拍板卡）", () => {
    const { container } = renderCard(
      <TeamPreviewCard
        preview={makePreview({
          status: "pending",
          decision: null,
          headline: "MVP主流程 · 预计 2 人",
        })}
      />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByText("研究员")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("resolved 缺 decision 不猜成超时", () => {
    renderCard(<TeamPreviewCard preview={makePreview({ decision: null })} />);
    expect(screen.getByRole("button", { name: /已经处理过了/ })).toBeTruthy();
    expect(screen.queryByText(/未及时回应/)).toBeNull();
  });

  it.each([
    ["adjust", "已调整 · 已交回修订 · 预计 2 人开工"],
    ["stop", "已取消 · 团队未启动 · 预计 2 人开工"],
    ["timeout", "未及时回应，团队未启动 · 预计 2 人开工"],
    ["orphaned", "已失效（回合已结束或服务已重启） · 预计 2 人开工"],
  ] as const)("resolved decision=%s 保留既有 label 文案", (decision, label) => {
    renderCard(<TeamPreviewCard preview={makePreview({ decision })} />);
    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  });

  it("debate resolved timeout 显示辩论未开赛文案", () => {
    renderCard(
      <TeamPreviewCard preview={makeDebatePreview({ decision: "timeout" })} />,
    );
    expect(
      screen.getByRole("button", {
        name: /未及时回应，辩论未开赛 · 预计 2 方开赛/,
      }),
    ).toBeTruthy();
  });

  it("debate resolved research_first 显示已选先调研文案", () => {
    renderCard(
      <TeamPreviewCard
        preview={makeDebatePreview({ decision: "research_first" })}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /已选先调研 · 辩论未开赛/,
      }),
    ).toBeTruthy();
  });

  it("resolved continue + 排除/收紧对账后缀", () => {
    renderCard(
      <TeamPreviewCard
        preview={makePreview({
          decision: "continue",
          excluded_run_ids: ["r2"],
          write_capability_overrides: [
            { run_id: "r1", capability: "text_only" },
          ],
        })}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /已授权开工 · 首波已放行 · 已排除 1 岗 · 已收紧写盘 · 2 人/,
      }),
    ).toBeTruthy();
  });

  it("resolved continue + note 显示嘱咐已注入", () => {
    renderCard(
      <TeamPreviewCard
        preview={makePreview({
          decision: "continue",
          note: "先做公开竞品",
        })}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /已授权开工 · 嘱咐已注入队员 · 2 人/,
      }),
    ).toBeTruthy();
  });

  it("debate resolved continue + note 显示嘱咐已注入", () => {
    renderCard(
      <TeamPreviewCard
        preview={makePreview({
          primitive: "debate",
          workers: [],
          decision: "continue",
          note: "最关心成本谁买单",
          motion: "该不该上四天工作制？",
          form: "debate",
          sides: [
            { key: "pro", name: "正方", stance: "应推广" },
            { key: "con", name: "反方", stance: "暂缓" },
          ],
          maxRounds: 5,
          thorough: true,
        })}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /已授权开赛 · 嘱咐已注入 · 2 方/,
      }),
    ).toBeTruthy();
  });

  it("debate resolved adjust 渲染交回修订，展开可见意见原文", () => {
    renderCard(
      <TeamPreviewCard
        preview={makePreview({
          primitive: "debate",
          workers: [],
          decision: "adjust",
          note: "旧路径改辩题",
          motion: "原辩题",
          form: "debate",
          sides: [
            { key: "pro", name: "正方", stance: "应推广" },
            { key: "con", name: "反方", stance: "暂缓" },
          ],
          maxRounds: 5,
          thorough: true,
        })}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: /已调整 · 已交回修订 · 预计 2 方开赛/,
    });
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle);
    expect(screen.getByText("旧路径改辩题")).toBeTruthy();
  });

  it("delegate resolved adjust 展开可见意见原文，编制已在也不藏卡", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    renderCard(
      <TeamPreviewCard
        preview={makePreview({
          decision: "adjust",
          note: "改成两人，先做竞品",
        })}
        messageId={MID}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: /已调整 · 已交回修订 · 预计 2 人开工/,
    });
    fireEvent.click(toggle);
    expect(screen.getByText("改成两人，先做竞品")).toBeTruthy();
  });

  it("debate pending 不占时间线（辩题立场只在拍板卡）", () => {
    const { container } = renderCard(
      <TeamPreviewCard
        preview={makePreview({
          primitive: "debate",
          workers: [],
          status: "pending",
          decision: null,
          motion: "该不该上四天工作制？",
          form: "debate",
          sides: [
            { key: "pro", name: "正方", stance: "应推广" },
            { key: "con", name: "反方", stance: "暂缓" },
          ],
          maxRounds: 5,
          thorough: true,
        })}
      />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByText("该不该上四天工作制？")).toBeNull();
    expect(screen.queryByText("正方")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("debate resolved continue + 编制已在 → 独立卡隐藏（图接管，不必等开跑）", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    const { container } = renderCard(
      <TeamPreviewCard preview={makeDebatePreview()} messageId={MID} />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("debate resolved + 协作图已出现时独立卡隐藏", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    useExecutionStore.getState().recordFrame(
      {
        t: 1,
        kind: "run_started",
        runId: "r1",
        agentId: "a",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      MID,
    );
    const { container } = renderCard(
      <TeamPreviewCard preview={makeDebatePreview()} messageId={MID} />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("delegate resolved + 协作图已出现时独立卡隐藏", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    useExecutionStore.getState().recordFrame(
      {
        t: 1,
        kind: "run_started",
        runId: "r1",
        agentId: "a",
        parentRunId: null,
        runKind: "agent",
        continuesRunId: null,
      },
      MID,
    );
    const { container } = renderCard(
      <TeamPreviewCard preview={makePreview()} messageId={MID} />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("delegate resolved continue + 编制已在 → 独立卡隐藏（图接管，不必等开跑）", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    const { container } = renderCard(
      <TeamPreviewCard preview={makePreview()} messageId={MID} />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("delegate resolved stop + 从未开跑 → 独立卡仍兜底", () => {
    useExecutionStore.getState().startExecution(debatePlan, MID);
    renderCard(
      <TeamPreviewCard
        preview={makePreview({ decision: "stop" })}
        messageId={MID}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /已取消 · 团队未启动 · 预计 2 人开工/,
      }),
    ).toBeTruthy();
  });
});
