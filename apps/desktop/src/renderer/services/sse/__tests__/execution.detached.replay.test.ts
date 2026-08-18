// @vitest-environment jsdom
import { replayFixturePrefix } from "@/preview/replay";
import { dispatchSSEEvent } from "@/services/sse/dispatch";
import * as refreshMod from "@/services/sse/refreshAfterBackgroundExecution";
import { getRuntime, getTurnPhase } from "@/stores/conversation";
import {
  execRuntime,
  projectRuntime,
  useExecutionStore,
} from "@/stores/execution";
import { loadFixtures } from "@agentcore/protocol-conformance";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const NAME = "multi_agent_execution_detached_harvest_settle";
const CID = `preview-${NAME}`;
const MID = "m1";

function fixtureEvents() {
  const fx = loadFixtures().find((f) => f.name === NAME);
  if (!fx) throw new Error(`missing fixture ${NAME}`);
  return fx.events;
}

function countThrough(type: string): number {
  const idx = fixtureEvents().findIndex((e) => e.type === type);
  if (idx < 0) throw new Error(`fixture missing ${type}`);
  return idx + 1;
}

const rt = () => execRuntime(useExecutionStore.getState(), MID);

beforeEach(() => {
  vi.spyOn(refreshMod, "refreshAfterBackgroundExecution").mockImplementation(
    () => {},
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("execution_detached live progress · harvest_settle replay", () => {
  it("message_end 后队员 tool_use_* 仍入图相位，不进船长气泡", () => {
    const events = fixtureEvents();
    replayFixturePrefix(CID, events, countThrough("message_end"));

    expect(getTurnPhase(CID)).toBe("completed");
    expect(rt().executionDetached).toBeTruthy();
    expect(rt().status).toBe("running");
    expect(projectRuntime(rt())?.progress).toEqual({ completed: 0, total: 1 });

    const beforeProcess = getRuntime(CID).messages.find(
      (m) => m.serverMessageId === MID || m.id === MID,
    )?.process;

    dispatchSSEEvent(
      {
        type: "tool_use_start",
        timestamp: "",
        payload: {
          tool_call_id: "tc-w1",
          tool_name: "web_search",
          arguments: { query: "x" },
          run_id: "r1",
        },
      },
      { conversationId: CID, source: "server" },
    );
    dispatchSSEEvent(
      {
        type: "tool_use_progress",
        timestamp: "",
        payload: {
          tool_call_id: "tc-w1",
          tool_name: "web_search",
          phase: "querying",
          run_id: "r1",
        },
      },
      { conversationId: CID, source: "server" },
    );

    expect(rt().workerToolPhases).toEqual({
      r1: { phase: "querying", toolName: "web_search" },
    });
    const exec = projectRuntime(rt());
    const worker = exec?.agents.find((a) => a.id === "w1");
    expect(worker?.toolExecutionLive).toEqual({
      toolName: "web_search",
      phase: "querying",
    });
    expect(
      exec?.runs
        .find((r) => r.id === "r1")
        ?.process.some(
          (s) => s.kind === "tool" && s.tool_name === "web_search",
        ),
    ).toBe(true);

    const afterProcess = getRuntime(CID).messages.find(
      (m) => m.serverMessageId === MID || m.id === MID,
    )?.process;
    expect(afterProcess).toEqual(beforeProcess);
    expect(
      afterProcess?.some(
        (s) => s.kind === "tool" && s.tool_name === "web_search",
      ),
    ).toBeFalsy();
  });

  it("CEO tool_use_start 在 terminal 仍被挡（内容突变）", () => {
    replayFixturePrefix(CID, fixtureEvents(), countThrough("message_end"));
    const before = getRuntime(CID).messages.find(
      (m) => m.serverMessageId === MID || m.id === MID,
    )?.process;

    dispatchSSEEvent(
      {
        type: "tool_use_start",
        timestamp: "",
        payload: {
          tool_call_id: "tc-ceo",
          tool_name: "web_search",
          arguments: { query: "x" },
        },
      },
      { conversationId: CID, source: "server" },
    );

    const after = getRuntime(CID).messages.find(
      (m) => m.serverMessageId === MID || m.id === MID,
    )?.process;
    expect(after).toEqual(before);
    expect(rt().workerToolPhases).toEqual({});
  });

  it("run_completed 段 live n/m 从 0/1 变为 1/1（不冻 detached 快照）", () => {
    const events = fixtureEvents();
    replayFixturePrefix(CID, events, countThrough("message_end"));
    expect(projectRuntime(rt())?.progress).toEqual({ completed: 0, total: 1 });

    replayFixturePrefix(CID, events, countThrough("run_completed"));
    expect(projectRuntime(rt())?.progress).toEqual({ completed: 1, total: 1 });
    expect(projectRuntime(rt())?.runs.find((r) => r.id === "r1")?.status).toBe(
      "completed",
    );
  });
});
