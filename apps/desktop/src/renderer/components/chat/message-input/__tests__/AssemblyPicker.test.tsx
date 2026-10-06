// @vitest-environment jsdom
/**
 * 输入区装配菜单是一列名字：配方在前，离开配方的在后。
 * 还折在配方上的那一行不另画；「默认」留在那一行上。
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
      row({ id: "sys-chat", name: "极简", kind: "system", recipe: "chat" }),
      row({ id: "sys-web", name: "轻量", kind: "system", recipe: "web" }),
      row({ id: "sys-full", name: "完整", kind: "system", recipe: "full" }),
      row({
        id: "owned-chat",
        name: "极简",
        kind: "user",
        recipe: "chat",
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

  it("一列名字，折进配方的不另画，默认留在配方行上", () => {
    renderPicker();
    expect(screen.getByRole("button", { name: "装配：极简" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /预置/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "装配：极简" }));

    const manage = screen.getByRole("button", { name: "管理装配…" });
    const list = manage.previousElementSibling;
    expect(list).toBeTruthy();
    const names = [...(list?.querySelectorAll("button") ?? [])].map(
      (button) => button.textContent ?? "",
    );
    expect(names.map((name) => name.replace("默认", ""))).toEqual([
      "极简",
      "轻量",
      "完整",
      "官方DS",
    ]);
    expect(names[0]).toContain("默认");
    expect(list?.textContent).not.toContain("预置");
    expect(list?.textContent).not.toContain("我的");
    expect(list?.querySelector('[aria-current="true"]')?.textContent).toContain(
      "极简",
    );
  });
});
