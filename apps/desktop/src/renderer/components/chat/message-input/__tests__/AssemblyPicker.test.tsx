// @vitest-environment jsdom
/**
 * 输入区装配菜单：标准、完整、极简、自建。官方三行各一行说明。不标「默认」。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { useComposerProfileDraftStore } from "@/lib/composerModelProfile";
import type { LlmModelProfileView } from "@/services/llmModelProfiles";
import { useConversationStore } from "@/stores/conversation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssemblyPicker } from "../AssemblyPicker";

const conversations = vi.hoisted(() => ({
  rows: [] as { id: string; assemblyId: string | null }[],
}));

const profiles = vi.hoisted(() => ({
  data: [] as LlmModelProfileView[],
  defaultId: "owned-chat",
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => conversations.rows,
  patchConversationCache: vi.fn(),
}));

vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: () => ({
    data: {
      default_assembly_id: profiles.defaultId,
      data: profiles.data,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/services/llmModelProfiles", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/llmModelProfiles")>();
  return {
    ...actual,
    rememberAssemblySelection: vi.fn(async () => ({
      id: "owned-chat",
      is_default: true,
    })),
  };
});

function row(
  partial: Pick<LlmModelProfileView, "id" | "name" | "kind"> &
    Partial<LlmModelProfileView>,
): LlmModelProfileView {
  return { is_default: false, ...partial } as LlmModelProfileView;
}

function renderPicker() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <AssemblyPicker />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AssemblyPicker", () => {
  beforeEach(() => {
    profiles.data = [
      row({
        id: "sys-standard",
        name: "标准",
        kind: "system",
        recipe: "standard",
      }),
      row({ id: "sys-full", name: "完整", kind: "system", recipe: "full" }),
      row({ id: "sys-chat", name: "极简", kind: "system", recipe: "chat" }),
      row({
        id: "owned-chat",
        name: "写稿",
        kind: "user",
        recipe: null,
        is_default: true,
      }),
      row({ id: "owned-ds", name: "官方DS", kind: "user", recipe: null }),
    ];
    profiles.defaultId = "owned-chat";
    conversations.rows = [{ id: "c1", assemblyId: "owned-chat" }];
    useConversationStore.setState({
      currentConversationId: "c1",
    } as never);
    useComposerProfileDraftStore.setState({
      profileId: null,
      assemblyId: null,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("标准完整极简在前，官方带一行说明，不标默认", () => {
    renderPicker();
    expect(screen.getByRole("button", { name: "能力：写稿" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /预置/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "能力：写稿" }));

    const manage = screen.getByRole("button", { name: "管理能力…" });
    const list = manage.previousElementSibling;
    expect(list).toBeTruthy();
    const names = [...(list?.querySelectorAll("button") ?? [])].map(
      (button) => button.getAttribute("aria-label") ?? "",
    );
    expect(names).toEqual(["标准", "完整", "极简", "写稿", "官方DS"]);
    expect(list?.textContent).toContain("能改文件、跑命令、上网。");
    expect(list?.textContent).toContain("含辩论、搜旧对话。");
    expect(list?.textContent).toContain("这场只聊天。");
    expect(list?.textContent).not.toContain("默认");
    expect(list?.textContent).not.toContain("预置");
    expect(list?.textContent).not.toContain("我的");
    expect(list?.querySelector('[aria-current="true"]')?.textContent).toContain(
      "写稿",
    );
  });
});
