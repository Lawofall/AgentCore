// @vitest-environment jsdom
/**
 * 已定案检查点：摘要行两态相同（短题干 · 答复）。装得下就不再展开。
 * 展开只补被截掉的全文，不把「题干：答复」再画一遍。
 * 普通澄清确认不画「已按你的决定继续」。
 */

import type { CheckpointDisplay } from "@/stores/conversation";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CheckpointCard } from "../CheckpointCard";

afterEach(cleanup);

const resolvedDecision: CheckpointDisplay = {
  id: "cp-1",
  question: "关于论文有几个方向想先跟你对齐",
  questions: [],
  intent: "decision",
  status: "resolved",
  decision: "continue",
  note: "就按这个方案开做：\n· 定位？：综述型\n· 读者？：公开发表\n· 篇幅？：精简干货",
  selected: [],
};

function body(): HTMLElement {
  const node = document.querySelector("[data-ask-settled-body]");
  if (!(node instanceof HTMLElement)) throw new Error("missing settled body");
  return node;
}

describe("ResolvedCheckpoint 摘要行", () => {
  it("收起和展开都留着短题干与答复，展开只补分题", () => {
    render(<CheckpointCard checkpoint={resolvedDecision} />);

    expect(screen.queryByText("已按你的决定继续")).toBeNull();
    const button = screen.getByRole("button");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.textContent).toContain("关于论文有几个方向想先跟你对齐");
    expect(button.textContent).toContain("综述型 · 公开发表 · 精简干货");
    expect(button.textContent).not.toContain("就按这个方案开做");
    const answer = button.querySelector("[data-ask-answer]");
    expect(answer?.className).toContain("text-sm");
    expect(answer?.className).toContain("max-w-64");
    expect(answer?.className).toContain("shrink-0");
    expect(answer?.className).not.toContain("text-xs");
    expect(answer?.className).not.toContain("font-medium");
    const row = button.className.split(/\s+/) ?? [];
    expect(row).toContain("w-auto");
    expect(row).toContain("max-w-full");
    expect(row).not.toContain("w-full");
    expect(body().parentElement?.getAttribute("aria-hidden")).toBe("true");
    expect(body().textContent).toContain("定位？");
    expect(body().textContent).not.toContain("定位？：综述型");

    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(button.textContent).toContain("关于论文有几个方向想先跟你对齐");
    expect(button.textContent).toContain("综述型 · 公开发表 · 精简干货");
    expect(body().parentElement?.hasAttribute("aria-hidden")).toBe(false);
    expect(body().textContent).toContain("篇幅？");
    expect(body().textContent).toContain("精简干货");
    expect(document.body.textContent).not.toContain("就按这个方案开做：");
  });

  it("从 compose note 去掉套话，题干和答复都留在摘要行", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-legacy-heading",
          question: "",
          note: "我的答复：\n· 你心里的「Agent 生态」更接近哪种？：都不太对",
        }}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).not.toContain("我的答复：");
    expect(document.body.textContent).toContain(
      "你心里的「Agent 生态」更接近哪种？",
    );
    expect(document.body.textContent).toContain("都不太对");
    expect(document.body.textContent).not.toContain(
      "你心里的「Agent 生态」更接近哪种？：都不太对",
    );
  });

  it("问句和选项都装进摘要行时不再展开", () => {
    const withSelected: CheckpointDisplay = {
      ...resolvedDecision,
      id: "cp-2",
      question: "这一题的问句",
      note: "",
      selected: ["方案 C：外包试点"],
    };
    render(<CheckpointCard checkpoint={withSelected} />);

    expect(screen.queryByText("已选定方案")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).toContain("这一题的问句");
    expect(document.body.textContent).toContain("方案 C：外包试点");
    expect(document.querySelector("[data-ask-settled-body]")).toBeNull();

    cleanup();

    const labelOnly: CheckpointDisplay = {
      ...withSelected,
      id: "cp-3",
      selected: [],
      note: "",
    };
    render(<CheckpointCard checkpoint={labelOnly} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByLabelText("拍板记录")).toBeNull();
    expect(document.body.textContent).toContain("这一题的问句");
  });

  it("答复超出摘要预算时展开用徽章，标题行仍留着", () => {
    const long = `方案 C：${"外包".repeat(20)}`;
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-long",
          question: "这一题的问句",
          note: "",
          selected: [long],
        }}
      />,
    );
    const button = screen.getByRole("button");
    expect(button.textContent).toContain("这一题的问句");
    expect(button.textContent).toContain(long);
    expect(body().parentElement?.getAttribute("aria-hidden")).toBe("true");
    expect(body().textContent).toContain(long);

    fireEvent.click(button);
    expect(button.textContent).toContain("这一题的问句");
    expect(button.textContent).toContain(long);
    expect(body().querySelector(".rounded-full")?.textContent).toBe(long);
  });

  it("括号提示只在展开里，答复不画第二遍", () => {
    const prompt =
      "这次要协作图帮你做出什么？（一句话说清要什么、给谁用、做到哪一档即可）";
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-shot",
          question: "批次总述",
          questions: [
            {
              id: "q0",
              prompt,
              kind: "text",
              options: [],
              multiple: false,
              default: "",
            },
          ],
          note: `${prompt}：我测试`,
          selected: [],
        }}
      />,
    );
    const button = screen.getByRole("button");
    expect(button.textContent).toContain("这次要协作图帮你做出什么？");
    expect(button.textContent).toContain("我测试");
    expect(button.textContent).not.toContain("一句话说清");
    expect(button.textContent).not.toContain("批次总述");
    expect(body().textContent).toContain("一句话说清");
    expect(body().textContent).not.toContain("我测试");
    expect(body().textContent).not.toContain(`${prompt}：我测试`);

    fireEvent.click(button);
    expect(button.textContent).toContain("这次要协作图帮你做出什么？");
    expect(button.textContent).toContain("我测试");
  });

  it("stop resolved 占「已取消本回合」；题干在展开，标题不换", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-stop",
          decision: "stop",
          note: "",
        }}
      />,
    );
    const button = screen.getByRole("button");
    const label = screen.getByText("已取消本回合");
    expect(label.className).toContain("text-sm");
    expect(label.className).not.toContain("text-xs");
    expect(label.className).not.toContain("font-medium");
    const row = button.className.split(/\s+/) ?? [];
    expect(row).toContain("w-auto");
    expect(row).not.toContain("w-full");
    expect(button.textContent).not.toContain(resolvedDecision.question);
    expect(body().parentElement?.getAttribute("aria-hidden")).toBe("true");
    expect(body().textContent).toContain(resolvedDecision.question);

    fireEvent.click(button);
    expect(button.textContent).toContain("已取消本回合");
    expect(body().parentElement?.hasAttribute("aria-hidden")).toBe(false);
    expect(body().textContent).toContain(resolvedDecision.question);
  });

  it("有结构题干时摘要用该题干，不用 question 总述", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-prompts",
          question: "批次总述",
          questions: [
            {
              id: "q0",
              prompt: "这一题的问句",
              kind: "choice",
              options: [{ label: "方案 C：外包试点" }],
              multiple: false,
              default: "",
            },
          ],
        }}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.body.textContent).toContain("这一题的问句");
    expect(document.body.textContent).toContain("综述型 · 公开发表 · 精简干货");
    expect(document.body.textContent).not.toContain("批次总述");
    expect(document.body.textContent).not.toContain("定位？：综述型");
  });

  it("缺 decision 不猜成超时", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-unknown",
          decision: null,
          note: "",
        }}
      />,
    );
    expect(screen.getByText("已经处理过了")).toBeTruthy();
    expect(screen.queryByText("未及时回应，已自行收尾")).toBeNull();
    expect(screen.getByRole("button").textContent).not.toContain(
      resolvedDecision.question,
    );
  });

  it("什么都没有时只留拍板记录", () => {
    render(
      <CheckpointCard
        checkpoint={{
          ...resolvedDecision,
          id: "cp-empty",
          question: "",
          note: "",
          selected: [],
        }}
      />,
    );
    expect(screen.getByLabelText("拍板记录")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
