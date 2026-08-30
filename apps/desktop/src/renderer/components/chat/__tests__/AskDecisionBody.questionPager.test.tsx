// @vitest-environment jsdom
/**
 * 多题通用澄清卡：n≥2 一题一面；编号可点切换（没写补充也能切）；非末题「下一题」不 resume。
 * choice 选项下本题人话。不是 Wizard（无「下一步」、无进度条、可回看已访）。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import {
  AskQuestionPager,
  resolveAskPrimaryAction,
} from "@/components/chat/ask/AskQuestionPager";
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
      options: [{ label: "方案 A（推荐）" }, { label: "方案 B" }],
      multiple: false,
      default: "方案 A（推荐）",
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

function primaryButton(name: RegExp | string) {
  return screen.getByRole("button", { name }) as HTMLButtonElement;
}

describe("resolveAskPrimaryAction", () => {
  it("advances on a non-last question and submits only when every question is visited", () => {
    expect(resolveAskPrimaryAction(1, 0, new Set([0]))).toEqual({
      type: "submit",
    });
    expect(resolveAskPrimaryAction(2, 0, new Set([0]))).toEqual({
      type: "advance",
      index: 1,
    });
    expect(resolveAskPrimaryAction(2, 1, new Set([0, 1]))).toEqual({
      type: "submit",
    });
    expect(resolveAskPrimaryAction(3, 2, new Set([0, 2]))).toEqual({
      type: "jump",
      index: 1,
    });
  });
});

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

  it("opens a single question with default unselected and 提交 disabled", () => {
    const onContinue = vi.fn();
    render(
      <Harness
        onContinue={onContinue}
        content={{
          question: "只一题",
          assumptions: [],
          questions: [
            {
              id: "q0",
              prompt: "选一种",
              kind: "choice",
              options: [{ label: "甲（推荐）" }, { label: "乙" }],
              multiple: false,
              default: "甲（推荐）",
            },
          ],
        }}
      />,
    );
    expect(
      screen
        .getByText("甲（推荐）")
        .closest("button")
        ?.getAttribute("aria-pressed"),
    ).toBe("false");
    expect(screen.getByText("默认")).toBeTruthy();
    expect(screen.queryByText(/^推荐$/)).toBeNull();
    expect(primaryButton(/^提交$/).disabled).toBe(true);
    expect(primaryButton(/^取消$/).disabled).toBe(false);

    fireEvent.click(screen.getByText("甲（推荐）"));
    expect(primaryButton(/^提交$/).disabled).toBe(false);
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("enables 提交 after writing a note without picking an option", () => {
    const onContinue = vi.fn();
    render(
      <Harness
        onContinue={onContinue}
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
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value: "选项都不对，我要第三条" },
    });
    expect(primaryButton(/^提交$/).disabled).toBe(false);
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("keeps 提交 enabled on a no-question card", () => {
    const onContinue = vi.fn();
    render(
      <Harness
        onContinue={onContinue}
        content={{ question: "选 A 还是 B？", assumptions: [], questions: [] }}
      />,
    );
    expect(primaryButton(/^提交$/).disabled).toBe(false);
    expect(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeTruthy();
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("does not stack a second note field on a text question", () => {
    render(
      <Harness
        content={{
          question: "只一题",
          assumptions: [],
          questions: [
            {
              id: "q0",
              prompt: "你的名字",
              kind: "text",
              options: [],
              multiple: false,
              default: "",
            },
          ],
        }}
      />,
    );
    expect(screen.queryByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeNull();
    expect(screen.getByPlaceholderText("填写你的答案")).toBeTruthy();
  });

  it("shows one question at a time; numbers switch without a 补充", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "切换问题" })).toBeTruthy();
    expect(screen.getByText("开工方式选哪种？")).toBeTruthy();
    expect(screen.queryByText("前端技术栈用哪个？")).toBeNull();
    expect(screen.getByText("方案 A（推荐）")).toBeTruthy();
    expect(screen.queryByText("React")).toBeNull();
    expect(screen.queryByText(/^推荐$/)).toBeNull();
    expect(screen.getByText("默认")).toBeTruthy();

    const q2 = primaryButton("第 2 题，共 2 题");
    expect(q2.disabled).toBe(false);
    fireEvent.click(q2);
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
    expect(screen.queryByText("开工方式选哪种？")).toBeNull();
    expect(primaryButton(/^提交$/).disabled).toBe(true);
  });

  it("uses 下一题 on the first question and only resumes on the last 提交", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    const next = primaryButton(/^下一题$/);
    expect(next.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /下一步/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^提交$/ })).toBeNull();
    const cancel = primaryButton(/^取消$/);
    expect(
      cancel.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(screen.getByText("方案 B"));
    expect(next.disabled).toBe(false);
    fireEvent.click(next);
    expect(onContinue).not.toHaveBeenCalled();
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
    expect(screen.queryByText("开工方式选哪种？")).toBeNull();
    expect(primaryButton(/^提交$/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^下一题$/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "第 1 题，共 2 题" }));
    expect(screen.getByText("开工方式选哪种？")).toBeTruthy();
    expect(primaryButton(/^下一题$/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    fireEvent.click(screen.getByText("Vue"));
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("walks three questions with 下一题 then 提交, and never labels the CTA 下一步", () => {
    const onContinue = vi.fn();
    const three: AskUserContent = {
      question: "总标题不要画",
      assumptions: [],
      questions: [
        {
          id: "q0",
          prompt: "第一题",
          kind: "choice",
          options: [{ label: "A1" }],
          multiple: false,
          default: "",
        },
        {
          id: "q1",
          prompt: "第二题",
          kind: "choice",
          options: [{ label: "B1" }],
          multiple: false,
          default: "",
        },
        {
          id: "q2",
          prompt: "第三题",
          kind: "choice",
          options: [{ label: "C1" }],
          multiple: false,
          default: "",
        },
      ],
    };
    render(<Harness content={three} onContinue={onContinue} />);
    expect(primaryButton("第 3 题，共 3 题").disabled).toBe(false);
    fireEvent.click(screen.getByText("A1"));
    fireEvent.click(primaryButton(/^下一题$/));
    expect(onContinue).not.toHaveBeenCalled();
    expect(screen.getByText("第二题")).toBeTruthy();
    expect(primaryButton("第 3 题，共 3 题").disabled).toBe(false);
    fireEvent.click(screen.getByText("B1"));
    fireEvent.click(primaryButton(/^下一题$/));
    expect(screen.getByText("第三题")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /下一步/ })).toBeNull();
    fireEvent.click(screen.getByText("C1"));
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("lets a note advance 下一题 without picking an option", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value: "选项都不对" },
    });
    fireEvent.click(primaryButton(/^下一题$/));
    expect(onContinue).not.toHaveBeenCalled();
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
  });

  it("lets the pager jump to an unvisited number without a 补充", () => {
    const onChange = vi.fn();
    render(
      <AskQuestionPager
        total={3}
        index={0}
        visited={new Set([0])}
        onChange={onChange}
      />,
    );
    const q2 = primaryButton("第 2 题，共 3 题");
    expect(q2.disabled).toBe(false);
    fireEvent.click(q2);
    expect(onChange).toHaveBeenCalledWith(1);
    fireEvent.click(primaryButton("第 3 题，共 3 题"));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("picking an option without 补充 still lets the number switch", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    fireEvent.click(screen.getByText("方案 B"));
    fireEvent.click(primaryButton("第 2 题，共 2 题"));
    expect(onContinue).not.toHaveBeenCalled();
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
    expect(primaryButton(/^提交$/).disabled).toBe(true);
  });

  it("does not let a note on question 1 submit question 2 unanswered", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value: "第一题我要别的" },
    });
    fireEvent.click(primaryButton(/^下一题$/));
    expect(screen.getByText("前端技术栈用哪个？")).toBeTruthy();
    expect(
      (screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER) as HTMLTextAreaElement)
        .value,
    ).toBe("");
    expect(primaryButton(/^提交$/).disabled).toBe(true);
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "第 1 题，共 2 题" }));
    expect(screen.getByText("开工方式选哪种？")).toBeTruthy();
    expect(
      (screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER) as HTMLTextAreaElement)
        .value,
    ).toBe("第一题我要别的");
  });

  it("submits after each question has its own note", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value: "第一题人话" },
    });
    fireEvent.click(primaryButton(/^下一题$/));
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value: "第二题人话" },
    });
    expect(primaryButton(/^提交$/).disabled).toBe(false);
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("keeps per-question notes and assumptions while switching", () => {
    render(<Harness />);
    expect(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeTruthy();
    expect(screen.getByText("起步计划")).toBeTruthy();
    expect(screen.getByText("单页落地")).toBeTruthy();
    fireEvent.click(screen.getByText("方案 A（推荐）"));
    fireEvent.click(primaryButton(/^下一题$/));
    expect(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER)).toBeTruthy();
    expect(screen.getByText("起步计划")).toBeTruthy();
    expect(screen.getByText("单页落地")).toBeTruthy();
  });

  it("preserves picks after switching away and back", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("方案 B"));
    fireEvent.click(primaryButton(/^下一题$/));
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

  it("does not seed default on a later question", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("方案 A（推荐）"));
    fireEvent.click(primaryButton(/^下一题$/));
    expect(
      screen.getByText("React").closest("button")?.getAttribute("aria-pressed"),
    ).toBe("false");
    expect(primaryButton(/^提交$/).disabled).toBe(true);
  });

  it("disables 提交 on the last question if an earlier pick was cleared", () => {
    const onContinue = vi.fn();
    render(<Harness onContinue={onContinue} />);
    fireEvent.click(screen.getByText("方案 B"));
    fireEvent.click(primaryButton(/^下一题$/));
    fireEvent.click(screen.getByText("Vue"));
    expect(primaryButton(/^提交$/).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "第 1 题，共 2 题" }));
    fireEvent.click(screen.getByText("方案 B"));
    fireEvent.click(screen.getByRole("button", { name: "第 2 题，共 2 题" }));
    expect(primaryButton(/^提交$/).disabled).toBe(true);
    fireEvent.click(primaryButton(/^提交$/));
    expect(onContinue).not.toHaveBeenCalled();
  });
});
