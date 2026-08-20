// @vitest-environment jsdom
import { DisplayNameHint } from "@/components/messages/DisplayNameHint";
import { dismissDisplayNameHint } from "@/lib/displayNameHint";
import { useAuthStore } from "@/stores/auth";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/displayNameHint", () => ({
  isDisplayNameHintDismissed: () => false,
  dismissDisplayNameHint: vi.fn(),
}));

afterEach(() => {
  cleanup();
  useAuthStore.setState({
    status: "unauthenticated",
    user: null,
    sessionVerified: false,
    reason: null,
  });
});

describe("DisplayNameHint", () => {
  it("shows for a generated handle and dismisses", () => {
    useAuthStore.setState({
      status: "authenticated",
      user: {
        id: "u1",
        username: "user_a3f90d12",
        displayName: "user_a3f90d12",
        email: "a@example.com",
        emailVerifiedAt: "2026-08-19T00:00:00Z",
        role: "user",
        avatarUrl: null,
      },
      sessionVerified: true,
      reason: null,
    });
    render(
      <MemoryRouter>
        <DisplayNameHint />
      </MemoryRouter>,
    );
    expect(screen.getByText(/系统分配的找人码/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(dismissDisplayNameHint).toHaveBeenCalledWith("u1");
    expect(screen.queryByText(/系统分配的找人码/)).toBeNull();
  });

  it("stays hidden when the nickname is custom", () => {
    useAuthStore.setState({
      status: "authenticated",
      user: {
        id: "u1",
        username: "user_a3f90d12",
        displayName: "Alice",
        email: "a@example.com",
        emailVerifiedAt: null,
        role: "user",
        avatarUrl: null,
      },
      sessionVerified: true,
      reason: null,
    });
    render(
      <MemoryRouter>
        <DisplayNameHint />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/系统分配的找人码/)).toBeNull();
  });
});
