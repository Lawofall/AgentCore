// @vitest-environment jsdom
/**
 * 写权冲突 escalate：结构化「移交写权 / 保持原主」。
 */
import { EscalationCard } from "@/components/chat/EscalationCard";
import type { RunEscalation } from "@/stores/execution";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const decideEscalation = vi.fn();

vi.mock("@/services/escalation", () => ({
  decideEscalation: (...args: unknown[]) => decideEscalation(...args),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { control: "/help" },
  ManualHelpLink: () => null,
}));

function ownershipEsc(overrides: Partial<RunEscalation> = {}): RunEscalation {
  return {
    id: "esc-own",
    question: "写入冲突：`site/index.html` 已归队友负责",
    assumption: "等移交后再写",
    status: "pending",
    answer: null,
    kind: "wait",
    questions: [],
    ownershipPaths: ["site/index.html", "site/styles.css"],
    lockOwnerRunId: "assemble",
    ...overrides,
  };
}

describe("EscalationCard ownership", () => {
  beforeEach(() => {
    decideEscalation.mockReset().mockResolvedValue("ok");
  });

  it("renders transfer / keep actions and POSTs transfer_ownership", async () => {
    render(
      <EscalationCard
        {...{
          escalation: ownershipEsc(),
          role: "工程师",
          conversationId: "conv-1",
          interactive: true as const,
        }}
      />,
    );
    expect(screen.getByText(/文件写权冲突/)).toBeTruthy();
    expect(screen.getByText("site/index.html")).toBeTruthy();
    expect(
      screen.getByText(/不移交写权时将按此继续（目标路径修订会落空）/),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "按假设继续（不移交）" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "移交写权" }));
    await waitFor(() => {
      expect(decideEscalation).toHaveBeenCalledWith("conv-1", "esc-own", {
        kind: "transfer_ownership",
      });
    });
  });

  it("keep_ownership posts without transfer flag", async () => {
    render(
      <EscalationCard
        {...{
          escalation: ownershipEsc(),
          role: "工程师",
          conversationId: "conv-1",
          interactive: true as const,
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "保持原主" }));
    await waitFor(() => {
      expect(decideEscalation).toHaveBeenCalledWith("conv-1", "esc-own", {
        kind: "keep_ownership",
      });
    });
  });

  it("use_assumption button clarifies no transfer", async () => {
    render(
      <EscalationCard
        {...{
          escalation: ownershipEsc(),
          role: "工程师",
          conversationId: "conv-1",
          interactive: true as const,
        }}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "按假设继续（不移交）" }),
    );
    await waitFor(() => {
      expect(decideEscalation).toHaveBeenCalledWith("conv-1", "esc-own", {
        kind: "use_assumption",
      });
    });
  });
});
