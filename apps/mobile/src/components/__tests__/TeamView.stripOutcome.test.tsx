// @vitest-environment jsdom
/**
 * Team strip is the primary turn verdict when the arbiter sets surface=strip.
 * Fold status==="failed" must not paint a second「失败」beside the bubble banner.
 */
import { TeamView } from "@/components/TeamView";
import {
  INTERRUPTED_STRIP_TITLE,
  PARTIAL_NOTICE,
  type TurnOutcome,
} from "@/lib/turnOutcome";
import type {
  ProjectedAgent,
  ProjectedRun,
} from "@agentcore/protocol-conformance";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

function makeAgent(
  p: Partial<ProjectedAgent> & { id: string; role: string },
): ProjectedAgent {
  return {
    thinking: false,
    status: "completed",
    currentRunId: null,
    output: "",
    reasoning: "",
    toolProgress: null,
    ...p,
  };
}

function makeRun(p: Partial<ProjectedRun> & { id: string }): ProjectedRun {
  return {
    agentId: "a1",
    task: "task",
    status: "completed",
    dependsOn: [],
    outputSummary: null,
    debrief: null,
    durationMs: null,
    error: null,
    failureKind: null,
    productLanded: null,
    parentRunId: null,
    kind: "agent",
    role: "队员",
    model: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    revised: null,
    replacesRunId: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    actId: "act-1",
    ...p,
  };
}

const AGENTS = [makeAgent({ id: "a1", role: "调研员" })];
const RUNS = [
  makeRun({
    id: "r1",
    status: "failed",
    productLanded: true,
  }),
];

function stripOutcome(partial: Partial<TurnOutcome>): TurnOutcome {
  return {
    kind: "error",
    notice: "模型调用失败，请重试。",
    reason: null,
    surface: "strip",
    recovery: { kind: "retry" },
    hideEmptyBubble: false,
    ...partial,
  };
}

describe("TeamView strip · arbiter owns the verdict", () => {
  it("partial outcome paints 部分完成, not 失败, even when fold status is failed", () => {
    render(
      <MemoryRouter>
        <TeamView
          agents={AGENTS}
          runs={RUNS}
          progress={{ completed: 0, total: 1 }}
          status="failed"
          outcome={stripOutcome({
            kind: "partial",
            notice: null,
            recovery: { kind: "retry" },
          })}
          supportIds={{ conversationId: "c1" }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(PARTIAL_NOTICE)).toBeTruthy();
    const titles = [...document.querySelectorAll(".team-strip-title")].map(
      (el) => el.textContent,
    );
    expect(titles.some((t) => t?.includes("失败"))).toBe(false);
    expect(
      screen.getByTestId("turn-outcome").getAttribute("data-surface"),
    ).toBe("strip");
    expect(screen.getByText("复制排查包")).toBeTruthy();
    expect(document.querySelector(".team-strip")?.textContent).not.toContain(
      "已交付",
    );
  });

  it("interrupted send_next keeps 已中断 on the strip; 排查包 follows composer", () => {
    render(
      <MemoryRouter>
        <TeamView
          agents={AGENTS}
          runs={RUNS}
          progress={{ completed: 0, total: 1 }}
          status="cancelled"
          outcome={stripOutcome({
            notice: "已中断。直接发送下一条即可重试。",
            surface: "composer",
            recovery: { kind: "send_next" },
          })}
          supportIds={{ conversationId: "c1", messageId: "m1" }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(INTERRUPTED_STRIP_TITLE)).toBeTruthy();
    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByText("复制排查包")).toBeNull();
    expect(screen.queryByTestId("turn-outcome")).toBeNull();
    expect(document.querySelector(".team-strip")?.textContent).not.toContain(
      "直接发送下一条",
    );
  });

  it("partial + composer hint: strip only paints 部分完成 战绩, no why or delivery summary", () => {
    render(
      <MemoryRouter>
        <TeamView
          agents={AGENTS}
          runs={RUNS}
          progress={{ completed: 0, total: 1 }}
          status="failed"
          elapsedMs={12_000}
          outcome={stripOutcome({
            kind: "partial",
            notice: "上游限流，暂时无法继续本回合。请约 4 秒后再试。",
            surface: "composer",
            recovery: { kind: "wait", retryAfterSec: 4 },
          })}
          supportIds={{ conversationId: "c1", messageId: "m1" }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(PARTIAL_NOTICE)).toBeTruthy();
    const strip = document.querySelector(".team-strip")?.textContent ?? "";
    expect(strip).toContain(PARTIAL_NOTICE);
    expect(strip).toContain("0/1");
    expect(strip).toContain("用时");
    expect(strip).not.toContain("上游限流");
    expect(strip).not.toContain("未能交付");
    expect(strip).not.toContain("已交付");
    expect(screen.queryByTestId("turn-outcome")).toBeNull();
    expect(screen.queryByText("复制排查包")).toBeNull();
  });
});
