// @vitest-environment jsdom
import { PreviewPage } from "@/pages/PreviewPage";
import type { PreviewFixture } from "@/preview/fixtures";
import type { ProjectedTurn } from "@agentcore/protocol-conformance";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/preview/fixtures", () => ({
  PREVIEW_FIXTURES: [],
}));

afterEach(() => {
  cleanup();
});

function projected(partial: Partial<ProjectedTurn>): ProjectedTurn {
  return {
    status: "completed",
    finishReason: "end_turn",
    outcome: "ok",
    error: null,
    content: "",
    reasoning: "",
    captainContext: [],
    process: [],
    citations: [],
    evidenceLedger: [],
    citedIds: [],
    agents: [],
    runs: [],
    acts: [],
    progress: { completed: 0, total: 0 },
    interactions: [],
    cost: null,
    debate: null,
    debateRounds: [],
    debatePretrial: null,
    crossExamEnabled: false,
    debateOpening: null,
    teamSynthesisPreview: null,
    deliveryStatus: null,
    turnWarning: null,
    autoFolder: null,
    userInterjections: [],
    ...partial,
  };
}

const FIXTURES: PreviewFixture[] = [
  {
    name: "plain_ok",
    description: "纯文本终态",
    projected: projected({ content: "你好管理员" }),
  },
  {
    name: "paused_gate",
    description: "闸卡暂停",
    projected: projected({
      status: "paused",
      finishReason: null,
      outcome: null,
      content: "需要批准",
      interactions: [
        {
          kind: "approval",
          id: "ap1",
          status: "pending",
          toolCallId: "tc1",
          toolName: "bash",
          arguments: {},
        },
      ],
    }),
  },
  {
    name: "team_worker",
    description: "多 Agent 队员过程",
    projected: projected({
      content: "综合结论",
      runs: [
        {
          id: "r1",
          agentId: "researcher",
          task: "查资料",
          status: "completed",
          dependsOn: [],
          outputSummary: "调研摘要",
          debrief: null,
          durationMs: null,
          error: null,
          failureKind: null,
          productLanded: null,
          parentRunId: null,
          kind: "agent",
          role: "调研员",
          model: null,
          usage: null,
          cost: null,
          stance: null,
          group: null,
          round: 0,
          continuesRunId: null,
          revised: null,
          replacesRunId: null,
          actId: "act-1",
          checkpoint: null,
          receivedContext: [],
          escalations: [],
          process: [
            { kind: "reasoning", text: "先搜公开材料。" },
            {
              kind: "tool",
              id: "t1",
              tool_name: "web_search",
              arguments: {},
              result: null,
              status: "success",
            },
          ],
        },
      ],
      progress: { completed: 1, total: 1 },
    }),
  },
];

function renderPreview(path = "/preview") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/preview"
          element={<PreviewPage fixtures={FIXTURES} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PreviewPage", () => {
  it("renders the selected vector's golden projected", () => {
    renderPreview("/preview?s=plain_ok");
    expect(screen.getByLabelText("场景")).toBeTruthy();
    expect(screen.getByText("纯文本终态")).toBeTruthy();
    expect(screen.getAllByText("你好管理员").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("对话终态")).toBeTruthy();
  });

  it("switches scenarios from the picker", () => {
    renderPreview("/preview?s=plain_ok");
    fireEvent.change(screen.getByLabelText("场景"), {
      target: { value: "paused_gate" },
    });
    expect(screen.getByText("闸卡暂停")).toBeTruthy();
    expect(screen.getAllByText("需要批准").length).toBeGreaterThan(0);
    expect(screen.getByText("审批")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("opens a worker process dock from the team graph", () => {
    renderPreview("/preview?s=team_worker");
    fireEvent.click(screen.getByText("调研员"));
    expect(screen.getByLabelText("队员过程")).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByText("调研摘要")).toBeTruthy();
  });
});
