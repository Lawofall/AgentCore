// @vitest-environment jsdom
/**
 * AskQuestionFields：已删的只读 Ask action 当普通选项（升级卡等共用内核）。
 */
import {
  AskQuestionFields,
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { interactiveCheckpointTone } from "@/components/ui/tone-presets";
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

describe("AskQuestionFields unknown deleted folder action", () => {
  beforeEach(() => {
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

  it("click is ordinary toggle — no grant helper / no download", () => {
    const onBindResolve = vi.fn(async () => {});
    render(<Harness conversationId="conv-1" onBindResolve={onBindResolve} />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(onBindResolve).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
  });

  it("escalation-like (no bind resolve) still toggles as ordinary", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /授权访问本机目录/ }));

    expect(window.fsApi?.grantSessionReadonlyRoot).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
  });
});
