// @vitest-environment jsdom
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import type { AskUserContent } from "@/components/chat/ask/AskUserFields";
import { useAskAnswer } from "@/components/chat/ask/AskUserFields";
import {
  DESKTOP_DOWNLOAD_URL,
  DESKTOP_REQUIRED_HINT,
} from "@/lib/desktopDownload";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => false),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

const grantContent: AskUserContent = {
  question: "需要本机目录吗？",
  assumptions: [],
  questions: [
    {
      id: "q0",
      prompt: "授权",
      kind: "choice",
      options: [
        { label: "授权本机目录", action: "grant_readonly_folder" },
        { label: "继续用云端" },
      ],
      multiple: false,
      default: "",
    },
  ],
};

function Harness() {
  const answer = useAskAnswer(grantContent);
  return (
    <AskDecisionBody
      content={grantContent}
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

describe("AskDecisionBody web grant actions", () => {
  beforeEach(() => {
    window.__WEB__ = true;
    vi.spyOn(window, "open").mockReturnValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.__WEB__ = undefined;
  });

  it("does not toggleChoice on grant; shows desktop download guide", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /授权本机目录/ }));
    expect(window.open).toHaveBeenCalledWith(
      DESKTOP_DOWNLOAD_URL,
      "_blank",
      "noopener,noreferrer",
    );
    expect(screen.getByText(new RegExp(DESKTOP_REQUIRED_HINT))).toBeTruthy();
    expect(
      screen.getByText(/https:\/\/fashitianxia\.xyz\/download/),
    ).toBeTruthy();
    // 未把 grant 选项写入答案选中态（假确认）
    const grantBtn = screen.getByRole("button", { name: /授权本机目录/ });
    expect(grantBtn.getAttribute("aria-pressed")).not.toBe("true");
  });

  it("still allows normal non-folder choice without opening download", () => {
    render(<Harness />);
    const cloudBtn = screen.getByRole("button", { name: /继续用云端/ });
    fireEvent.click(cloudBtn);
    expect(window.open).not.toHaveBeenCalled();
    // 回归：普通选项选中态不得被 canRunFolder 条件误伤
    expect(cloudBtn.getAttribute("aria-pressed")).toBe("true");
  });
});
