// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const revokeMutate = vi.fn();

vi.mock("@/hooks/useExternalGrants", () => ({
  useExternalGrants: vi.fn(),
  useRevokeExternalGrant: vi.fn(() => ({
    mutate: revokeMutate,
    isPending: false,
  })),
}));

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => true,
}));

vi.mock("@/lib/grantOrganizeFolder", () => ({
  pickAndGrantConversationFolder: vi.fn(),
}));

import { useExternalGrants } from "@/hooks/useExternalGrants";
import { ExternalMountsSection } from "../ExternalMountsSection";

const useExternalGrantsMock = vi.mocked(useExternalGrants);

function mockQuery(
  data: unknown,
  over: Partial<{ isLoading: boolean; isError: boolean }> = {},
) {
  return {
    data,
    isLoading: over.isLoading ?? false,
    isError: over.isError ?? false,
    refetch: vi.fn(),
  };
}

const SAMPLE = [
  {
    root_id: "root-1",
    alias: "bills",
    label: "对账单",
    namespace: ".",
    mode: "attach_rw" as const,
    working_root: true,
  },
  {
    root_id: "root-2",
    alias: "desk",
    label: "桌面",
    namespace: "external/desk",
    mode: "readonly" as const,
    working_root: false,
  },
];

beforeEach(() => {
  revokeMutate.mockReset();
  useExternalGrantsMock.mockReturnValue(mockQuery([]) as never);
});

afterEach(() => {
  cleanup();
});

describe("ExternalMountsSection", () => {
  it("shows add-folder when the grant list is empty on desktop", () => {
    render(<ExternalMountsSection conversationId="conv-1" />);
    expect(screen.getByText("正在用的文件夹")).toBeTruthy();
    expect(screen.getByText(/还没有授权文件夹/)).toBeTruthy();
  });

  it("lists working root vs extra without exposing external/", () => {
    useExternalGrantsMock.mockReturnValue(mockQuery(SAMPLE) as never);
    render(<ExternalMountsSection conversationId="conv-1" />);

    expect(screen.getByText("正在用的文件夹")).toBeTruthy();
    expect(screen.getByText("对账单")).toBeTruthy();
    expect(screen.getByText("桌面")).toBeTruthy();
    expect(screen.getByText(/正在用 · 可改/)).toBeTruthy();
    expect(screen.getByText(/额外 · 只读/)).toBeTruthy();
    expect(screen.queryByText(/external\//)).toBeNull();
    expect(screen.queryByText(/^[A-Za-z]:\\/)).toBeNull();
  });

  it("calls revoke with root_id when 撤销 is clicked", () => {
    useExternalGrantsMock.mockReturnValue(mockQuery(SAMPLE) as never);
    render(<ExternalMountsSection conversationId="conv-1" />);

    const buttons = screen.getAllByRole("button", { name: "撤销" });
    expect(buttons).toHaveLength(2);
    const first = buttons[0];
    expect(first).toBeTruthy();
    fireEvent.click(first);

    expect(revokeMutate).toHaveBeenCalledWith(
      "root-1",
      expect.objectContaining({
        onError: expect.any(Function),
      }),
    );
  });

  it("shows retry affordance on load error", () => {
    useExternalGrantsMock.mockReturnValue(
      mockQuery(undefined, { isError: true }) as never,
    );
    render(<ExternalMountsSection conversationId="conv-1" />);
    const line = screen.getByText(/无法加载已授权文件夹/);
    expect(line.className).toContain("text-muted-foreground");
    expect(line.className).not.toContain("destructive");
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});
