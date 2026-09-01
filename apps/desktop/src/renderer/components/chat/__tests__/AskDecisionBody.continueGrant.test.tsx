// @vitest-environment jsdom
/**
 * Continue 不得把 listed grant_* 退化成口头「已授权」。
 * 已删的只读 Ask action 当普通选项（不履约、也不停提交）。
 * `grant_organize_folder` 须点授权行履约（打开不预选，底栏不会因 default 出现「允许整理」）。
 * 找不到 → 卡面失败（≠ cancelled 静默）。人话短同意仍可交。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import {
  ASK_NOTE_PLACEHOLDER,
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
import { DESKTOP_REQUIRED_HINT } from "@/lib/desktopDownload";
import type { AskOption } from "@/types/events";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pickAndGrantOrganizeFolder = vi.fn();
const pickAndGrantAttachFolder = vi.fn();

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/grantOrganizeFolder", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/grantOrganizeFolder")>();
  return {
    ...actual,
    pickAndGrantOrganizeFolder: (...args: unknown[]) =>
      pickAndGrantOrganizeFolder(...args),
    pickAndGrantAttachFolder: (...args: unknown[]) =>
      pickAndGrantAttachFolder(...args),
  };
});

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
  assumptions: [],
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
    pickAndGrantOrganizeFolder.mockReset();
    pickAndGrantAttachFolder.mockReset();
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

describe("AskDecisionBody organize confirm card", () => {
  beforeEach(() => {
    pickAndGrantOrganizeFolder.mockReset();
    pickAndGrantAttachFolder.mockReset();
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

  const organizeContent: AskUserContent = {
    question: "要把桌面「咨询」整理成 pdf 吗？",
    assumptions: [],
    questions: [
      {
        id: "q0",
        prompt: "整理授权",
        kind: "choice",
        options: [
          {
            label: "授权整理该目录",
            action: "grant_organize_folder",
            well_known: "desktop",
            target_name: "咨询",
          },
          { label: "先不整理" },
        ],
        multiple: false,
        default: "授权整理该目录",
      },
    ],
  };

  it("shows 将整理 target; mixed skip keeps 需要你拍板 until the grant row is used", () => {
    render(<Harness content={organizeContent} />);
    expect(screen.getByText("将整理：桌面 › 咨询")).toBeTruthy();
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.queryByText(/整理确认/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^允许整理$/ })).toBeNull();
    expect(
      (screen.getByRole("button", { name: /^提交$/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    // No picker framing
    expect(screen.queryByText(/选文件夹/)).toBeNull();
  });

  it("mixed scope+attach card stays 需要你拍板 (does not hijack to 加入本对话)", () => {
    const mixed: AskUserContent = {
      question: "现在还看不到效果，下一步怎么做？",
      assumptions: [],
      questions: [
        {
          id: "q0",
          prompt: "现在还看不到效果，下一步怎么做？",
          kind: "choice",
          options: [
            {
              label: "先搭一个最小演示页（推荐）",
              action: "grant_attach_folder",
            },
            { label: "先不搭，等以后做界面" },
          ],
          multiple: false,
          default: "",
        },
      ],
    };
    render(<Harness content={mixed} />);
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.queryByText(/加入本对话/)).toBeNull();
    expect(screen.queryByText(/将本机目录加入/)).toBeNull();
  });

  it("grant-only attach card uses 加入本对话 chrome and 允许改 target", () => {
    const attachOnly: AskUserContent = {
      question: "允许改这个目录？",
      assumptions: [],
      questions: [
        {
          id: "q0",
          prompt: "允许改这个目录？",
          kind: "choice",
          options: [
            {
              label: "允许改设计稿",
              action: "grant_attach_folder",
              well_known: "desktop",
              target_name: "设计稿",
            },
          ],
          multiple: false,
          default: "",
        },
      ],
    };
    render(<Harness content={attachOnly} />);
    expect(screen.getByText(/加入本对话/)).toBeTruthy();
    expect(screen.getByText("允许改：桌面 › 设计稿")).toBeTruthy();
  });

  it("grant option row fulfills organize via helper — no silent upgrade, no picker", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async (_answer: string) => {});
    pickAndGrantOrganizeFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "咨询", mode: "organize" },
      alias: "咨询",
      namespace: "external/咨询",
      displayLabel: "桌面 › 咨询",
    });

    render(
      <Harness
        content={organizeContent}
        onContinue={onContinue}
        onBindResolve={onBindResolve}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /授权整理该目录/ }));

    await waitFor(() => {
      expect(pickAndGrantOrganizeFolder).toHaveBeenCalledWith("conv-1", {
        wellKnown: "desktop",
        targetName: "咨询",
      });
    });
    expect(onContinue).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(onBindResolve).toHaveBeenCalled();
    });
    const composed = onBindResolve.mock.calls[0]?.[0] ?? "";
    expect(composed).toContain("授权整理该目录");
    expect(composed).toContain("桌面 › 咨询");
  });

  it("readonly→organize still requires confirm click (does not auto-fulfill on mount)", () => {
    // Same root may already be readonly-mounted; card must still wait for allow.
    render(<Harness content={organizeContent} />);
    expect(pickAndGrantOrganizeFolder).not.toHaveBeenCalled();
    expect(screen.getByText("将整理：桌面 › 咨询")).toBeTruthy();
  });

  /** 打开不预选；人话短同意在 listed 未勾选时可交。 */
  function typeNote(value: string) {
    fireEvent.change(screen.getByPlaceholderText(ASK_NOTE_PLACEHOLDER), {
      target: { value },
    });
  }

  it("note short affirm fulfills organize grant when listed unchecked", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async (_answer: string) => {});
    pickAndGrantOrganizeFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "咨询", mode: "organize" },
      alias: "咨询",
      namespace: "external/咨询",
      displayLabel: "桌面 › 咨询",
    });

    render(
      <Harness
        content={organizeContent}
        onContinue={onContinue}
        onBindResolve={onBindResolve}
      />,
    );
    typeNote("可以");
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(pickAndGrantOrganizeFolder).toHaveBeenCalledWith("conv-1", {
        wellKnown: "desktop",
        targetName: "咨询",
      });
    });
    expect(onContinue).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(onBindResolve).toHaveBeenCalled();
    });
    const composed = onBindResolve.mock.calls[0]?.[0] ?? "";
    expect(composed).toContain("授权整理该目录");
  });

  it("note 可以 on mixed organize+attach card fulfills organize, not attach", async () => {
    const mixed: AskUserContent = {
      question: "授权哪个？",
      assumptions: [],
      questions: [
        {
          id: "q0",
          prompt: "授权",
          kind: "choice",
          options: [
            {
              label: "加入可读写",
              action: "grant_attach_folder",
              well_known: "desktop",
              target_name: "咨询",
            },
            {
              label: "授权整理该目录",
              action: "grant_organize_folder",
              well_known: "desktop",
              target_name: "咨询",
            },
            { label: "先不整理" },
          ],
          multiple: false,
          default: "加入可读写",
        },
      ],
    };
    pickAndGrantOrganizeFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "咨询", alias: "咨询", mode: "organize" },
      alias: "咨询",
      namespace: "external/咨询",
      displayLabel: "桌面 › 咨询",
    });
    render(<Harness content={mixed} />);
    typeNote("可以");
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    await waitFor(() => {
      expect(pickAndGrantOrganizeFolder).toHaveBeenCalled();
    });
    expect(pickAndGrantAttachFolder).not.toHaveBeenCalled();
  });

  it("note ordinary text does not trigger organize grant", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});

    render(
      <Harness
        content={organizeContent}
        onContinue={onContinue}
        onBindResolve={onBindResolve}
      />,
    );
    typeNote("先放一放，下周再说");
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(pickAndGrantOrganizeFolder).not.toHaveBeenCalled();
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
  });

  it("note short affirm failure stays on card via setBindError", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantOrganizeFolder.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    render(
      <Harness
        content={organizeContent}
        onContinue={onContinue}
        onBindResolve={onBindResolve}
      />,
    );
    typeNote("允许整理");
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(pickAndGrantOrganizeFolder).toHaveBeenCalled();
    });
    expect(onContinue).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(screen.getByText("找不到该目录")).toBeTruthy();
  });

  it("shows structured 将整理 only — not the model option subtitle", () => {
    const withModelDetail: AskUserContent = {
      ...organizeContent,
      questions: [
        {
          ...organizeContent.questions[0],
          options: [
            {
              ...organizeContent.questions[0].options[0],
              detail: "模型发挥的副标题，不要画出来",
            },
            organizeContent.questions[0].options[1],
          ],
        },
      ],
    };
    render(<Harness content={withModelDetail} />);
    expect(screen.getByText("将整理：桌面 › 咨询")).toBeTruthy();
    expect(screen.queryByText(/模型发挥/)).toBeNull();
    expect(screen.queryByText(/将整理：桌面 › 咨询 · /)).toBeNull();
  });
});

describe("AskDecisionBody generic option one-line", () => {
  afterEach(cleanup);

  it("does not paint model option second sentences; message omitted when questions exist", () => {
    const content: AskUserContent = {
      question: "用哪种格式？\n背景说明应保留",
      assumptions: [],
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
      assumptions: [],
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
          assumptions: [],
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
      assumptions: [],
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
      assumptions: [],
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
