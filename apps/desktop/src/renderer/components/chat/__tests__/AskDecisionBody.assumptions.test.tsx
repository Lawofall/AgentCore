// @vitest-environment jsdom
/**
 * 生产起步计划：项名 / 取值上下叠排，长 label 不得落进 w-16 / w-14 窄列。
 */
import { AskDecisionBody } from "@/components/chat/ask/AskDecisionBody";
import {
  type AskUserContent,
  useAskAnswer,
} from "@/components/chat/ask/AskUserFields";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { checkpoint: "/manual" },
  ManualHelpLink: () => null,
}));

const LONG_LABEL = "这次先做的范围和先不做的边界";

const content: AskUserContent = {
  question: "按这版起步可以吗？",
  assumptions: [
    { id: "a0", label: "范围", value: "先做首页" },
    { id: "a1", label: LONG_LABEL, value: "登录和支付先不做" },
  ],
  questions: [
    {
      id: "q0",
      prompt: "方向对吗？",
      kind: "choice",
      options: [{ label: "对，按这个做" }, { label: "先改方向" }],
      multiple: false,
      default: "对，按这个做",
    },
  ],
};

function Harness() {
  const answer = useAskAnswer(content);
  return (
    <AskDecisionBody
      content={content}
      answer={answer}
      busy={false}
      submitting={null}
      onContinue={() => {}}
      onStop={() => {}}
    />
  );
}

function expectStackedPair(label: string, value: string) {
  const dt = screen.getByText(label);
  const dd = screen.getByText(value);
  expect(dt.tagName).toBe("DT");
  expect(dd.tagName).toBe("DD");
  expect(dt.className).toMatch(/text-muted-foreground/);
  expect(dt.className).not.toMatch(/\bw-14\b/);
  expect(dt.className).not.toMatch(/\bw-16\b/);
  expect(dt.parentElement).toBe(dd.parentElement);
  expect(dt.parentElement?.className).not.toMatch(/\bflex\b/);
  expect(
    dt.compareDocumentPosition(dd) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
}

describe("AskDecisionBody 起步计划 stack", () => {
  afterEach(cleanup);

  it("stacks muted labels above values; long labels are not in a fixed narrow column", () => {
    render(<Harness />);

    expect(screen.getByText("起步计划")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /起步计划/ })).toBeNull();

    expectStackedPair("范围", "先做首页");
    expectStackedPair(LONG_LABEL, "登录和支付先不做");

    expect(screen.getByText("方向对吗？")).toBeTruthy();
    expect(screen.getByText("对，按这个做")).toBeTruthy();
    expect(screen.getByText("先改方向")).toBeTruthy();
  });
});
