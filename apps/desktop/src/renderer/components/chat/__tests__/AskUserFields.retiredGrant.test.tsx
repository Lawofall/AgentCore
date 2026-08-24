// @vitest-environment jsdom
/**
 * AskQuestionFields：旧 `grant_readonly_folder` 停履约（升级卡等共用内核）。
 */
import {
  AskQuestionFields,
  type AskUserContent,
  GRANT_READONLY_FOLDER_RETIRED,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
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
  conversationId,
  onBindResolve,
}: {
  conversationId?: string;
  onBindResolve?: (composed: string) => void | Promise<void>;
}) {
  const answer = useAskAnswer(grantContent);
  return (
    <AskQuestionFields
      content={grantContent}
      answer={answer}
      tone={interactiveCheckpointTone.neutral}
      disabled={false}
      conversationId={conversationId}
      onBindResolve={onBindResolve}
    />
  );
}

describe("AskQuestionFields retired grant_readonly_folder", () => {
  beforeEach(() => {
    pickAndGrantReadonlyFolder.mockReset();
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    window.fsApi = {
      grantSessionReadonlyRoot: vi.fn(),
    } as unknown as typeof window.fsApi;
    vi.spyOn(window, "open").mockReturnValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // biome-ignore lint/performance/noDelete: 测后清 stub
    delete (window as { fsApi?: unknown }).fsApi;
  });

  it("click is honest fail — no helper / no picker / no download", () => {
    const onBindResolve = vi.fn(async () => {});
    render(<Harness conversationId="conv-1" onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    expect(screen.getByText(GRANT_READONLY_FOLDER_RETIRED)).toBeTruthy();
  });

  it("escalation-like (no bind resolve) still fails honestly", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    expect(screen.getByText(GRANT_READONLY_FOLDER_RETIRED)).toBeTruthy();
  });
});
