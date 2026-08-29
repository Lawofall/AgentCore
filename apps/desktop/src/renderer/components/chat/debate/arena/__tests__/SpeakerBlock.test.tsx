// @vitest-environment jsdom
/**
 * 立论块「展开全文」交互：
 * - 默认论点列表视图，收起态只有头行一枚「展开全文」（对齐质询区，无底部冗余入口）；
 * - 展开后头行 + 底部各一枚「收起全文」，共享同一 showAll，任一处收起都同步；
 * - 全文视图直接渲染 Markdown，不再套 CollapsibleSpeech（无 max-h-72 夹层回归锁）。
 */

import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DebateRoundModel, DebateSideModel } from "../../model";
import { SpeakerBlock } from "../SpeakerBlock";

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

/** 可被 parseSpeechArguments 拆成多条论点的结构化发言。 */
const STRUCTURED_OUTPUT = [
  "1. 第一论点：平台应承担尾部风险。",
  "2. 第二论点：熔断机制保护用户。",
].join("\n");

function speechRun(id = "mod_r1_pro"): RunNode {
  return {
    id,
    agentId: id,
    status: "completed",
    kind: "agent",
    parentRunId: null,
    continuesRunId: null,
    receivedContext: [],
  } as unknown as RunNode;
}

function executionWith(agents: Partial<AgentState>[]): Execution {
  return {
    status: "completed",
    runs: [],
    agents: agents as AgentState[],
    frames: [],
    debate: null,
    debateRounds: [],
  } as unknown as Execution;
}

function sideModel(overrides: Partial<DebateSideModel> = {}): DebateSideModel {
  return {
    key: "mod_r1_pro",
    sideKey: "pro",
    name: "支持方",
    stance: "pro",
    colorVar: "var(--debate-pro)",
    model: "",
    run: speechRun(),
    ...overrides,
  };
}

function roundModel(sides: DebateSideModel[]): DebateRoundModel {
  return {
    roundNo: 1,
    focus: "",
    summary: "",
    verdict: null,
    sides,
    clashes: [],
    inFlight: false,
    userInterjections: [],
    crossExam: [],
    witnessExam: [],
    scores: [],
    findings: [],
    threadTurns: [],
  };
}

function renderBlock(output = STRUCTURED_OUTPUT) {
  const side = sideModel();
  const execution = executionWith([
    { id: "mod_r1_pro", outputChunks: [output] },
  ]);
  return render(
    <SpeakerBlock
      side={side}
      round={roundModel([side])}
      execution={execution}
      messageId="m1"
      stage="立论"
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SpeakerBlock 展开全文", () => {
  it("默认渲染论点列表，不直接展示全文 Markdown", () => {
    renderBlock();

    // 论点标题在折叠行按钮上（aria-expanded=false）；正文 Markdown 也挂着但折叠。
    expect(
      screen.getByRole("button", { name: /第一论点/, expanded: false }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /第二论点/, expanded: false }),
    ).toBeTruthy();
    expect(
      screen
        .queryAllByTestId("markdown")
        .every((el) => el.textContent !== STRUCTURED_OUTPUT),
    ).toBe(true);
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
  });

  it("头行展开、展开态头行/底部两枚收起都同步", () => {
    renderBlock();

    // 收起态只有头行一枚「展开全文」。
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();

    // 点头行展开 → 头行 + 底部各一枚「收起全文」，全文 Markdown 出现。
    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
    expect(screen.getByTestId("markdown").textContent).toBe(STRUCTURED_OUTPUT);
    expect(screen.getAllByRole("button", { name: "收起全文" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "展开全文" })).toBeNull();
    // 论点列表行已卸下。
    expect(screen.queryByRole("button", { name: /第一论点/ })).toBeNull();

    // 点底部「收起全文」→ 回到列表，头行变回「展开全文」。
    fireEvent.click(screen.getAllByRole("button", { name: "收起全文" })[1]);
    expect(
      screen
        .queryAllByTestId("markdown")
        .every((el) => el.textContent !== STRUCTURED_OUTPUT),
    ).toBe(true);
    expect(
      screen.getByRole("button", { name: /第一论点/, expanded: false }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();

    // 再次头行展开，改用头行「收起全文」收起，确认双向同步。
    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
    expect(screen.getByTestId("markdown").textContent).toBe(STRUCTURED_OUTPUT);
    fireEvent.click(screen.getAllByRole("button", { name: "收起全文" })[0]);
    expect(
      screen
        .queryAllByTestId("markdown")
        .every((el) => el.textContent !== STRUCTURED_OUTPUT),
    ).toBe(true);
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
  });

  it("全文视图下 DOM 无 max-h-72 夹层容器（双层折叠回归锁）", () => {
    const { container } = renderBlock();

    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));

    expect(screen.getByTestId("markdown").textContent).toBe(STRUCTURED_OUTPUT);
    expect(container.querySelector(".max-h-72")).toBeNull();
    // CollapsibleSpeech 展开后文案是「收起」，不应出现。
    expect(screen.queryByRole("button", { name: "收起" })).toBeNull();
  });
});

describe("SpeakerBlock 论点行标题", () => {
  const LONG_TITLE = "论点一：四叶花卉是公共元素，但LV的Monogram是独创作品";
  const LONG_SPEECH = [
    `### ${LONG_TITLE}`,
    "正文说明公共元素与独创作品的界限。",
  ].join("\n");

  it("收起态 CSS truncate；展开后标题可换行显示全文", () => {
    renderBlock(LONG_SPEECH);

    const row = screen.getByRole("button", {
      name: new RegExp(LONG_TITLE.slice(0, 8)),
      expanded: false,
    });
    const titleSpan = row.querySelector("span.min-w-0");
    expect(titleSpan?.textContent).toBe(LONG_TITLE);
    expect(titleSpan?.className).toContain("truncate");
    expect(titleSpan?.className).not.toContain("whitespace-normal");

    fireEvent.click(row);

    const openRow = screen.getByRole("button", {
      name: new RegExp(LONG_TITLE.slice(0, 8)),
      expanded: true,
    });
    const openSpan = openRow.querySelector("span.min-w-0");
    expect(openSpan?.textContent).toBe(LONG_TITLE);
    expect(openSpan?.className).toContain("whitespace-normal");
    expect(openSpan?.className).not.toContain("truncate");
  });

  it("结构化 title 已截断时，用 output 重水合大纲完整标题", () => {
    const full1 = "论点一：四叶花卉是公共元素，但LV的Monogram是独创作品";
    const full2 = "论点二：LV四叶花图案经长期使用已获得“第二含义”";
    const output = [
      `### ${full1}`,
      "正文甲。",
      "",
      `### ${full2}`,
      "正文乙。",
    ].join("\n");
    const side = sideModel({
      arguments: [
        {
          id: "a1",
          title: "论点一：四叶花卉是公共元素，但LV的Monogram是…",
          body: "落盘正文甲",
        },
        {
          id: "a2",
          title: "论点二：LV四叶花图案经长期使用已获得…",
          body: "落盘正文乙",
        },
      ],
    });
    const execution = executionWith([
      { id: "mod_r1_pro", outputChunks: [output] },
    ]);
    render(
      <SpeakerBlock
        side={side}
        round={roundModel([side])}
        execution={execution}
        messageId="m1"
        stage="立论"
      />,
    );

    expect(
      screen.getByRole("button", { name: /独创作品/, expanded: false }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /第二含义/, expanded: false }),
    ).toBeTruthy();
    // 大纲按钮文案含完整标题，而非落盘截断态
    expect(screen.queryByRole("button", { name: /Monogram是…$/ })).toBeNull();
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
  });
});
