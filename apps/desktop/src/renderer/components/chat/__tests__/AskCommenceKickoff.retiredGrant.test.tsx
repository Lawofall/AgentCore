// @vitest-environment jsdom
/**
 * 预览 Kickoff：已删的只读 Ask action 当普通选项（有会话绑定时也不调 helper）。
 */
import { AskCommenceKickoffBody } from "@/components/chat/ask/AskCommenceKickoff";
import {
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
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

const staleReadonlyOption = {
  label: "授权访问本机目录",
  action: "grant_readonly_folder",
} as unknown as AskOption;

const grantContent: AskUserContent = {
  question: "需要本机目录吗？",
  assumptions: [],
  questions: [
    {
      id: "q0",
      prompt: "授权",
      kind: "choice",
      options: [staleReadonlyOption, { label: "继续用云端" }],
      multiple: false,
      default: "",
    },
  ],
};

function Harness({
  onBindResolve = vi.fn(async () => {}),
}: {
  onBindResolve?: (composed: string) => void | Promise<void>;
}) {
  const answer = useAskAnswer(grantContent);
  return (
    <AskCommenceKickoffBody
      content={grantContent}
      answer={answer}
      busy={false}
      submitting={null}
      onContinue={() => {}}
      onStop={() => {}}
      conversationId="conv-1"
      onBindResolve={onBindResolve}
    />
  );
}

describe("AskCommenceKickoffBody unknown deleted folder action", () => {
  beforeEach(() => {
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
    } as unknown as typeof window.fsApi;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // biome-ignore lint/performance/noDelete: 测后清 stub
    delete (window as { fsApi?: unknown }).fsApi;
  });

  it("option click is ordinary — no grant helper", () => {
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
  });
});
