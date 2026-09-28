// @vitest-environment jsdom
/**
 * 拍板卡撞上「这条已经结了」的回执（云对话多端同权 B2 · 验收 5）。
 *
 * 另一端先答了、主管接管仲裁、或超时按假设兜底，都会让本端这一点扑空。此时按钮必须就此关掉
 * ——放回可点只会一点再点、次次 404；但也不能一直转圈，那帧 `escalation_resolved` 可能早就
 * 过去了，等不来。谁结的、结果如何，一律等带线材字段的收口帧，卡面不猜。
 */
import { EscalationCard } from "@/components/chat/EscalationCard";
import type { RunEscalation } from "@/stores/execution";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const decideEscalation = vi.fn();
const notifySubmitInteractionResult = vi.fn();

vi.mock("@/services/escalation", () => ({
  decideEscalation: (...args: unknown[]) => decideEscalation(...args),
}));

vi.mock("@/services/interactionSubmit", () => ({
  notifySubmitInteractionResult: (...args: unknown[]) =>
    notifySubmitInteractionResult(...args),
}));

vi.mock("@/components/ManualHelpLink", () => ({
  MANUAL_HELP: { control: "/help" },
  ManualHelpLink: () => null,
}));

function ownershipEsc(): RunEscalation {
  return {
    id: "esc-own",
    question: "写入冲突：`site/index.html` 已归队友负责",
    assumption: "等移交后再写",
    status: "pending",
    answer: null,
    kind: "wait",
    questions: [],
    ownershipPaths: ["site/index.html"],
    lockOwnerRunId: "assemble",
  };
}

function renderCard() {
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
}

describe("EscalationCard · 回执说已经结了", () => {
  beforeEach(() => {
    decideEscalation.mockReset().mockResolvedValue("already_settled");
    notifySubmitInteractionResult.mockReset();
  });

  it("按钮就此关掉，再点也不会二次提交", async () => {
    renderCard();
    const transfer = screen.getByRole("button", { name: "移交写权" });
    fireEvent.click(transfer);

    await waitFor(() => {
      expect(transfer).toHaveProperty("disabled", true);
    });
    fireEvent.click(screen.getByRole("button", { name: "保持原主" }));
    expect(decideEscalation).toHaveBeenCalledTimes(1);
  });

  it("如实提示，而不是报成提交失败", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "移交写权" }));

    await waitFor(() => {
      expect(notifySubmitInteractionResult).toHaveBeenCalledWith(
        "already_settled",
      );
    });
  });

  it("普通失败仍放回可点（这次没发出去，不是卡结了）", async () => {
    decideEscalation.mockResolvedValue("busy");
    renderCard();
    const transfer = screen.getByRole("button", { name: "移交写权" });
    fireEvent.click(transfer);

    await waitFor(() => {
      expect(notifySubmitInteractionResult).toHaveBeenCalledWith("busy");
    });
    expect(transfer).toHaveProperty("disabled", false);
  });
});
