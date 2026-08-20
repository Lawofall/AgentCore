// @vitest-environment jsdom
/**
 * B4：本机选择器失败须出固定结构化卡，禁静默/空转。
 * 本机传统：open_local_project 走 pickAndOpenLocalFolder，不 toast 改导 Composer。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import type { AskUserContent } from "@/components/chat/ask/AskUserFields";
import { useAskAnswer } from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pickAndOpenLocalFolder = vi.fn();
const pickAndBindLocalFolder = vi.fn();

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => true),
}));

vi.mock("@/lib/openLocalFolder", () => ({
  pickAndOpenLocalFolder: (...args: unknown[]) =>
    pickAndOpenLocalFolder(...args),
  formatOpenLocalFolderAnswer: (label: string, name: string) =>
    `${label}（${name}）`,
}));

vi.mock("@/lib/bindLocalFolder", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/bindLocalFolder")>();
  return {
    ...actual,
    pickAndBindLocalFolder: (...args: unknown[]) =>
      pickAndBindLocalFolder(...args),
  };
});

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

const openLocalContent: AskUserContent = {
  question: "在哪打开？",
  assumptions: [],
  questions: [
    {
      id: "q0",
      prompt: "工作区",
      kind: "choice",
      options: [
        { label: "打开本地项目", action: "open_local_project" },
        { label: "继续用云端" },
      ],
      multiple: false,
      default: "",
    },
  ],
};

function Harness() {
  const answer = useAskAnswer(openLocalContent);
  return (
    <AskDecisionBody
      content={openLocalContent}
      answer={answer}
      busy={false}
      submitting={null}
      onContinue={() => {}}
      onStop={() => {}}
      conversationId="conv-1"
      onBindResolve={async () => {}}
    />
  );
}

describe("AskDecisionBody local picker failure card", () => {
  beforeEach(() => {
    pickAndOpenLocalFolder.mockReset();
    pickAndBindLocalFolder.mockReset();
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    window.fsApi = {} as unknown as typeof window.fsApi;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // biome-ignore lint/performance/noDelete: 测后清 stub
    delete (window as { fsApi?: unknown }).fsApi;
  });

  it("shows structured card for dialog_failed (未弹选择器)", async () => {
    pickAndOpenLocalFolder.mockResolvedValue({
      ok: false,
      reason: "dialog_failed",
      message: "系统未能打开文件夹选择器",
    });

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /打开本地项目/ }));

    await waitFor(() => {
      expect(screen.getByTestId("local-picker-failure-card")).toBeTruthy();
    });
    const card = screen.getByTestId("local-picker-failure-card");
    expect(card.getAttribute("data-failure-kind")).toBe("dialog_failed");
    expect(card.textContent).toContain("未弹出文件夹选择器");
    expect(card.className).toContain("bg-muted/40");
    expect(card.className).not.toContain("destructive");
  });

  it("shows structured card for unauthorized", async () => {
    pickAndOpenLocalFolder.mockResolvedValue({
      ok: false,
      reason: "unauthorized",
      message: "所选路径无法访问",
    });

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /打开本地项目/ }));

    await waitFor(() => {
      expect(screen.getByTestId("local-picker-failure-card")).toBeTruthy();
    });
    expect(
      screen
        .getByTestId("local-picker-failure-card")
        .getAttribute("data-failure-kind"),
    ).toBe("unauthorized");
    expect(
      screen.getByTestId("local-picker-failure-card").textContent,
    ).toContain("未能授权本机目录");
  });

  it("cancelled stays silent — no failure card", async () => {
    pickAndOpenLocalFolder.mockResolvedValue({
      ok: false,
      reason: "cancelled",
    });

    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /打开本地项目/ }));

    await waitFor(() => {
      expect(pickAndOpenLocalFolder).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("local-picker-failure-card")).toBeNull();
  });
});
