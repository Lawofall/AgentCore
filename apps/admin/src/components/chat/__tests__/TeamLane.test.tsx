// @vitest-environment jsdom
import type { NormalizedRun } from "@/components/chat/chatTurn";
import { TeamLane } from "@/components/chat/TeamLane";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

function run(partial: Partial<NormalizedRun> & Pick<NormalizedRun, "id" | "status">): NormalizedRun {
  return {
    agentId: partial.agentId ?? partial.id,
    role: partial.role ?? "调研员",
    task: partial.task ?? "查资料",
    kind: partial.kind ?? "agent",
    parentRunId: partial.parentRunId ?? null,
    outputSummary: "",
    error: "",
    debriefSummary: "",
    process: [],
    ...partial,
  };
}

function reportCopy() {
  expect(screen.queryByText("协作已完成")).toBeNull();
  expect(screen.queryByText("部分失败")).toBeNull();
  expect(screen.queryByText("已停止")).toBeNull();
  expect(screen.queryByText("执行中")).toBeNull();
  expect(screen.queryByText(/^协作$/)).toBeNull();
  expect(screen.queryByText("完成")).toBeNull();
}

describe("TeamLane", () => {
  it("all-complete bar is icon + n/m only (no 协作已完成)", () => {
    render(
      <TeamLane
        runs={[run({ id: "r1", status: "completed" })]}
        progress={{ completed: 1, total: 1 }}
      />,
    );
    const team = screen.getByLabelText("团队");
    expect(team.textContent).toContain("1/1");
    expect(screen.getByText("1/1")).toBeTruthy();
    expect(screen.getByLabelText("完成")).toBeTruthy();
    reportCopy();
    expect(screen.getByText("调研员")).toBeTruthy();
    expect(screen.getByText("查资料")).toBeTruthy();
  });

  it("failed bar uses X + n/m, no 部分失败", () => {
    render(
      <TeamLane
        runs={[
          run({ id: "r1", status: "completed" }),
          run({ id: "r2", status: "failed", role: "写手", task: "起草" }),
        ]}
        progress={{ completed: 1, total: 2 }}
      />,
    );
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.getByLabelText("失败")).toBeTruthy();
    reportCopy();
    expect(screen.getByText("写手")).toBeTruthy();
  });

  it("running bar uses spinner + n/m, no 执行中 copy", () => {
    render(
      <TeamLane
        runs={[run({ id: "r1", status: "running" })]}
        progress={{ completed: 0, total: 1 }}
      />,
    );
    expect(screen.getByText("0/1")).toBeTruthy();
    expect(screen.getByLabelText("执行中")).toBeTruthy();
    reportCopy();
  });

  it("cancelled bar uses a distinct stop icon + n/m, no 已停止 copy", () => {
    render(
      <TeamLane
        runs={[run({ id: "r1", status: "cancelled" })]}
        progress={{ completed: 0, total: 1 }}
      />,
    );
    expect(screen.getByText("0/1")).toBeTruthy();
    expect(screen.getByLabelText("已停止")).toBeTruthy();
    reportCopy();
  });

  it("clicking a node opens the dock via onSelectRun", () => {
    const onSelectRun = vi.fn();
    render(
      <TeamLane
        runs={[run({ id: "r1", status: "completed" })]}
        progress={{ completed: 1, total: 1 }}
        onSelectRun={onSelectRun}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onSelectRun).toHaveBeenCalledWith("r1");
  });
});
