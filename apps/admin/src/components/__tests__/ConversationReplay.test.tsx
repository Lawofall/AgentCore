// @vitest-environment jsdom
/**
 * Layout pins for 会话复盘, and the contract for the `?turn=` anchor.
 *
 * Three layout regressions live here. (1) The narrow and wide layouts were two separate
 * subtrees, so every bubble, team graph and worker dock was mounted twice — double the
 * effects, and expanding a section in one copy did nothing to the other. (2) The panes
 * were sized with `calc(100vh - 11rem)`, a number that was already wrong the day the
 * shell moved to `h-dvh` with its own scroll container. (3) The dock was a fixed 480px
 * (now 400, matching the desktop side panel default) with no way — mouse or keyboard —
 * to give a long worker transcript more room.
 *
 * The anchor is the fourth: which turn was open lived only in component state, so the
 * best link an operator could hand a colleague pointed at a whole conversation, and a
 * reload dropped them back at the top of it.
 */

import { ConversationReplay } from "@/components/ConversationReplay";
import type {
  AdminConversationReplay,
  ReplayMessage,
  ReplayRun,
} from "@/services/adminObservability";
import {
  fetchConversationReplay,
  fetchReplayTurnFinalState,
} from "@/services/adminObservability";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminObservability", () => ({
  fetchConversationReplay: vi.fn(),
  fetchReplayTurnFinalState: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/**
 * Elements the timeline scrolled to. jsdom ships no layout engine and therefore no
 * `scrollIntoView` at all, so "landed on the anchored turn" is invisible unless the
 * method is stood up and asked what it was called on.
 */
const scrolledInto: HTMLElement[] = [];

beforeEach(() => {
  window.localStorage.clear();
  scrolledInto.length = 0;
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: function scrollIntoViewStub(this: HTMLElement) {
      scrolledInto.push(this);
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function run(p: Partial<ReplayRun> & { run_id: string }): ReplayRun {
  return {
    agent_id: p.agent_id ?? p.run_id,
    content: null,
    debrief: null,
    depends_on: [],
    error: null,
    kind: "agent",
    output_summary: null,
    parent_run_id: null,
    role: null,
    status: "completed",
    task: "",
    ...p,
  };
}

function msg(
  p: Partial<ReplayMessage> & { id: string; role: string },
): ReplayMessage {
  return {
    content: null,
    cost_total: 0,
    created_at: "2026-08-01T00:00:00Z",
    credential_source: null,
    harvest_kind: null,
    metrics: null,
    models: [],
    origin: null,
    runs: [],
    runs_payload: null,
    projected: null,
    has_final_state: false,
    spans: [],
    trace_id: null,
    ...p,
  };
}

function replay(
  messages: ReplayMessage[],
  extra: Omit<Partial<AdminConversationReplay>, "conversation"> & {
    conversation?: Partial<AdminConversationReplay["conversation"]>;
  } = {},
): AdminConversationReplay {
  const { conversation, ...rest } = extra;
  return {
    conversation: {
      created_at: "2026-08-01T00:00:00Z",
      display_name: "Alice",
      id: "c1",
      title: "一次多 Agent 会话",
      user_id: "u1",
      username: "alice",
      ...conversation,
    },
    cost_total: 0,
    errors: 0,
    has_more_before: false,
    messages,
    turns: 1,
    ...rest,
  };
}

const MESSAGES: ReplayMessage[] = [
  msg({ id: "u1", role: "user", content: "帮我查一下" }),
  msg({
    id: "a1",
    role: "assistant",
    content: "CEO 汇总",
    runs: [run({ run_id: "r1", role: "研究员", task: "搜集资料" })],
  }),
];

/** Reads back what the page did to the address it was opened at. */
function LocationProbe() {
  const location = useLocation();
  return (
    <>
      <span data-testid="loc-search">{location.search}</span>
      <span data-testid="loc-from">
        {(location.state as { from?: string } | null)?.from ?? ""}
      </span>
      <span data-testid="nav-type">{useNavigationType()}</span>
    </>
  );
}

function renderReplay(
  opts: { onBack?: () => void; search?: string; state?: unknown } = {},
) {
  const onBack = opts.onBack ?? vi.fn();
  const view = render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/replay/c1",
          search: opts.search ?? "",
          state: opts.state ?? null,
        },
      ]}
    >
      <ConversationReplay conversationId="c1" onBack={onBack} backLabel="返回" />
      <LocationProbe />
    </MemoryRouter>,
  );
  return { ...view, onBack };
}

/** Opens the diagnose dock (desktop side-panel analog, closed by default). */
async function openDock() {
  fireEvent.click(await screen.findByRole("button", { name: "打开诊断" }));
  return screen.findByRole("separator");
}

describe("ConversationReplay layout", () => {
  it("时间线只挂载一份（宽窄两套布局曾各渲染一遍）", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();

    expect(await screen.findAllByText("CEO 汇总")).toHaveLength(1);
    expect(screen.getAllByText("帮我查一下")).toHaveLength(1);
    expect(screen.getAllByLabelText("对话终态")).toHaveLength(1);
  });

  it("正文不再用视口高度硬算（无 100vh 魔法数）", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    const { container } = renderReplay();
    await screen.findByText("CEO 汇总");
    await openDock();

    expect(container.innerHTML).not.toContain("100vh");
  });

  it("开坞后时间线仍在 DOM 中（靠 CSS 隐藏，不丢阅读位置）", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    await openDock();

    expect(screen.getAllByText("CEO 汇总")).toHaveLength(1);
    expect(screen.getByText("搜集资料")).toBeTruthy();
  });

  it("诊断面板宽度键盘可调并记住", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    const handle = await openDock();
    expect(handle.getAttribute("aria-valuenow")).toBe("400");

    // 左箭头把分隔条往左推 = 右侧面板变宽。
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(handle.getAttribute("aria-valuenow")).toBe("424");

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle.getAttribute("aria-valuenow")).toBe("376");

    // 越界被夹住，不会拖成负宽或吃掉整屏。
    fireEvent.keyDown(handle, { key: "Home" });
    expect(handle.getAttribute("aria-valuenow")).toBe("720");
    fireEvent.keyDown(handle, { key: "End" });
    expect(handle.getAttribute("aria-valuenow")).toBe("320");

    expect(window.localStorage.getItem("admin:replay:dock-width")).toBe("320");
  });

  it("阅读柱是桌面对话同款 max-w-3xl，开坞不改这个上限", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    await screen.findByText("CEO 汇总");
    const pane = screen.getByLabelText("对话阅读区");
    expect(pane.querySelector(".max-w-3xl")).toBeTruthy();

    await openDock();
    expect(screen.getByLabelText("对话阅读区").querySelector(".max-w-3xl")).toBeTruthy();
  });

  it("下次进入沿用上次的面板宽度", async () => {
    window.localStorage.setItem("admin:replay:dock-width", "600");
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    const handle = await openDock();

    expect(handle.getAttribute("aria-valuenow")).toBe("600");
  });

  it("加载失败给出重试，成功后照常渲染", async () => {
    vi.mocked(fetchConversationReplay)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(replay(MESSAGES));

    renderReplay();

    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByText("CEO 汇总")).toBeTruthy());
  });

  it("返回按钮走来源页回调", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    const { onBack } = renderReplay();
    fireEvent.click(await screen.findByRole("button", { name: "返回" }));

    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("主画面是桌面对话克隆：无顶栏 pills/KPI，诊断默认关", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    const { container } = renderReplay();
    await screen.findByText("一次多 Agent 会话");

    expect(container.querySelector("header")).toBeNull();
    expect(screen.queryByRole("button", { name: /#1/ })).toBeNull();
    expect(screen.queryByLabelText("运维信号")).toBeNull();
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开诊断" })).toBeTruthy();
    expect(screen.getByLabelText("只读输入").textContent).toContain("只读复盘");
    expect(screen.queryByRole("button", { name: "对话大纲" })).toBeNull();
  });

  it("会话 KPI 与 id 复制在诊断坞内", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    await openDock();

    expect(screen.getByTitle("c1（点击复制）")).toBeTruthy();
    expect(screen.getByText(/错误 0/)).toBeTruthy();
    expect(screen.getByText(/成本/)).toBeTruthy();
    expect(screen.getByText(/多 Agent 1 回合/)).toBeTruthy();
    expect(screen.getByLabelText("运维信号")).toBeTruthy();
  });

  it("至少两条用户消息才出现对话大纲", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay();
    await screen.findByText("第一回合结论");
    expect(screen.getByRole("button", { name: "对话大纲" })).toBeTruthy();
  });

  it("打开诊断且未选回合时锚到最后一条助手消息", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay();
    await screen.findByText("第一回合结论");
    expect(screen.getByTestId("loc-search").textContent).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "打开诊断" }));
    expect(screen.getByTestId("loc-search").textContent).toBe("?turn=a2");
    expect(screen.getByRole("separator")).toBeTruthy();
  });

  it("切回合时诊断坞保持打开", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay();
    await openDock();
    expect(screen.getByRole("separator")).toBeTruthy();

    fireEvent.click(screen.getByText("第一回合结论"));
    expect(screen.getByRole("separator")).toBeTruthy();
    expect(screen.getByTestId("loc-search").textContent).toBe("?turn=a1");
  });

  it("moves execution_harvest to the ops bar, not the user column", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({
          id: "h1",
          role: "user",
          origin: "execution_harvest",
          harvest_kind: "cancelled",
          content:
            "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
        }),
        ...MESSAGES,
      ]),
    );

    renderReplay();
    await screen.findByText("CEO 汇总");
    expect(screen.queryByLabelText("运维信号")).toBeNull();

    await openDock();
    expect(screen.getByLabelText("运维信号").textContent).toContain("系统收口");
    expect(screen.getByText("已取消")).toBeTruthy();
    expect(
      screen.queryByText(
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
      ),
    ).toBeNull();

    fireEvent.click(screen.getByText("已取消"));
    expect(
      await screen.findByText(
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
      ),
    ).toBeTruthy();
  });

  it("falls back to 系统收口 in the ops bar when only the prefix is present", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({
          id: "h1",
          role: "user",
          content: "【系统收口】后台团队任务已全部完成。请综合队员产出。",
        }),
        msg({ id: "a1", role: "assistant", content: "综合产出" }),
      ]),
    );

    renderReplay();
    await screen.findByText("综合产出");
    expect(
      screen.getByLabelText("对话阅读区").textContent,
    ).not.toContain("【系统收口】");

    await openDock();

    expect(screen.getByLabelText("运维信号").textContent).toContain("系统收口");
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    // Opening diagnose anchors the following assistant, so the harvest body
    // is in the dock as the preceding trigger — still not in the reading column.
    expect(screen.getByText("【系统收口】后台团队任务已全部完成。请综合队员产出。")).toBeTruthy();
    expect(
      screen.getByLabelText("对话阅读区").textContent,
    ).not.toContain("【系统收口】");
  });

  it("keeps span ops in the diagnose dock after selecting a turn", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({ id: "u1", role: "user", content: "帮我查一下" }),
        msg({
          id: "a1",
          role: "assistant",
          content: "查完了",
          spans: [
            {
              args_preview: "q=foo",
              finish_reason: null,
              input_tokens: 10,
              kind: "llm",
              name: null,
              output_tokens: 20,
              result_preview: null,
              round_idx: 0,
              run_id: null,
              success: true,
            },
            {
              args_preview: "q=foo",
              finish_reason: null,
              input_tokens: null,
              kind: "tool",
              name: "web_search",
              output_tokens: null,
              result_preview: "3 hits",
              round_idx: null,
              run_id: null,
              success: true,
            },
          ],
        }),
      ]),
    );

    renderReplay();
    fireEvent.click(await screen.findByText("查完了"));
    expect(screen.queryByText("1 次模型调用 · 1 次工具")).toBeNull();
    expect(screen.queryByText("web_search")).toBeNull();

    await openDock();
    expect(screen.getByText("1 次模型调用 · 1 次工具")).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
  });

  it("tells ops when earlier messages were truncated", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(MESSAGES, { has_more_before: true }),
    );

    renderReplay();
    await screen.findByText("CEO 汇总");
    expect(screen.getByRole("status").textContent).toContain(
      "更早的消息已被截断",
    );
  });

  it("shows 会话已删 beside the title when the conversation is a tombstone", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(MESSAGES, {
        conversation: { deleted_at: "2026-08-10T00:00:00Z" },
      }),
    );

    renderReplay();
    expect(await screen.findByText("会话已删")).toBeTruthy();
    expect(screen.getByText("一次多 Agent 会话")).toBeTruthy();
  });

  it("does not show 会话已删 on a live conversation", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(replay(MESSAGES));

    renderReplay();
    await screen.findByText("一次多 Agent 会话");
    expect(screen.queryByText("会话已删")).toBeNull();
  });
});

/** Two assistant turns, so "landed on the anchored one" is a distinguishable claim. */
const ANCHOR_MESSAGES: ReplayMessage[] = [
  msg({ id: "u1", role: "user", content: "帮我查一下" }),
  msg({ id: "a1", role: "assistant", content: "第一回合结论", trace_id: "t-1" }),
  msg({ id: "u2", role: "user", content: "再看看这个" }),
  msg({ id: "a2", role: "assistant", content: "第二回合结论", trace_id: "t-2" }),
];

/** Selection is a ring on screen and `aria-current` in the DOM — the bubble only. */
function selectedTurns() {
  return screen.queryAllByRole("button", { current: true });
}

describe("ConversationReplay 回合锚点", () => {
  it("带回合锚点的链接直接打开就落在那个回合上", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay({ search: "?turn=a2" });
    await screen.findByText("第二回合结论");

    // 时间线里被锚定的那条气泡（大纲按钮不加 aria-current）。
    const current = selectedTurns();
    expect(current).toHaveLength(1);
    expect(
      current.filter((el) => el.textContent?.includes("第二回合结论")),
    ).toHaveLength(1);
    expect(
      current.filter((el) => el.textContent?.includes("第一回合结论")),
    ).toHaveLength(0);
    // 时间线滚到它，而不是把人留在会话顶部自己找。滚动发生在渲染之后的 effect 里，
    // 所以文本出现不代表滚动已经跑过——同步断言会在负载下抢跑。
    await waitFor(() =>
      expect(scrolledInto.at(-1)?.textContent).toContain("第二回合结论"),
    );
  });

  it("锚点指向不存在的回合时不崩，退回未选中", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay({ search: "?turn=早就没有的回合" });

    // 会话照常渲染：既不是白屏，也不是错误态。
    expect(await screen.findByText("第一回合结论")).toBeTruthy();
    expect(screen.getByText("第二回合结论")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    // 也不拿第一个回合顶包充数——解析不出来的锚点就是未选中。
    expect(selectedTurns()).toHaveLength(0);
    expect(scrolledInto).toHaveLength(0);
  });

  it("没有锚点就不预选回合，默认值也不写进地址", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay();
    await screen.findByText("第一回合结论");

    expect(selectedTurns()).toHaveLength(0);
    expect(screen.getByTestId("loc-search").textContent).toBe("");
  });

  it("点选回合把锚点写进地址，且是 replace 不堆历史", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay();
    fireEvent.click(await screen.findByText("第二回合结论"));

    expect(screen.getByTestId("loc-search").textContent).toBe("?turn=a2");
    expect(selectedTurns()).toHaveLength(1);
    // 每点一个回合压一条历史，浏览器的返回键就再也退不回来源列表了。
    expect(screen.getByTestId("nav-type").textContent).toBe("REPLACE");
  });

  it("写锚点不会把来源页丢掉（返回仍回得去）", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    renderReplay({ state: { from: "/conversations/turns?q=boom" } });
    fireEvent.click(await screen.findByText("第二回合结论"));

    expect(screen.getByTestId("loc-search").textContent).toBe("?turn=a2");
    // setSearchParams 本身是一次导航：不显式把 state 带上，ReplayPage 的来源页就没了。
    expect(screen.getByTestId("loc-from").textContent).toBe(
      "/conversations/turns?q=boom",
    );
  });

  it("trace 链接照旧落在对应回合，改选回合也不冲掉 trace", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES),
    );

    // 对话页的 trace 跳转交过来的是 trace_id，不是消息 id。
    renderReplay({ search: "?trace=t-2" });
    await screen.findByText("第二回合结论");
    expect(
      selectedTurns().filter((el) =>
        el.textContent?.includes("第二回合结论"),
      ),
    ).toHaveLength(1);

    fireEvent.click(screen.getByText("第一回合结论"));
    expect(screen.getByTestId("loc-search").textContent).toBe(
      "?trace=t-2&turn=a1",
    );
  });
});

describe("ConversationReplay 按需终态", () => {
  it("auto-fetches final state for journaled assistant rows in the loaded window", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({ id: "u1", role: "user", content: "帮我查一下" }),
        msg({
          id: "a1",
          role: "assistant",
          content: "CEO 汇总",
          has_final_state: true,
        }),
      ]),
    );
    vi.mocked(fetchReplayTurnFinalState).mockResolvedValue({
      message_id: "a1",
      runs_payload: {
        events_complete: true,
        finish_reason: "end_turn",
        process: [{ kind: "tool", tool_name: "web_search", status: "success" }],
      },
      projected: null,
    });

    renderReplay();
    await screen.findByText("CEO 汇总");
    await waitFor(() =>
      expect(fetchReplayTurnFinalState).toHaveBeenCalledWith("c1", "a1"),
    );
    expect(await screen.findByText("使用 1 个工具")).toBeTruthy();
    expect(screen.queryByText("web_search")).toBeNull();
    expect(screen.queryByText("finish end_turn")).toBeNull();
    fireEvent.click(screen.getByText("使用 1 个工具"));
    expect(screen.getByText("web_search")).toBeTruthy();
  });

  it("hydrates the URL-anchored turn on first paint", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay(ANCHOR_MESSAGES.map((m) =>
        m.id === "a2" ? { ...m, has_final_state: true } : m,
      )),
    );
    vi.mocked(fetchReplayTurnFinalState).mockResolvedValue({
      message_id: "a2",
      runs_payload: {
        events_complete: true,
        finish_reason: "end_turn",
        process: [{ kind: "tool", tool_name: "read_file", status: "success" }],
      },
      projected: null,
    });

    renderReplay({ search: "?turn=a2" });
    await waitFor(() =>
      expect(fetchReplayTurnFinalState).toHaveBeenCalledWith("c1", "a2"),
    );
    expect(await screen.findByText("使用 1 个工具")).toBeTruthy();
    fireEvent.click(screen.getByText("使用 1 个工具"));
    expect(screen.getByText("read_file")).toBeTruthy();
  });

  it("retries a failed final-state fetch without reloading the thread", async () => {
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({
          id: "a1",
          role: "assistant",
          content: "CEO 汇总",
          has_final_state: true,
        }),
      ]),
    );
    vi.mocked(fetchReplayTurnFinalState)
      .mockRejectedValueOnce(new Error("终态失败"))
      .mockResolvedValueOnce({
        message_id: "a1",
        runs_payload: {
          events_complete: true,
          finish_reason: "end_turn",
          process: [{ kind: "tool", tool_name: "web_search", status: "success" }],
        },
        projected: null,
      });

    renderReplay({ search: "?turn=a1" });
    expect(
      await screen.findByRole("button", { name: "重试加载终态" }),
    ).toBeTruthy();
    expect(fetchConversationReplay).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "重试加载终态" }));
    expect(await screen.findByText("使用 1 个工具")).toBeTruthy();
    fireEvent.click(screen.getByText("使用 1 个工具"));
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(fetchConversationReplay).toHaveBeenCalledTimes(1);
  });

  it("does not leave 正在加载终态 stuck when Strict Mode remounts the hydrate effect", async () => {
    let resolveFetch!: (value: {
      message_id: string;
      runs_payload: {
        events_complete: boolean;
        finish_reason: string;
        process: { kind: string; tool_name: string; status: string }[];
      };
      projected: null;
    }) => void;
    vi.mocked(fetchConversationReplay).mockResolvedValue(
      replay([
        msg({
          id: "a1",
          role: "assistant",
          content: "CEO 汇总",
          has_final_state: true,
        }),
      ]),
    );
    vi.mocked(fetchReplayTurnFinalState).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        }),
    );

    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/replay/c1"]}>
          <ConversationReplay
            conversationId="c1"
            onBack={vi.fn()}
            backLabel="返回"
          />
        </MemoryRouter>
      </StrictMode>,
    );

    await screen.findByText("CEO 汇总");
    expect((await screen.findByRole("status")).textContent).toContain(
      "正在加载终态",
    );

    resolveFetch({
      message_id: "a1",
      runs_payload: {
        events_complete: true,
        finish_reason: "end_turn",
        process: [{ kind: "tool", tool_name: "web_search", status: "success" }],
      },
      projected: null,
    });

    await waitFor(() => {
      expect(screen.queryByText("正在加载终态")).toBeNull();
    });
    expect(screen.getByText("使用 1 个工具")).toBeTruthy();
  });
});
