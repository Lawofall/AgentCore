// @vitest-environment jsdom
/**
 * Continue 不得把未知已删 folder action 当履约。
 * 打开不预选 `default`；普通选项走 onContinue。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import {
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
import { DESKTOP_REQUIRED_HINT } from "@/lib/desktopDownload";
import type { AskOption } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

/** Leftover wire from a deleted Ask action — runtime string, not in the union. */
const staleReadonlyOption = {
  label: "授权访问本机目录",
  action: "grant_readonly_folder",
} as unknown as AskOption;

const grantDefaultContent: AskUserContent = {
  question: "需要本机目录吗？",
  questions: [
    {
      id: "q0",
      prompt: "授权",
      kind: "choice",
      options: [staleReadonlyOption, { label: "继续用云端" }],
      multiple: false,
      default: "授权访问本机目录",
    },
  ],
};

function Harness({
  content = grantDefaultContent,
  onContinue = vi.fn(),
  onBindResolve = vi.fn(async () => {}),
}: {
  content?: AskUserContent;
  onContinue?: () => void;
  onBindResolve?: (composed: string) => void | Promise<void>;
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
      conversationId="conv-1"
      onBindResolve={onBindResolve}
    />
  );
}

describe("AskDecisionBody Continue + unknown deleted folder action", () => {
  beforeEach(() => {
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
    } as unknown as typeof window.fsApi;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // biome-ignore lint/performance/noDelete: 测后清掉 stub，避免污染其它套件
    delete (window as { fsApi?: unknown }).fsApi;
  });

  it("stale action click then 提交 is ordinary submit", () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});

    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
  });

  it("normal non-folder selection still uses onContinue", () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /继续用云端/ }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
  });

  it("option-row stale action click is ordinary toggle", () => {
    const onBindResolve = vi.fn(async () => {});

    const content: AskUserContent = {
      ...grantDefaultContent,
      questions: [
        {
          ...grantDefaultContent.questions[0],
          default: "",
        },
      ],
    };
    render(<Harness content={content} onBindResolve={onBindResolve} />);
    const staleBtn = screen.getByRole("button", { name: /授权访问本机目录/ });
    fireEvent.click(staleBtn);

    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(staleBtn.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("AskDecisionBody generic option one-line", () => {
  afterEach(cleanup);

  it("does not paint model option second sentences; message omitted when questions exist", () => {
    const content: AskUserContent = {
      question: "用哪种格式？\n背景说明应保留",
      questions: [
        {
          id: "q0",
          prompt: "选一种",
          kind: "choice",
          options: [
            { label: "Markdown", detail: "一周内可验证" },
            { label: "PDF", detail: "方便打印" },
          ],
          multiple: false,
          default: "Markdown",
        },
      ],
    };
    render(<Harness content={content} />);
    expect(screen.queryByText(/用哪种格式？/)).toBeNull();
    expect(screen.queryByText(/背景说明应保留/)).toBeNull();
    expect(screen.getByText("选一种")).toBeTruthy();
    expect(screen.getByText("Markdown")).toBeTruthy();
    expect(screen.getByText("PDF")).toBeTruthy();
    expect(screen.queryByText("一周内可验证")).toBeNull();
    expect(screen.queryByText("方便打印")).toBeNull();
  });
});

describe("AskDecisionBody question stems", () => {
  afterEach(cleanup);

  it("does not paint message as a banner title when questions exist", () => {
    const content: AskUserContent = {
      question: "总标题不要画",
      questions: [
        {
          id: "q0",
          prompt: "这一题",
          kind: "choice",
          options: [{ label: "A" }, { label: "B" }],
          multiple: false,
          default: "A",
        },
      ],
    };
    render(<Harness content={content} />);
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.queryByText("总标题不要画")).toBeNull();
    expect(screen.getByText("这一题")).toBeTruthy();
  });

  it("paints message as the sole stem when there are no questions", () => {
    render(
      <Harness
        content={{
          question: "选 A 还是 B？",
          questions: [],
        }}
      />,
    );
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.getByText("选 A 还是 B？")).toBeTruthy();
    expect(screen.getAllByText("选 A 还是 B？")).toHaveLength(1);
  });

  it("paints each question prompt and not the message title", () => {
    const content: AskUserContent = {
      question: "总标题不要画",
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
      ],
    };
    render(<Harness content={content} />);
    expect(screen.queryByText("总标题不要画")).toBeNull();
    expect(screen.getByText("第一题")).toBeTruthy();
    expect(screen.queryByText("第二题")).toBeNull();
    expect(
      (
        screen.getByRole("button", {
          name: "第 2 题，共 2 题",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    fireEvent.click(screen.getByText("A1"));
    fireEvent.click(screen.getByRole("button", { name: /^下一题$/ }));
    expect(screen.getByText("第二题")).toBeTruthy();
    expect(screen.queryByText("第一题")).toBeNull();
  });

  it("falls back to message when the only question has an empty prompt", () => {
    const content: AskUserContent = {
      question: "用这句话当题干",
      questions: [
        {
          id: "q0",
          prompt: "",
          kind: "choice",
          options: [{ label: "A" }, { label: "B" }],
          multiple: false,
          default: "A",
        },
      ],
    };
    render(<Harness content={content} />);
    expect(screen.getByText("用这句话当题干")).toBeTruthy();
    expect(screen.getAllByText("用这句话当题干")).toHaveLength(1);
    expect(screen.getByText("A")).toBeTruthy();
  });

  it("paints a fill-in when a choice question has no options", () => {
    const content: AskUserContent = {
      question: "总标题不要画",
      questions: [
        {
          id: "q0",
          prompt: "还缺什么？",
          kind: "choice",
          options: [],
          multiple: false,
          default: "",
        },
      ],
    };
    render(<Harness content={content} />);
    expect(screen.getByText("还缺什么？")).toBeTruthy();
    expect(screen.getByPlaceholderText("填写你的答案")).toBeTruthy();
    expect(screen.queryByText("总标题不要画")).toBeNull();
  });
});

describe("AskDecisionBody Continue + deleted action on Web", () => {
  beforeEach(async () => {
    const { hasLocalFiles } = await import("@/lib/capabilities");
    vi.mocked(hasLocalFiles).mockReturnValue(false);
    window.__WEB__ = true;
    vi.spyOn(window, "open").mockReturnValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.__WEB__ = undefined;
  });

  it("Continue with stale action after picking is ordinary submit — no download", () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    expect(screen.queryByText(new RegExp(DESKTOP_REQUIRED_HINT))).toBeNull();
  });
});
