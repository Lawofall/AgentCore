// @vitest-environment jsdom
/**
 * 预览 Kickoff：旧 `grant_readonly_folder` 停履约（有会话绑定时也不调 helper）。
 */
import { AskCommenceKickoffBody } from "@/components/chat/ask/AskCommenceKickoff";
import {
  type AskUserContent,
  GRANT_READONLY_FOLDER_RETIRED,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { hasLocalFiles } from "@/lib/capabilities";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pickAndGrantReadonlyFolder = vi.fn();

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
        { label: "授权访问本机目录", action: "grant_readonly_folder" },
        { label: "继续用云端" },
      ],
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

describe("AskCommenceKickoffBody retired grant_readonly_folder", () => {
  beforeEach(() => {
    pickAndGrantReadonlyFolder.mockReset();
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

  it("option click is honest fail — no helper / no grant", () => {
    const onBindResolve = vi.fn(async () => {});
    render(<Harness onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(screen.getByText(GRANT_READONLY_FOLDER_RETIRED)).toBeTruthy();
  });
});
