// @vitest-environment jsdom
/**
 * Render + interaction tests for the mobile 离线恢复 card (结构化挂起 2b / 挂起即收口 ②).
 *
 * ResumeCard is the SINGLE durable surface for a turn that paused at a checkpoint and then
 * lost its live stream — surfaced on reopen, and (under ②, post flag-on) the moment a live
 * stream ENDS at a checkpoint (message_end finish_reason=paused → ChatPage.refreshPaused).
 * Unlike PauseCard it reads a PERSISTED PausedTurnSummary and asks the parent to drive a
 * fresh resume stream. These assert the two kind branches (ask_user / plan_review), that the
 * note rides along, and plan_review / team_preview 调整 gating — coverage the durable path lacked.
 * Dense kinds (team_preview / walls) use Latch + Interaction Sheet; Modal is stubbed (jsdom
 * lacks showModal). The block comment keeps the @vitest-environment directive file-leading
 * past organizeImports.
 */

import type { PausedTurnSummary } from "@/api/turn";
import { ResumeCard } from "@/components/ResumeCard";
import {
  clearColdInteractions,
  markColdResolved,
  upsertColdRequired,
} from "@/lib/coldInteractions";
import { resetKickoffAdjustDraftsForTests } from "@/lib/kickoffAdjustDraft";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/Modal", () => ({
  Modal: ({
    children,
    className,
  }: {
    children: ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
}));

afterEach(() => {
  cleanup();
  resetKickoffAdjustDraftsForTests();
  clearColdInteractions();
});

function summary(over: Partial<PausedTurnSummary> = {}): PausedTurnSummary {
  return {
    message_id: "m-server-1",
    checkpoint_id: "cp1",
    kind: "ask_user",
    user_message: "做 A 还是 B？",
    user_message_id: "u1",
    question: "先做 A 还是 B?\n两条路线各有取舍。",
    // 契约序列化必带（服务端带默认值恒输出；仅 team_preview 开工卡才有具体值）
    form: "",
    headline: "",
    motion: "",
    primitive: "delegate",
    max_rounds: 0,
    thorough: true,
    browser_login: false,
    ...over,
  } as PausedTurnSummary;
}

describe("ResumeCard · ask_user", () => {
  it("renders the offline headline, the original request, and question", () => {
    render(<ResumeCard paused={summary()} onResume={vi.fn()} />);
    expect(screen.getByText("需要你拍板（已离线保留）")).toBeTruthy();
    expect(screen.getByText("做 A 还是 B？")).toBeTruthy();
    expect(screen.getByText("先做 A 还是 B? 两条路线各有取舍。")).toBeTruthy();
    // ask_user has no 调整 (that is plan_review / team_preview steer).
    expect(screen.queryByText("调整")).toBeNull();
  });

  it("提交 submits continue with the trimmed note", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={summary()} onResume={onResume} />);
    fireEvent.change(screen.getByPlaceholderText(/可选/), {
      target: { value: "  选 A  " },
    });
    fireEvent.click(screen.getByText("提交"));
    expect(onResume).toHaveBeenCalledWith("continue", "选 A", []);
  });

  it("取消 submits stop（硬停，非 empty continue）", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={summary()} onResume={onResume} />);
    expect(screen.queryByText("跳过")).toBeNull();
    fireEvent.click(screen.getByText("取消"));
    expect(onResume).toHaveBeenCalledWith("stop", "", []);
    expect(onResume).not.toHaveBeenCalledWith(
      "continue",
      expect.anything(),
      expect.anything(),
    );
  });

  it("本机目录 action 可点 → LocalPickerFailureCard unavailable（不灰掉、不提交）", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "decision",
          questions: [
            {
              id: "q0",
              prompt: "工作区",
              kind: "choice",
              multiple: false,
              options: [
                { label: "打开本地项目", action: "open_local_project" },
                { label: "继续用云端" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    const folderBtn = screen.getByRole("button", { name: /打开本地项目/ });
    expect((folderBtn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(folderBtn);
    const card = screen.getByTestId("local-picker-failure-card");
    expect(card.getAttribute("data-failure-kind")).toBe("unavailable");
    expect(card.textContent).toContain("本机目录仅桌面端可用");
    expect(onResume).not.toHaveBeenCalled();
  });

  it("proposal_pick 行选映射进 selected；CTA 采用此方案", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "proposal_pick",
          questions: [
            {
              id: "q0",
              prompt: "选方案",
              kind: "choice",
              multiple: false,
              options: [
                { label: "方案 A", detail: "走云端更稳" },
                { label: "方案 B", detail: "走本机更快" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(
      document.querySelector('[data-ask-intent="proposal_pick"]'),
    ).toBeTruthy();
    expect(screen.getAllByText("方案挑选 · 选一条推进").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("走云端更稳")).toBeTruthy();
    fireEvent.click(screen.getByText("方案 A"));
    fireEvent.click(screen.getByText("采用此方案"));
    expect(onResume).toHaveBeenCalledWith("continue", "", ["方案 A"]);
  });

  it("proposal_pick 空选不可提交", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "proposal_pick",
          questions: [
            {
              id: "q0",
              prompt: "选方案",
              kind: "choice",
              multiple: false,
              options: [{ label: "方案 A" }, { label: "方案 B" }],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    const cta = screen.getByRole("button", { name: "采用此方案" });
    expect((cta as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(cta);
    expect(onResume).not.toHaveBeenCalled();
  });

  it("organize_plan 默认可剔除；非全保留；CTA 确认并整理（n）", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "organize_plan",
          questions: [
            {
              id: "q0",
              prompt: "保留哪些操作",
              kind: "choice",
              multiple: true,
              options: [
                { label: "a → b", op: "move", source: "a", destination: "b" },
                { label: "删 x", op: "delete", path: "x" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(
      document.querySelector('[data-ask-intent="organize_plan"]'),
    ).toBeTruthy();
    expect(
      screen.getAllByText("整理方案 · 确认要执行的项").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("取消勾选即剔除")).toBeTruthy();
    expect(screen.getByText("a → b")).toBeTruthy();
    expect(screen.getByText("delete x")).toBeTruthy();
    // Uncheck second item — not keep-all.
    fireEvent.click(screen.getByText("删 x"));
    fireEvent.click(screen.getByRole("button", { name: /确认并整理/ }));
    expect(onResume).toHaveBeenCalledWith("continue", "", ["a → b"]);
    expect(onResume.mock.calls[0][2]).not.toEqual(["a → b", "删 x"]);
  });

  it("organize_plan 空选禁 CTA", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "organize_plan",
          questions: [
            {
              id: "q0",
              prompt: "保留哪些操作",
              kind: "choice",
              multiple: true,
              options: [
                { label: "a → b", op: "move", source: "a", destination: "b" },
                { label: "删 x", op: "delete", path: "x" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByText("a → b"));
    fireEvent.click(screen.getByText("删 x"));
    const cta = screen.getByRole("button", { name: /^确认并整理$/ });
    expect((cta as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(cta);
    expect(onResume).not.toHaveBeenCalled();
  });

  it("risk_ack 行式多选；严重度灰字；空选可继续", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "risk_ack",
          questions: [
            {
              id: "q0",
              prompt: "本轮处理哪些风险",
              kind: "choice",
              multiple: true,
              options: [
                { label: "[高] 密钥轮换", detail: "优先" },
                { label: "[低] 文档补齐" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(document.querySelector('[data-ask-intent="risk_ack"]')).toBeTruthy();
    expect(screen.getByText("密钥轮换")).toBeTruthy();
    expect(screen.getByText("优先")).toBeTruthy();
    expect(screen.getByText("高")).toBeTruthy();
    expect(screen.getByText("低")).toBeTruthy();
    // Empty selection allowed.
    fireEvent.click(screen.getByText("确认并继续"));
    expect(onResume).toHaveBeenCalledWith("continue", "", []);
  });

  it("decision / kickoff / 裸 ask 选项第二句不画，只显示选项名", () => {
    for (const intent of [undefined, "decision", "kickoff"] as const) {
      cleanup();
      render(
        <ResumeCard
          paused={summary({
            ...(intent ? { intent } : {}),
            questions: [
              {
                id: "q0",
                prompt: "先做哪条",
                kind: "choice",
                multiple: false,
                options: [
                  { label: "方案 A", detail: "这条更稳但慢" },
                  { label: "方案 B", detail: "快但不稳" },
                ],
              },
            ],
          })}
          onResume={vi.fn()}
        />,
      );
      expect(screen.getByText("方案 A")).toBeTruthy();
      expect(screen.getByText("方案 B")).toBeTruthy();
      expect(screen.queryByText("这条更稳但慢")).toBeNull();
      expect(screen.queryByText("快但不稳")).toBeNull();
      expect(document.querySelector(".ask-check-detail")).toBeNull();
    }
  });

  it("decision 允许整理选项用结构化字段画将整理，不用模型副标题", () => {
    render(
      <ResumeCard
        paused={summary({
          intent: "decision",
          questions: [
            {
              id: "q0",
              prompt: "整理哪份",
              kind: "choice",
              multiple: false,
              options: [
                {
                  label: "允许整理咨询文件夹",
                  action: "grant_organize_folder",
                  well_known: "desktop",
                  target_name: "咨询",
                  detail: "模型发挥的副标题不要画",
                },
              ],
            },
          ],
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByText("允许整理咨询文件夹")).toBeTruthy();
    expect(screen.getByText("将整理：桌面 › 咨询")).toBeTruthy();
    expect(screen.queryByText("模型发挥的副标题不要画")).toBeNull();
  });

  it("decision default 预选 + compose 答复 +「其他」逃逸", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "decision",
          questions: [
            {
              id: "q0",
              prompt: "先做哪条",
              kind: "choice",
              multiple: false,
              default: "方案 A",
              options: [{ label: "方案 A" }, { label: "方案 B" }],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(document.querySelector('[data-ask-intent="decision"]')).toBeTruthy();
    expect(screen.getByText("其他…")).toBeTruthy();
    // Default preselected — one-click submit composes.
    fireEvent.click(screen.getByText("提交"));
    expect(onResume).toHaveBeenCalledWith(
      "continue",
      "我的答复：\n· 先做哪条：方案 A",
      [],
    );
  });

  it("daily_review 默认全选，取消勾选后提交带 selected", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "daily_review",
          questions: [
            {
              id: "q0",
              prompt: "落盘哪些提案",
              kind: "choice",
              multiple: true,
              options: [
                {
                  label: "偏好简洁",
                  review_kind: "preference",
                  body: "短句",
                },
                { label: "规则先问", review_kind: "rule", body: "先确认" },
                {
                  label: "主题：周报节奏",
                  review_kind: "topic",
                  body: "周五",
                },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(
      screen.getAllByText("复盘提案 · 确认要落盘的项").length,
    ).toBeGreaterThan(0);
    expect(
      document.querySelector('[data-ask-intent="daily_review"]'),
    ).toBeTruthy();
    expect(screen.getByText("偏好 · 短句")).toBeTruthy();
    expect(screen.getByText("规则 · 先确认")).toBeTruthy();
    expect(
      screen.getByText(/确认后服务端直接写入记忆\/规则\/文档/),
    ).toBeTruthy();
    expect(screen.getByText("取消勾选即跳过")).toBeTruthy();

    // Seed all three; uncheck one.
    fireEvent.click(screen.getByText("主题：周报节奏"));
    fireEvent.click(screen.getByRole("button", { name: /确认落盘/ }));
    expect(onResume).toHaveBeenCalledWith("continue", "", [
      "偏好简洁",
      "规则先问",
    ]);
  });

  it("daily_review 全取消后确认落盘禁用", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          intent: "daily_review",
          questions: [
            {
              id: "q0",
              prompt: "落盘哪些提案",
              kind: "choice",
              multiple: true,
              options: [
                { label: "偏好简洁", review_kind: "preference", body: "短句" },
                { label: "规则先问", review_kind: "rule", body: "先确认" },
              ],
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByText("偏好简洁"));
    fireEvent.click(screen.getByText("规则先问"));
    const cta = screen.getByRole("button", { name: /^确认落盘$/ });
    expect((cta as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(cta);
    expect(onResume).not.toHaveBeenCalled();
  });
});

describe("ResumeCard · plan_review", () => {
  const planReview = (
    over: Partial<PausedTurnSummary> = {},
  ): PausedTurnSummary =>
    summary({
      kind: "plan_review",
      checkpoint_id: "pr1",
      question: "",
      steps: [{ role: "调研", output_summary: "方案就绪" }],
      pending: [{ role: "执行" }],
      ...over,
    });

  it("renders the plan_review headline and the completed step", () => {
    render(<ResumeCard paused={planReview()} onResume={vi.fn()} />);
    expect(
      screen.getAllByText("执行已暂停 · 待你决定是否继续").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("调研")).toBeTruthy();
    expect(screen.getByText("方案就绪")).toBeTruthy();
  });

  it("调整 is gated until a note is typed, then steers with it", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={planReview()} onResume={onResume} />);
    const adjust = screen.getByText("调整") as HTMLButtonElement;
    expect(adjust.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/可选/), {
      target: { value: "换个方向" },
    });
    expect(adjust.disabled).toBe(false);
    fireEvent.click(adjust);
    expect(onResume).toHaveBeenCalledWith("adjust", "换个方向", []);
  });
});

describe("ResumeCard · team_preview", () => {
  const teamPreview = (
    over: Partial<PausedTurnSummary> = {},
  ): PausedTurnSummary =>
    summary({
      kind: "team_preview",
      checkpoint_id: "tp1",
      question: "",
      workers: [
        {
          run_id: "r1",
          role: "调研",
          task: "做A",
          depends_on: [],
          write_capability: "can_write_files",
          write_capability_label: "可改文件",
        },
      ],
      tools: ["file_write"],
      primitive: "delegate",
      ...over,
    });

  it("三键：授权并开工 + 调整 + 取消；无逐次审批 / 排除岗", () => {
    render(<ResumeCard paused={teamPreview()} onResume={vi.fn()} />);
    expect(screen.getByText("授权并开工")).toBeTruthy();
    expect(screen.getByText("调整")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.queryByText("逐次审批开工")).toBeNull();
    expect(screen.queryByText("纳入本轮")).toBeNull();
    expect(screen.getByText("本批工具：file_write")).toBeTruthy();
  });

  it("Latch + Sheet：默认打开可点 CTA；收起留 latch，再打开不丢控件", () => {
    render(<ResumeCard paused={teamPreview()} onResume={vi.fn()} />);
    // Sheet open → latch hidden so chat column is not double-taxed.
    expect(screen.queryByTestId("resume-card-latch")).toBeNull();
    expect(screen.getByText("授权并开工")).toBeTruthy();
    fireEvent.click(screen.getByTestId("interaction-sheet-collapse"));
    expect(screen.queryByText("授权并开工")).toBeNull();
    const latch = screen.getByTestId("resume-card-latch");
    expect(latch).toBeTruthy();
    expect(screen.getByText("1 人待确认 · 点开授权开工")).toBeTruthy();
    fireEvent.click(latch);
    expect(screen.getByText("授权并开工")).toBeTruthy();
    expect(screen.queryByText("纳入本轮")).toBeNull();
    expect(screen.getByText("改为仅文字")).toBeTruthy();
  });

  it("主按钮带嘱咐发 continue（未改正时无修正载荷）", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={teamPreview()} onResume={onResume} />);
    fireEvent.change(screen.getByPlaceholderText(/对全体队员的嘱咐/), {
      target: { value: "更简洁" },
    });
    fireEvent.click(screen.getByText("授权并开工"));
    expect(onResume).toHaveBeenCalledWith("continue", "更简洁", []);
    expect(onResume.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("调整进入调整态：空意见不可提交；有意见发 adjust，不带修正字段", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={teamPreview()} onResume={onResume} />);
    fireEvent.click(screen.getByText("调整"));
    expect(onResume).not.toHaveBeenCalled();
    const submitAdjust = screen.getByRole("button", {
      name: "交回修订",
    }) as HTMLButtonElement;
    expect(submitAdjust.disabled).toBe(true);
    fireEvent.click(submitAdjust);
    expect(onResume).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "  改成两人，先做竞品  " },
    });
    expect(submitAdjust.disabled).toBe(false);
    fireEvent.click(submitAdjust);
    expect(onResume).toHaveBeenCalledWith("adjust", "改成两人，先做竞品", []);
    expect(onResume.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("调整态不渲染开工按钮", () => {
    render(<ResumeCard paused={teamPreview()} onResume={vi.fn()} />);
    expect(screen.getByText("授权并开工")).toBeTruthy();
    fireEvent.click(screen.getByText("调整"));
    expect(screen.queryByText("授权并开工")).toBeNull();
    expect(screen.getByRole("button", { name: "交回修订" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "返回" })).toBeTruthy();
    expect(screen.queryByText("取消")).toBeNull();
  });

  it("返回丢弃调整输入并回到确认态", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={teamPreview()} onResume={onResume} />);
    fireEvent.change(screen.getByPlaceholderText(/对全体队员的嘱咐/), {
      target: { value: "确认态嘱咐" },
    });
    fireEvent.click(screen.getByText("调整"));
    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "要丢掉的调整意见" },
    });
    fireEvent.click(screen.getByText("返回"));
    expect(screen.getByText("授权并开工")).toBeTruthy();
    expect(screen.queryByTestId("team-preview-adjust-note")).toBeNull();
    expect(
      (screen.getByPlaceholderText(/对全体队员的嘱咐/) as HTMLTextAreaElement)
        .value,
    ).toBe("确认态嘱咐");
    fireEvent.click(screen.getByText("调整"));
    expect(
      (screen.getByTestId("team-preview-adjust-note") as HTMLTextAreaElement)
        .value,
    ).toBe("");
    expect(onResume).not.toHaveBeenCalled();
  });

  it("调整意见草稿扛住重挂载", () => {
    const { unmount } = render(
      <ResumeCard paused={teamPreview()} onResume={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("调整"));
    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "改分工" },
    });
    unmount();
    render(<ResumeCard paused={teamPreview()} onResume={vi.fn()} />);
    expect(
      (screen.getByTestId("team-preview-adjust-note") as HTMLTextAreaElement)
        .value,
    ).toBe("改分工");
    expect(screen.queryByText("授权并开工")).toBeNull();
  });

  it("交回修订后保留调整表单，CTA 进提交中，不整卡换成等待面", () => {
    const onResume = vi.fn();
    const { rerender } = render(
      <ResumeCard paused={teamPreview()} onResume={onResume} />,
    );
    fireEvent.click(screen.getByText("调整"));
    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "改成两人" },
    });
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(onResume).toHaveBeenCalledWith("adjust", "改成两人", []);
    rerender(
      <ResumeCard
        paused={{ ...teamPreview(), interactionStatus: "submitting" }}
        onResume={onResume}
      />,
    );
    expect(screen.getByTestId("team-preview-adjust-note")).toBeTruthy();
    expect(
      (screen.getByTestId("team-preview-adjust-note") as HTMLTextAreaElement)
        .value,
    ).toBe("改成两人");
    const submitting = screen.getByRole("button", {
      name: "提交中…",
    }) as HTMLButtonElement;
    expect(submitting.disabled).toBe(true);
    expect(screen.queryByTestId("kickoff-adjust-waiting")).toBeNull();
    expect(screen.queryByText("CEO 正在按你的意见重排团队")).toBeNull();
    expect(screen.queryByText("授权并开工")).toBeNull();
  });

  it("收紧写盘后点调整仍不带 write_capability_overrides", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={teamPreview()} onResume={onResume} />);
    fireEvent.click(screen.getByText("改为仅文字"));
    fireEvent.click(screen.getByText("调整"));
    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "先改分工" },
    });
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(onResume).toHaveBeenCalledWith("adjust", "先改分工", []);
    expect(onResume.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("确认面无排除岗入口；continue 不附 excluded_run_ids", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "调研",
              task: "做A",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
            {
              run_id: "r2",
              role: "写作",
              task: "做B",
              depends_on: [],
              write_capability: "can_write_files",
              write_capability_label: "可改文件",
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(screen.queryByText("纳入本轮")).toBeNull();
    expect(screen.queryByLabelText(/纳入本轮/)).toBeNull();
    fireEvent.click(screen.getByText("授权并开工"));
    expect(onResume).toHaveBeenCalledWith("continue", "", []);
    const amendments = onResume.mock.calls[0]?.[3] as
      | Record<string, unknown>
      | undefined;
    expect(amendments).toBeUndefined();
  });

  it("可改文件 → 仅文字：continue 带 write_capability_overrides", () => {
    const onResume = vi.fn();
    render(<ResumeCard paused={teamPreview()} onResume={onResume} />);
    expect(screen.getByText("可改文件")).toBeTruthy();
    fireEvent.click(screen.getByText("改为仅文字"));
    fireEvent.click(screen.getByText("授权并开工"));
    expect(onResume).toHaveBeenCalledWith("continue", "", [], {
      write_capability_overrides: [{ run_id: "r1", capability: "text_only" }],
    });
    const amendments = onResume.mock.calls[0]?.[3] as
      | Record<string, unknown>
      | undefined;
    expect(amendments).not.toHaveProperty("excluded_run_ids");
  });

  it("delegate 开工卡无模型下拉；continue 不附 model_overrides", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "调研",
              task: "做A",
              depends_on: [],
              model: "ceo-flash",
              origin: "platform",
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
            {
              run_id: "r2",
              role: "写作",
              task: "做B",
              depends_on: [],
              model: "ceo-flash",
              origin: "platform",
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(screen.queryByTestId(/team-worker-model-/)).toBeNull();
    fireEvent.click(screen.getByText("授权并开工"));
    expect(onResume).toHaveBeenCalledWith("continue", "", []);
    const amendments = onResume.mock.calls[0]?.[3] as
      | Record<string, unknown>
      | undefined;
    expect(amendments).toBeUndefined();
  });

  it("全员同桌也不画工作区", () => {
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "调研",
              task: "做A",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
              target_folder_name: "本会话工作区",
            },
            {
              run_id: "r2",
              role: "写作",
              task: "做B",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
              target_folder_name: "本会话工作区",
            },
          ],
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByText(/工作区 ·/)).toBeNull();
    expect(screen.queryByTestId("team-workspace-summary")).toBeNull();
    expect(screen.queryByTestId("team-worker-desk-r1")).toBeNull();
  });

  it("队员工作区不一致也不画工作区", () => {
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "甲",
              task: "读甲",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
              target_folder_id: "f1",
              target_folder_name: "云端甲",
            },
            {
              run_id: "r2",
              role: "乙",
              task: "读乙",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
              target_folder_id: "f2",
              target_folder_name: "云端乙",
            },
          ],
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByText(/工作区 ·/)).toBeNull();
    expect(screen.queryByTestId("team-workspace-summary")).toBeNull();
    expect(screen.queryByTestId("team-worker-desk-r1")).toBeNull();
    expect(screen.queryByTestId("team-worker-desk-r2")).toBeNull();
  });

  it("旧帧无工作区字段时不画工作区", () => {
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "调研",
              task: "做A",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
            {
              run_id: "r2",
              role: "写作",
              task: "做B",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
          ],
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByText(/工作区 ·/)).toBeNull();
    expect(screen.queryByTestId("team-workspace-summary")).toBeNull();
  });

  it("已是仅文字无升权入口；stop 不带修正", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          workers: [
            {
              run_id: "r1",
              role: "调研",
              task: "做A",
              depends_on: [],
              write_capability: "text_only",
              write_capability_label: "仅文字报告",
            },
            {
              run_id: "r2",
              role: "写作",
              task: "做B",
              depends_on: [],
              write_capability: "can_write_files",
              write_capability_label: "可改文件",
            },
          ],
        })}
        onResume={onResume}
      />,
    );
    expect(screen.queryByText("改为仅文字")).toBeTruthy(); // r2 only
    // tighten then stop → amendments ignored (not passed)
    fireEvent.click(screen.getByText("改为仅文字"));
    fireEvent.click(screen.getByText("取消"));
    expect(onResume).toHaveBeenCalledWith("stop", "", []);
  });

  it("debate 开赛 + 调整 + 取消；嘱咐走 continue；无纳入控件", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          primitive: "debate",
          workers: [],
          motion: "辩题",
          sides: [{ name: "正方", stance: "赞成" }],
        })}
        onResume={onResume}
      />,
    );
    expect(screen.getByText("开赛")).toBeTruthy();
    expect(screen.getByText("调整")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.queryByText("纳入本轮")).toBeNull();
    expect(screen.queryByText("先多视角调研再辩")).toBeNull();
    expect(screen.queryByTestId(/team-worker-model-/)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText(/开赛嘱咐/), {
      target: { value: "最关心成本谁买单" },
    });
    fireEvent.click(screen.getByText("开赛"));
    expect(onResume).toHaveBeenCalledWith("continue", "最关心成本谁买单", []);
  });

  it("辩论调整态：开赛按钮不渲染；提交 adjust 不带修正字段", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          primitive: "debate",
          workers: [],
          motion: "辩题",
          sides: [{ name: "正方", stance: "赞成" }],
        })}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByText("调整"));
    expect(screen.queryByText("开赛")).toBeNull();
    fireEvent.change(screen.getByTestId("team-preview-adjust-note"), {
      target: { value: "旧路径改辩题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "交回修订" }));
    expect(onResume).toHaveBeenCalledWith("adjust", "旧路径改辩题", []);
    expect(onResume.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("辩论有 run_id：裁判节点显式；无模型下拉；continue 不附 model_overrides", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          primitive: "debate",
          workers: [],
          motion: "辩题",
          sides: [
            {
              key: "pro",
              name: "正方",
              stance: "赞成",
              run_id: "side-pro",
              model: "ceo-flash",
              origin: "platform",
            },
            {
              key: "con",
              name: "反方",
              stance: "反对",
              run_id: "side-con",
              model: "ceo-flash",
              origin: "platform",
            },
          ],
          ...({
            moderator_run_id: "mod-1",
            moderator_model: "ceo-flash",
            moderator_origin: "platform",
          } as Partial<PausedTurnSummary>),
        })}
        onResume={onResume}
      />,
    );
    expect(screen.getByTestId("debate-side-side-pro")).toBeTruthy();
    expect(screen.getByTestId("debate-side-side-con")).toBeTruthy();
    expect(screen.getByTestId("debate-moderator-mod-1")).toBeTruthy();
    expect(screen.queryByTestId(/team-worker-model-/)).toBeNull();
    expect(screen.queryByText("纳入本轮")).toBeNull();
    fireEvent.click(screen.getByText("开赛"));
    expect(onResume).toHaveBeenCalledWith("continue", "", []);
    expect(onResume.mock.calls[0]?.[3]).toBeUndefined();
  });

  it("首版无版本标记", () => {
    render(
      <ResumeCard
        paused={teamPreview({ revision: 1, revision_note: "" })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("kickoff-revision")).toBeNull();
    expect(screen.queryByText("第 1 版")).toBeNull();
    expect(screen.queryByText("第 2 版")).toBeNull();
    expect(screen.queryByText("按你的意见修订")).toBeNull();
    expect(screen.getAllByText("团队预审 · 开干前确认").length).toBeGreaterThan(
      0,
    );
  });

  it("第 2 版显示版本 + 意见", () => {
    render(
      <ResumeCard
        paused={teamPreview({
          checkpoint_id: "tp2",
          revision: 2,
          revised_from: "tp1",
          revision_note: "人太多，改成一个人做",
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByTestId("kickoff-revision")).toBeTruthy();
    expect(screen.getByText("第 2 版 · 按你的意见修订")).toBeTruthy();
    expect(screen.getByText("你交回的意见")).toBeTruthy();
    expect(screen.getByText("人太多，改成一个人做")).toBeTruthy();
    expect(screen.getAllByText(/第 2 版/).length).toBeGreaterThan(0);
  });

  it("上一版缺失时不画 diff", () => {
    render(
      <ResumeCard
        paused={teamPreview({
          checkpoint_id: "tp2",
          revision: 2,
          revised_from: "tp-gone",
          revision_note: "人太多，改成一个人做",
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByText("第 2 版 · 按你的意见修订")).toBeTruthy();
    expect(screen.getByText("人太多，改成一个人做")).toBeTruthy();
    expect(screen.queryByTestId("kickoff-revision-diff")).toBeNull();
    expect(screen.queryByText("无变化")).toBeNull();
    expect(screen.queryByText(/相对上一版/)).toBeNull();
  });

  it("上一版在冷 store 时标注成员增删", () => {
    upsertColdRequired({
      kind: "team_preview",
      conversationId: "c1",
      messageId: "m1",
      payload: {
        checkpoint_id: "tp1",
        primitive: "delegate",
        workers: [
          { run_id: "r1", role: "调研", task: "做A", depends_on: [] },
          { run_id: "r2", role: "审校", task: "做B", depends_on: [] },
        ],
      },
    });
    markColdResolved({
      kind: "team_preview",
      id: "tp1",
      resolution: { decision: "adjust", note: "改成一个人" },
    });
    render(
      <ResumeCard
        paused={teamPreview({
          checkpoint_id: "tp2",
          revision: 2,
          revised_from: "tp1",
          revision_note: "改成一个人",
          workers: [
            {
              run_id: "n1",
              role: "调研",
              task: "做A",
              depends_on: [],
              write_capability: "can_write_files",
              write_capability_label: "可改文件",
            },
          ],
        })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByTestId("kickoff-revision-diff").textContent).toContain(
      "去掉 审校",
    );
    expect(screen.getByText("相对上一版")).toBeTruthy();
  });

  it("开工卡不再提供 research_first 第三键（庭前取证内化）", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={teamPreview({
          primitive: "debate",
          workers: [],
          motion: "辩题",
          sides: [{ name: "正方", stance: "赞成" }],
        })}
        onResume={onResume}
      />,
    );
    expect(screen.queryByText("先多视角调研再辩")).toBeNull();
    expect(screen.getByText("开赛")).toBeTruthy();
  });
});

describe("ResumeCard · ask_user browser_login", () => {
  it("renders 需要你登录 + Sandbox 引导；可开直播；无假打开浏览器", () => {
    const onOpenLive = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          browser_login: true,
          question: "请登录目标站点",
        })}
        onResume={vi.fn()}
        onOpenLive={onOpenLive}
      />,
    );
    expect(screen.getByText(/需要你登录/)).toBeTruthy();
    expect(screen.getByText("请登录目标站点")).toBeTruthy();
    expect(screen.getByText(/Sandbox/)).toBeTruthy();
    expect(screen.queryByText(/手机暂无内嵌浏览器/)).toBeNull();
    expect(screen.queryByText(/桌面端完成登录/)).toBeNull();
    expect(screen.getByText("已登录，继续")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.getByTestId("browser-login-open-live")).toBeTruthy();
    fireEvent.click(screen.getByText("查看直播"));
    expect(onOpenLive).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("跳过")).toBeNull();
    expect(screen.queryByText("停止")).toBeNull();
    expect(screen.queryByText("打开浏览器")).toBeNull();
    expect(screen.queryByText("需要你拍板（已离线保留）")).toBeNull();
  });

  it("无 onOpenLive 时不显示「查看直播」", () => {
    render(
      <ResumeCard
        paused={summary({ browser_login: true, question: "登录" })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("browser-login-open-live")).toBeNull();
    expect(screen.getByText(/Sandbox/)).toBeTruthy();
  });

  it("已登录，继续 → continue + note「已登录，继续」", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({ browser_login: true, question: "登录" })}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByText("已登录，继续"));
    expect(onResume).toHaveBeenCalledWith("continue", "已登录，继续", []);
  });

  it("取消 → stop（wire decision=stop）", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({ browser_login: true, question: "登录" })}
        onResume={onResume}
      />,
    );
    fireEvent.click(screen.getByText("取消"));
    expect(onResume).toHaveBeenCalledWith("stop", "", []);
  });

  it("有 assumptions →「按假设继续」+ note=假设文案", () => {
    const onResume = vi.fn();
    render(
      <ResumeCard
        paused={summary({
          browser_login: true,
          question: "登录",
          assumptions: [{ id: "a0", label: "登录", value: "用户已登录" }],
        })}
        onResume={onResume}
      />,
    );
    // 冷路挂起没有墙钟——只能说「一直等你」，不得承诺自动按假设继续。
    expect(
      screen.getByText(
        /不会自动继续——这条一直等你；点「按假设继续」才按此走：登录：用户已登录/,
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("按假设继续"));
    expect(onResume).toHaveBeenCalledWith("continue", "登录：用户已登录", []);
  });

  it("无 assumptions 时不显示「按假设继续」", () => {
    render(
      <ResumeCard
        paused={summary({ browser_login: true, question: "登录" })}
        onResume={vi.fn()}
      />,
    );
    expect(screen.queryByText("按假设继续")).toBeNull();
  });

  it("普通 ask 不受影响：仍是拍板标题 + 取消", () => {
    render(<ResumeCard paused={summary()} onResume={vi.fn()} />);
    expect(screen.getByText("需要你拍板（已离线保留）")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.queryByText(/需要你登录/)).toBeNull();
    expect(screen.queryByTestId("browser-login-decision")).toBeNull();
  });
});

describe("ResumeCard · resume_deferred", () => {
  it("deferredBusyReason paints 放行已记下 and hides actions", () => {
    render(
      <ResumeCard
        paused={{
          ...summary(),
          interactionStatus: "submitting",
          deferredBusyReason: "live_turn",
        }}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByTestId("resume-card-deferred")).toBeTruthy();
    expect(screen.getByText(/放行已记下/)).toBeTruthy();
    expect(screen.getByText("其它回合进行中")).toBeTruthy();
    expect(screen.queryByText("取消")).toBeNull();
  });

  it("wrap_up reason shows host wrap-up detail", () => {
    render(
      <ResumeCard
        paused={{
          ...summary(),
          interactionStatus: "submitting",
          deferredBusyReason: "wrap_up",
        }}
        onResume={vi.fn()}
      />,
    );
    expect(screen.getByText("宿主回合收口中")).toBeTruthy();
  });
});
