// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/workflows", () => ({
  createWorkflowFromPlaybook: vi.fn(),
}));

import { ApiError } from "@/services/api";
import type {
  WorkflowTemplate,
  WorkflowTemplateSlot,
} from "@/services/workflows";
import { createWorkflowFromPlaybook } from "@/services/workflows";
import { MemoryRouter } from "react-router-dom";
import { UseTemplateDialog } from "../UseTemplateDialog";

function slot(over: Partial<WorkflowTemplateSlot> = {}): WorkflowTemplateSlot {
  return {
    key: "topic",
    label: "主题",
    required: true,
    hint: null,
    choices: [],
    ...over,
  };
}

function renderDialog(slots: WorkflowTemplateSlot[]) {
  const template: WorkflowTemplate = {
    id: "cite_write_review",
    title: "调研报告成文",
    summary: "",
    slots,
  };
  render(
    <MemoryRouter>
      <UseTemplateDialog open template={template} onClose={() => {}} />
    </MemoryRouter>,
  );
}

function submitDisabled(): boolean {
  return screen
    .getByRole("button", { name: "复制为我的" })
    .hasAttribute("disabled");
}

afterEach(() => {
  cleanup();
});

describe("UseTemplateDialog slots", () => {
  it("blocks submit on empty required slots only", () => {
    renderDialog([slot()]);
    expect(submitDisabled()).toBe(true);
    expect(screen.getByText(/还需填写：主题/)).toBeTruthy();
  });

  it("lets an untouched optional slot through", () => {
    renderDialog([slot({ key: "style", label: "气质", required: false })]);
    expect(submitDisabled()).toBe(false);
    expect(screen.getByText(/气质（可选）/)).toBeTruthy();
  });

  it("renders an enumerated slot as a picker of its allowed values", () => {
    renderDialog([
      slot({
        key: "style",
        label: "气质",
        required: false,
        hint: "默认营销落地页",
        choices: [
          { value: "marketing", label: "marketing（营销落地页）" },
          { value: "toolshed", label: "toolshed（控制台 dense）" },
        ],
      }),
    ]);

    const picker = screen.getByLabelText(/气质/);
    expect(picker.tagName).toBe("SELECT");
    expect(
      screen.getByRole("option", { name: "marketing（营销落地页）" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("option", { name: "toolshed（控制台 dense）" }),
    ).toBeTruthy();
    // 可选枚举必须能「不指定」，让后端用模板默认值。
    expect(screen.getByRole("option", { name: /不指定/ })).toBeTruthy();
    expect(screen.getByText("默认营销落地页")).toBeTruthy();
  });

  it("shows a recoverable copy failure as muted inline text", async () => {
    vi.mocked(createWorkflowFromPlaybook).mockRejectedValue(
      new ApiError(500, JSON.stringify({ error: { message: "复制开小差" } })),
    );
    renderDialog([slot()]);
    fireEvent.change(screen.getByLabelText(/主题/), {
      target: { value: "竞品" },
    });
    fireEvent.click(screen.getByRole("button", { name: "复制为我的" }));

    const err = await screen.findByText("复制开小差");
    expect(err.className).toContain("text-muted-foreground");
    expect(err.className).not.toContain("destructive");
  });
});
