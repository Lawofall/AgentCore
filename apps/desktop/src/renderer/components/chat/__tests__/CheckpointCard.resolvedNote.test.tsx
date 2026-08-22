// @vitest-environment jsdom
/**
 * 已定案检查点存根：默认收起成单行（有结论文才画标签 + 用户答复摘要），点击展开见问题全文、
 * 选项 chips 与答复明细。普通澄清确认不画「已按你的决定继续」。收起态不展示 CEO 原问题。
 */

import type { CheckpointDisplay } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CheckpointCard } from "../CheckpointCard";

afterEach(cleanup);

const resolvedKickoff: CheckpointDisplay = {
  id: "cp-1",
  question: "关于论文有几个方向想先跟你对齐",
  assumptions: [],
  questions: [],
  intent: "kickoff",
  status: "resolved",
  decision: "continue",
  note: "就按这个方案开做：\n· 定位？：综述型\n· 读者？：公开发表\n· 篇幅？：精简干货",
  selected: [],
};

describe("ResolvedCheckpoint 单行折叠", () => {
  it("默认收起单行：普通澄清确认不画套话，答复摘要常驻；点击展开见全文", () => {
    render(<CheckpointCard checkpoint={resolvedKickoff} />);

    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    // 收起摘要是 note（截断展示），不是 CEO 问题。
    expect(document.body.textContent).toContain("就按这个方案开做：");
    expect(document.body.textContent).not.toContain(resolvedKickoff.question);

    fireEvent.click(screen.getByText(/就按这个方案开做/));
    expect(document.body.textContent).toContain(resolvedKickoff.question);
    expect(document.body.textContent).toContain("· 定位？：综述型");
    expect(document.body.textContent).toContain("· 篇幅？：精简干货");
  });

  it("无 note 时折叠摘要用 selected；专用卡无答复则只留结论标签", () => {
    const withSelected: CheckpointDisplay = {
      ...resolvedKickoff,
      id: "cp-2",
      intent: "proposal_pick",
      question: "选哪条方案推进？",
      selected: ["方案 C：外包试点"],
      note: "",
    };
    render(<CheckpointCard checkpoint={withSelected} />);

    expect(screen.getByText("已选定方案")).toBeTruthy();
    expect(document.body.textContent).toContain("方案 C：外包试点");
    expect(document.body.textContent).not.toContain("选哪条方案推进？");

    cleanup();

    const labelOnly: CheckpointDisplay = {
      ...withSelected,
      id: "cp-3",
      selected: [],
      note: "",
    };
    render(<CheckpointCard checkpoint={labelOnly} />);
    expect(screen.getByText("已选定方案")).toBeTruthy();
    expect(document.body.textContent).not.toContain("选哪条方案推进？");
  });

  it("proposal_pick：选项 chips 收起时随卡隐藏、展开后显示", () => {
    const resolvedProposal: CheckpointDisplay = {
      ...resolvedKickoff,
      id: "cp-2",
      intent: "proposal_pick",
      question: "选哪条方案推进？",
      selected: ["方案 C：外包试点"],
      note: "",
    };
    render(<CheckpointCard checkpoint={resolvedProposal} />);

    // 收起：结论 + selected 摘要（truncate 行），展开区 chips 尚未挂载。
    expect(screen.getByText("已选定方案")).toBeTruthy();
    const stub = screen.getByText("已选定方案").closest("button");
    expect(stub?.textContent).toContain("方案 C：外包试点");

    fireEvent.click(screen.getByText("已选定方案"));
    expect(screen.getByText("方案 C：外包试点")).toBeTruthy();
    expect(document.body.textContent).toContain("选哪条方案推进？");
  });

  it("stop / research_first resolved 占「已取消本回合」存根；收起不见问句", () => {
    for (const decision of ["stop", "research_first"] as const) {
      render(
        <CheckpointCard
          checkpoint={{
            ...resolvedKickoff,
            id: `cp-${decision}`,
            decision,
            note: "",
          }}
        />,
      );
      expect(screen.getByText("已取消本回合")).toBeTruthy();
      expect(document.body.textContent).not.toContain(resolvedKickoff.question);
      fireEvent.click(screen.getByText("已取消本回合"));
      expect(document.body.textContent).toContain(resolvedKickoff.question);
      cleanup();
    }
  });

  it("缺 decision 不猜成超时", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedKickoff,
          id: "cp-unknown",
          decision: null,
          note: "",
        }}
      />,
    );
    expect(screen.getByText("已经处理过了")).toBeTruthy();
    expect(screen.queryByText("未及时回应，已自行收尾")).toBeNull();
  });
});
