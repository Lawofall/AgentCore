// @vitest-environment jsdom
/**
 * ask_user list-confirm chrome: organize_plan keeps the checklist
 * body (second line, seed-all, side-effect CTA). Caption is sr-only 需要你拍板.
 * daily_review chrome is absent.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AskUiIntent } from "@/lib/checkpointIntent";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskUserCard, collectAskSelected } from "../CheckpointCard";
import type { AskUserContent } from "../ask/AskUserFields";

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

afterEach(cleanup);

const organizeContent: AskUserContent = {
  question: "确认要执行的整理项？",
  questions: [
    {
      id: "q0",
      prompt: "勾选要执行的项",
      kind: "choice",
      multiple: true,
      default: "",
      options: [
        { label: "新建 Archive", op: "mkdir", path: "Archive" },
        {
          label: "移动报告",
          op: "move",
          source: "a.pdf",
          destination: "Archive/a.pdf",
        },
        { label: "删除草稿", op: "delete", path: "draft.md" },
      ],
    },
  ],
};

function renderCard(
  intent: AskUiIntent,
  content: AskUserContent,
  onSubmit = vi.fn(),
) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AskUserCard content={content} intent={intent} onSubmit={onSubmit} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe("AskUserCard intent variants", () => {
  it("organize_plan 默认全选、第二行总览、副作用 CTA；批次标题在卡头", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderCard("organize_plan", organizeContent, onSubmit);

    expect(
      document.querySelector('[data-ask-intent="organize_plan"]'),
    ).toBeTruthy();
    expect(
      document.querySelector('[data-ask-card="organize_plan"]'),
    ).toBeTruthy();
    expect(screen.getByText("需要你拍板").classList.contains("sr-only")).toBe(
      true,
    );
    expect(screen.getByText("确认要执行的整理项？")).toBeTruthy();
    expect(screen.queryByText(/整理方案/)).toBeNull();
    expect(
      screen.getByText(
        "总览：新建 1 个文件夹、移动 1 个文件、删除 1 项（进回收站）",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/确认后按方案批量执行，不再二次弹审批/),
    ).toBeTruthy();

    fireEvent.click(screen.getByText("删除草稿"));
    fireEvent.click(screen.getByRole("button", { name: /确认并整理/ }));

    expect(onSubmit).toHaveBeenCalledWith("continue", "", [
      "新建 Archive",
      "移动报告",
    ]);
  });

  it("次要 CTA 文案为取消，点击仍发 decision=stop", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderCard("organize_plan", organizeContent, onSubmit);

    expect(screen.queryByText("停止")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onSubmit).toHaveBeenCalledWith("stop", "", []);
  });

  it("daily_review chrome is absent — wire falls back to decision", () => {
    renderCard("daily_review" as AskUiIntent, organizeContent);

    expect(
      document.querySelector('[data-ask-intent="daily_review"]'),
    ).toBeNull();
    expect(document.querySelector('[data-ask-card="daily_review"]')).toBeNull();
    expect(document.querySelector('[data-ask-intent="decision"]')).toBeTruthy();
    expect(screen.queryByRole("button", { name: /确认落盘/ })).toBeNull();
  });

  it("collectAskSelected 扁平化多题 picks", () => {
    expect(
      collectAskSelected(organizeContent, { q0: ["新建 Archive"] }),
    ).toEqual(["新建 Archive"]);
  });
});
