// @vitest-environment jsdom
/**
 * Continue 不得把预选 grant_* 退化成口头「已授权」——须先解析履约（无 picker）。
 * 找不到 → 卡面失败文案（≠ cancelled 静默）。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import type { AskUserContent } from "@/components/chat/ask/AskUserFields";
import { useAskAnswer } from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  DESKTOP_DOWNLOAD_URL,
  DESKTOP_REQUIRED_HINT,
} from "@/lib/desktopDownload";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pickAndGrantReadonlyFolder = vi.fn();
const pickAndGrantOrganizeFolder = vi.fn();

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/grantReadonlyFolder", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/grantReadonlyFolder")>();
  return {
    ...actual,
    pickAndGrantReadonlyFolder: (...args: unknown[]) =>
      pickAndGrantReadonlyFolder(...args),
  };
});

vi.mock("@/lib/grantOrganizeFolder", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/grantOrganizeFolder")>();
  return {
    ...actual,
    pickAndGrantOrganizeFolder: (...args: unknown[]) =>
      pickAndGrantOrganizeFolder(...args),
  };
});

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

const grantDefaultContent: AskUserContent = {
  question: "需要本机目录吗？",
  context: "",
  assumptions: [],
  questions: [
    {
      id: "q0",
      prompt: "授权",
      kind: "choice",
      options: [
        { label: "授权访问本机目录", action: "grant_readonly_folder" },
        { label: "继续用云端" },
      ],
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

describe("AskDecisionBody Continue + grant fulfillment", () => {
  beforeEach(() => {
    pickAndGrantReadonlyFolder.mockReset();
    pickAndGrantOrganizeFolder.mockReset();
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    // canLocalFs 需要 fsApi；履约本身已 mock，不必真实现。
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

  it("preselected grant default + Continue fulfills (not bare onContinue)", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(pickAndGrantReadonlyFolder).toHaveBeenCalledWith(
        "conv-1",
        undefined,
      );
    });
    expect(onContinue).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(screen.getByText("找不到该目录")).toBeTruthy();
  });

  it("forwards well_known / target_name hints to grant helper", async () => {
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });
    const content: AskUserContent = {
      ...grantDefaultContent,
      questions: [
        {
          ...grantDefaultContent.questions[0],
          options: [
            {
              label: "授权桌面报表",
              action: "grant_readonly_folder",
              well_known: "desktop",
              target_name: "报表",
            },
          ],
          default: "授权桌面报表",
        },
      ],
    };
    render(<Harness content={content} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(pickAndGrantReadonlyFolder).toHaveBeenCalledWith("conv-1", {
        wellKnown: "desktop",
        targetName: "报表",
      });
    });
    expect(screen.getByText("找不到该目录")).toBeTruthy();
  });

  it("not_found stays on card with failure copy — no resume / no bare grant", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(pickAndGrantReadonlyFolder).toHaveBeenCalled();
    });
    expect(onContinue).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    const bindFail = screen.getByText("找不到该目录");
    expect(bindFail.className).toContain("text-muted-foreground");
    expect(bindFail.className).not.toContain("destructive");
    // 卡仍在：主 CTA 仍可点
    expect(screen.getByRole("button", { name: /^提交$/ })).toBeTruthy();
  });

  it("resolve success resumes via onBindResolve with fulfilled answer", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async (_answer: string) => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "报表", alias: "报表" },
      alias: "报表",
      namespace: "external/报表",
      displayLabel: "报表",
    });

    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(onBindResolve).toHaveBeenCalled();
    });
    expect(onContinue).not.toHaveBeenCalled();
    const composed = onBindResolve.mock.calls[0]?.[0] ?? "";
    expect(composed).toContain("授权访问本机目录");
    expect(composed).toContain("报表");
    expect(composed).toContain("external/报表");
    expect(composed).toContain("只读");
  });

  it("resolve success clears bindBusy so CTA is not stuck when card stays mounted", async () => {
    // resume 成功但不卸载卡（例如父级尚未换阶段）——主 CTA 不得永久 busy
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "报表", alias: "报表" },
      alias: "报表",
      namespace: "external/报表",
    });

    render(<Harness onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    await waitFor(() => {
      expect(onBindResolve).toHaveBeenCalled();
    });
    await waitFor(() => {
      const submit = screen.getByRole("button", { name: /^提交$/ });
      expect((submit as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it("normal non-folder selection still uses onContinue", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    // 改选普通选项（会清掉预选 grant）
    fireEvent.click(screen.getByRole("button", { name: /继续用云端/ }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
  });

  it("option-row grant click fulfills without picker", async () => {
    const onBindResolve = vi.fn(async () => {});
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: true,
      root: { id: "r1", name: "资料", alias: "资料" },
      alias: "资料",
      namespace: "external/资料",
    });

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
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    await waitFor(() => {
      expect(pickAndGrantReadonlyFolder).toHaveBeenCalledWith(
        "conv-1",
        undefined,
      );
      expect(onBindResolve).toHaveBeenCalled();
    });
  });
});

describe("AskDecisionBody organize confirm card", () => {
  beforeEach(() => {
    pickAndGrantReadonlyFolder.mockReset();
    pickAndGrantOrganizeFolder.mockReset();
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
    context: "",
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

  it("shows 将整理 target and 允许整理 shell before allow", () => {
    render(<Harness content={organizeContent} />);
    expect(screen.getByText("将整理：桌面 › 咨询")).toBeTruthy();
    expect(screen.getByText(/整理确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^允许整理$/ })).toBeTruthy();
    // No picker framing
    expect(screen.queryByText(/选文件夹/)).toBeNull();
  });

  it("Continue fulfills organize via helper — no silent upgrade, no picker", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /^允许整理$/ }));

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

  it("其他… short affirm fulfills organize grant (not bare compose)", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /其他…/ }));
    fireEvent.change(screen.getByPlaceholderText("填写你的答案"), {
      target: { value: "可以" },
    });
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

  it("其他… ordinary text does not trigger organize grant", async () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});

    render(
      <Harness
        content={organizeContent}
        onContinue={onContinue}
        onBindResolve={onBindResolve}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /其他…/ }));
    fireEvent.change(screen.getByPlaceholderText("填写你的答案"), {
      target: { value: "先放一放，下周再说" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(pickAndGrantOrganizeFolder).not.toHaveBeenCalled();
    expect(onContinue).toHaveBeenCalledTimes(1);
    expect(onBindResolve).not.toHaveBeenCalled();
  });

  it("其他… short affirm failure stays on card via setBindError", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /其他…/ }));
    fireEvent.change(screen.getByPlaceholderText("填写你的答案"), {
      target: { value: "允许整理" },
    });
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

  it("does not paint model option second sentences; header stays", () => {
    const content: AskUserContent = {
      question: "用哪种格式？",
      context: "背景说明应保留",
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
    expect(screen.getByText("用哪种格式？")).toBeTruthy();
    expect(screen.getByText("背景说明应保留")).toBeTruthy();
    expect(screen.getByText("选一种")).toBeTruthy();
    expect(screen.getByText("Markdown")).toBeTruthy();
    expect(screen.getByText("PDF")).toBeTruthy();
    expect(screen.queryByText("一周内可验证")).toBeNull();
    expect(screen.queryByText("方便打印")).toBeNull();
  });
});

describe("AskDecisionBody Continue + grant on Web", () => {
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

  it("Continue with grant default shows download guide — no onContinue", () => {
    const onContinue = vi.fn();
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onContinue={onContinue} onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));

    expect(onContinue).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(window.open).toHaveBeenCalledWith(
      DESKTOP_DOWNLOAD_URL,
      "_blank",
      "noopener,noreferrer",
    );
    expect(screen.getByText(new RegExp(DESKTOP_REQUIRED_HINT))).toBeTruthy();
  });
});
