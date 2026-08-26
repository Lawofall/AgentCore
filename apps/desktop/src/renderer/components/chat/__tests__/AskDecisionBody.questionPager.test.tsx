// @vitest-environment jsdom
/**
 * 多题通用澄清卡：n≥2 一题一面 + 编号乱序跳；n=1 无分页铬。
 * 提交仍一次交整张卡；人话框常驻。不是 Wizard（无「下一步」）。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import {
  ASK_NOTE_PLACEHOLDER,
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

const twoQuestions: AskUserContent = {
  question: "总标题不要画",
  assumptions: [{ id: "a0", label: "交付物", value: "单页落地" }],
  questions: [
    {
      id: "q0",
      prompt: "开工方式选哪种？",
      kind: "choice",
      options: [{ label: "方案 A" }, { label: "方案 B" }],
      multiple: false,
      default: "方案 A",
    },
    {
      id: "q1",
      prompt: "前端技术栈用哪个？",
      kind: "choice",
      options: [{ label: "React" }, { label: "Vue" }],
      multiple: false,
      default: "React",
    },
  ],
};

function Harness({
  content = twoQuestions,
  onContinue = vi.fn(),
}: {
  content?: AskUserContent;
  onContinue?: () => void;
}) {
  const answer = useAskAnswer(content);
  return (
    <AskDecisionBody
      content={content}
      answer={answer}
      busy={false}
      submitting={null}
      onContinue={onContinue}
      onStop={() => {}}
    />
  );
}

describe("AskDecisionBody question pager", () => {
  afterEach(cleanup);

  it("does not paint a pager for a single question", () => {
    render(
      <Harness
        content={{
          question: "只一题",
          assumptions: [],
          questions: [
            {
              id: "q0",
              prompt: "选一种",
              kind: "choice",
              options: [{ label: "甲" }, { label: "乙" }],
              multiple: false,
              default: "甲",
            },
          ],
        }}
      />,
    );
    expect(screen.queryByRole("group", { name: "切换问题" })).toBeNull();
    expect(screen.queryByRole("button", { name: /第 \d+ 题/ })).toBeNull();
    expect(screen.getByText("选一种")).toBeTruthy();
    expect(screen.getByText("甲")).toBeTruthy();
  });

  it("shows one question at a time and jumps by number", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "切换问题" })).toBeTruthy();
    expect(screen.getByText("开工方式选哪种？")).toBeTruthy();
    expect(screen.queryByText("前端技术栈用哪个？")).toBeNull();
    expect(screen.getByText("方案 A")).toBeTruthy();
    expect(screen.queryByText("React")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
    expect(screen.queryByText("开工方式选哪种？")).toBeNull();
    expect(screen.getByText("React")).toBeTruthy();
    expect(screen.queryByText("方案 A")).toBeNull();
    expect(screen.getByRole("button", { name: /提交/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "第 1 题，共 2 题" }));
    expect(screen.getByText("开工方式选哪种？")).toBeTruthy();
  });

  it("keeps 提交 as the only primary CTA on every step", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    expect(screen.getByRole("button", { name: /提交/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /下一步/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /提交/ }));
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("前端技术栈用哪个？")).toBeNull();
  });

  it("keeps the note and assumptions visible while switching", () => {
    render(<Harness />);
    expect(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeTruthy();
    expect(screen.getByText("起步计划")).toBeTruthy();
    expect(screen.getByText("单页落地")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    expect(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeTruthy();
    expect(screen.getByText("起步计划")).toBeTruthy();
    expect(screen.getByText("单页落地")).toBeTruthy();
  });

  it("preserves picks after switching away and back", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("方案 B"));
    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    fireEvent.click(screen.getByText("Vue"));
    fireEvent.click(screen.getByRole("button", { name: "第 1 题，共 2 题" }));
    expect(
      screen
        .getByText("方案 B")
        .closest("button")
        ?.getAttribute("aria-pressed"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    expect(
      screen.getByText("Vue").closest("button")?.getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("seeds default on the unvisited question", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    expect(
      screen.getByText("React").closest("button")?.getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
