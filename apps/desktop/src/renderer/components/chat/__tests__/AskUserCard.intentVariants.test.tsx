// @vitest-environment jsdom
/**
 * ask_user list-confirm chrome: organize_plan / daily_review keep the checklist
 * body (second line, seed-all, side-effect CTA). Caption is the shared 需要你拍板.
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
  assumptions: [],
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

const dailyReviewContent: AskUserContent = {
  question: "确认要落盘的项？\n来自今日对话摘要。",
  assumptions: [],
  questions: [
    {
      id: "q0",
      prompt: "勾选要写入的项",
      kind: "choice",
      multiple: true,
      default: "",
      options: [
        {
          label: "偏好简洁回复",
          review_kind: "preference",
          body: "用户偏好短句答复",
        },
        {
          label: "主题：周报节奏",
          review_kind: "topic",
          body: "每周五整理周报",
        },
        {
          label: "规则：先问再改文件",
          review_kind: "rule",
          body: "改文件前先征得确认",
        },
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
  it("organize_plan 默认全选、第二行总览、副作用 CTA；caption 为需要你拍板", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderCard("organize_plan", organizeContent, onSubmit);

    expect(
      document.querySelector('[data-ask-intent="organize_plan"]'),
    ).toBeTruthy();
    expect(
      document.querySelector('[data-ask-card="organize_plan"]'),
    ).toBeTruthy();
    expect(screen.getByText("需要你拍板")).toBeTruthy();
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

  it("daily_review 默认全选，取消勾选后提交带 selected；caption 为需要你拍板", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderCard("daily_review", dailyReviewContent, onSubmit);

    expect(
      document.querySelector('[data-ask-intent="daily_review"]'),
    ).toBeTruthy();
    expect(
      document.querySelector('[data-ask-card="daily_review"]'),
    ).toBeTruthy();
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.queryByText(/复盘提案/)).toBeNull();
    expect(screen.getByText("偏好简洁回复")).toBeTruthy();
    expect(screen.getByText(/偏好 · 用户偏好短句答复/)).toBeTruthy();
    expect(
      screen.getByText(/确认后服务端直接写入记忆\/规则\/文档/),
    ).toBeTruthy();

    fireEvent.click(screen.getByText("主题：周报节奏"));
    fireEvent.click(screen.getByRole("button", { name: /确认落盘/ }));

    expect(onSubmit).toHaveBeenCalledWith("continue", "", [
      "偏好简洁回复",
      "规则：先问再改文件",
    ]);
  });

  it("collectAskSelected 扁平化多题 picks", () => {
    expect(
      collectAskSelected(organizeContent, { q0: ["新建 Archive"] }),
    ).toEqual(["新建 Archive"]);
  });
});
