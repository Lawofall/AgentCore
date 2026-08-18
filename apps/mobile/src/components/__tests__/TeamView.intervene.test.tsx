// @vitest-environment jsdom
/**
 * 手机端按人干预（只改这个人的方向 / 只停这个人）—— 点开队员就在手边。
 *
 * 之前手机上这两件事一处都没有：列表按人显示每个队员在干什么，能操作的却只有整轮停止。
 * 这里钉住两件事：入口在队员详情里够得着；run 终局时整条不渲染（不再变灰留着）。
 * 排队 pending 仍画：可停；改方向变灰 +「还没开工」（手机无 hover，原因必须是可见文字）。
 */
import { TeamView } from "@/components/TeamView";
import { resetRunStopPending } from "@/lib/runStopPending";
import type {
  ProjectedAgent,
  ProjectedRun,
} from "@agentcore/protocol-conformance";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** jsdom 无 showModal；与 StageCard / BrowserLiveSheet 同桩。 */
vi.mock("@/components/Modal", () => ({
  Modal: ({ children, label }: { children: ReactNode; label?: string }) => (
    <div aria-label={label}>{children}</div>
  ),
}));

const submitRunStop = vi.fn();
const submitRunRedirect = vi.fn();

vi.mock("@/api/runControl", () => ({
  submitRunStop: (...args: unknown[]) => submitRunStop(...args),
  submitRunRedirect: (...args: unknown[]) => submitRunRedirect(...args),
}));

const CID = "conv-1";
const EID = "exec-1";

/** 引擎受理了这次干预（服务端回执的正常形）。 */
const ACCEPTED = { queued: 1, accepted: true, reason: "queued", detail: "" };

/** 引擎够不着这个 run：什么都没入队。 */
const REFUSED = {
  queued: 0,
  accepted: false,
  reason: "no_live_drive",
  detail: "这批工作已经不在引擎手里了，没有能停的在跑队员。",
};

function makeAgent(
  p: Partial<ProjectedAgent> & { id: string; role: string },
): ProjectedAgent {
  return {
    thinking: false,
    status: "working",
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
    task: "调研竞品",
    status: "running",
    dependsOn: [],
    outputSummary: null,
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
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    actId: "act-1",
    ...p,
  };
}

const AGENTS = [makeAgent({ id: "a1", role: "调研员" })];

function openMember(
  opts: {
    runStatus?: ProjectedRun["status"];
    turnStatus?: "running" | "completed";
    conversationId?: string | null;
    executionId?: string | null;
  } = {},
) {
  const run = makeRun({ id: "r1", status: opts.runStatus ?? "running" });
  render(
    <TeamView
      agents={AGENTS}
      runs={[run]}
      progress={{ completed: 0, total: 1 }}
      status={opts.turnStatus ?? "running"}
      conversationId={
        opts.conversationId === undefined ? CID : opts.conversationId
      }
      executionId={opts.executionId === undefined ? EID : opts.executionId}
    />,
  );
  // 一次点击即达：列表上那张卡就是入口。
  fireEvent.click(screen.getByText("调研员"));
}

describe("TeamView 按人干预", () => {
  beforeEach(() => {
    submitRunStop.mockReset();
    submitRunStop.mockResolvedValue(ACCEPTED);
    submitRunRedirect.mockReset();
    submitRunRedirect.mockResolvedValue(ACCEPTED);
    resetRunStopPending();
  });

  afterEach(cleanup);

  it("跑着的队员：两个动作都在，且说清只作用于这一个人", () => {
    openMember();

    expect(screen.getByRole("button", { name: "改这个人的方向" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(screen.getByText(/不是「停止整轮」/)).toBeTruthy();
  });

  it.each(["completed", "failed", "cancelled", "skipped"] as const)(
    "终局 %s：整条不渲染、不写灰字原因",
    (status) => {
      openMember({ runStatus: status });

      expect(document.querySelector(".rd-intervene")).toBeNull();
      expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
      expect(
        screen.queryByRole("button", { name: /改这个人的方向/ }),
      ).toBeNull();
      expect(screen.queryByText(/这位队员已经跑完/)).toBeNull();
      expect(screen.queryByText(/没跑成/)).toBeNull();
      expect(screen.queryByText(/已经停下/)).toBeNull();
      expect(screen.queryByText(/没有执行/)).toBeNull();
    },
  );

  it("排队中的队员：可以停，但没有在跑的工作可改", () => {
    openMember({ runStatus: "pending" });

    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /^改这个人的方向（/ }),
    ).toBeTruthy();
    expect(screen.getByText(/还没开工/)).toBeTruthy();
  });

  it("停止只停这一个人，且不假装已经停了", async () => {
    openMember();

    fireEvent.click(screen.getByRole("button", { name: "停止这位队员" }));

    await waitFor(() => {
      expect(submitRunStop).toHaveBeenCalledWith(CID, {
        executionId: EID,
        runId: "r1",
      });
    });
    expect(screen.getByText(/等引擎确认后这位队员的状态才会变/)).toBeTruthy();
  });

  it("改方向就地写、就地提交", async () => {
    openMember();

    fireEvent.click(screen.getByRole("button", { name: "改这个人的方向" }));
    const box = screen.getByPlaceholderText("具体、可执行的修改方向…");
    fireEvent.change(box, { target: { value: "改用公开财报数据" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改方向" }));

    await waitFor(() => {
      expect(submitRunRedirect).toHaveBeenCalledWith(CID, {
        executionId: EID,
        runId: "r1",
        feedback: "改用公开财报数据",
      });
    });
  });

  it("没有会话 / 没有图时不出条（结构上无从提交，与「来晚了」无关）", () => {
    openMember({ executionId: null });

    expect(screen.queryByRole("button", { name: /停止这位队员/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /改这个人的方向/ })).toBeNull();
  });

  // 气泡收口了不代表引擎够不着：团队转后台跑时驱动循环照样在排干 redirect。
  // 以前这里看 `status === "running"` 自己猜，猜错就把还能改的人说成「改不动了」。
  it("整轮气泡收口后，还在跑的队员照样能改方向（够不够得着由服务端答）", () => {
    openMember({ turnStatus: "completed" });

    const redirect = screen.getByRole("button", { name: "改这个人的方向" });
    expect(redirect.getAttribute("aria-disabled")).toBeNull();
  });

  it("引擎说够不着：照它的原话说，不留「停止请求中…」", async () => {
    submitRunStop.mockResolvedValue(REFUSED);
    openMember();

    fireEvent.click(screen.getByRole("button", { name: "停止这位队员" }));

    await waitFor(() => {
      expect(screen.getByText(REFUSED.detail)).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "停止这位队员" })).toBeTruthy();
    expect(screen.queryByText(/等引擎确认后这位队员的状态才会变/)).toBeNull();
  });

  it("引擎说改不到：不许说成已改，草稿留在原处让用户改投别处", async () => {
    submitRunRedirect.mockResolvedValue({
      ...REFUSED,
      reason: "unknown_run",
      detail: "引擎当前的计划里没有这位队员，改不到他。",
    });
    openMember();

    fireEvent.click(screen.getByRole("button", { name: "改这个人的方向" }));
    const box = screen.getByPlaceholderText("具体、可执行的修改方向…");
    fireEvent.change(box, { target: { value: "改用公开财报数据" } });
    fireEvent.click(screen.getByRole("button", { name: "提交改方向" }));

    await waitFor(() => {
      expect(
        screen.getByText("引擎当前的计划里没有这位队员，改不到他。"),
      ).toBeTruthy();
    });
    expect(
      (
        screen.getByPlaceholderText(
          "具体、可执行的修改方向…",
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("改用公开财报数据");
  });

  // 在飞态活在组件外：关掉详情再打开，按钮仍是「停止请求中…」，同一条请求发不出第二遍。
  it("停止在飞态跨面板卸载存活（重开详情不能再发一遍）", async () => {
    openMember();

    fireEvent.click(screen.getByRole("button", { name: "停止这位队员" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "停止请求中…" })).toBeTruthy();
    });

    // 关掉队员详情再点开同一位。
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByText("调研员"));

    const reopened = screen.getByRole("button", { name: "停止请求中…" });
    expect((reopened as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(reopened);
    expect(submitRunStop).toHaveBeenCalledTimes(1);
  });
});
