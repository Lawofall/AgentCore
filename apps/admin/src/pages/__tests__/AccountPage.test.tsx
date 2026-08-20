// @vitest-environment jsdom
/**
 * AccountPage tests — pin the two blocks as real `<form>`s.
 *
 * Both sections used to be a `<div>` with an onClick button: Enter did nothing and
 * password managers had no form to attach a saved credential to. These assert the
 * submit path (Enter / implicit submission), the guard against submitting an
 * incomplete form, and the autocomplete wiring managers rely on.
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { AccountPage } from "@/pages/AccountPage";
import { changePassword, updateProfile } from "@/services/auth";
import { type AuthUser, useAuthStore } from "@/stores/auth";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/auth", () => ({
  changePassword: vi.fn(),
  updateProfile: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const SELF: AuthUser = {
  id: "self-id",
  username: "admin",
  displayName: "管理员",
  email: null,
  emailVerifiedAt: null,
  role: "admin",
  passwordMustChange: false,
};

beforeEach(() => {
  useAuthStore.setState({ status: "authenticated", user: SELF });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** The `<form>` owning a field — what Enter would submit. */
function formOf(field: HTMLElement): HTMLFormElement {
  const form = field.closest("form");
  if (!form) throw new Error("field is not inside a <form>");
  return form;
}

describe("AccountPage 个人资料", () => {
  it("提交表单（回车）即保存资料，不必点按钮", async () => {
    vi.mocked(updateProfile).mockResolvedValue({ ...SELF, displayName: "新名字" });
    render(<AccountPage />);

    const nameField = screen.getByLabelText("显示名");
    fireEvent.change(nameField, { target: { value: "新名字" } });
    fireEvent.submit(formOf(nameField));

    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith({
        displayName: "新名字",
        email: null,
      }),
    );
  });

  it("未改动时提交不发请求", () => {
    render(<AccountPage />);
    fireEvent.submit(formOf(screen.getByLabelText("显示名")));
    expect(updateProfile).not.toHaveBeenCalled();
  });

  it("显示邮箱未验证态，不提供补验入口", () => {
    render(<AccountPage />);
    expect(screen.getByText("邮箱 · 未填写")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发送验证码" })).toBeNull();
  });

  it("改邮箱后显示未验证，不自动发码", async () => {
    vi.mocked(updateProfile).mockResolvedValue({
      ...SELF,
      email: "new@example.com",
      emailVerifiedAt: null,
    });
    useAuthStore.setState({
      status: "authenticated",
      user: {
        ...SELF,
        email: "old@example.com",
        emailVerifiedAt: "2026-08-19T00:00:00Z",
      },
    });
    render(<AccountPage />);
    expect(screen.getByText("邮箱 · 已验证")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("邮箱 · 已验证"), {
      target: { value: "new@example.com" },
    });
    fireEvent.submit(formOf(screen.getByLabelText("显示名")));

    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith({
        displayName: "管理员",
        email: "new@example.com",
      }),
    );
    expect(screen.getByText("邮箱 · 未验证")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发送验证码" })).toBeNull();
  });
});

describe("AccountPage 修改密码", () => {
  function fillPassword(current: string, next: string, confirm: string) {
    fireEvent.change(screen.getByLabelText("当前密码"), {
      target: { value: current },
    });
    fireEvent.change(screen.getByLabelText("新密码（至少 8 位）"), {
      target: { value: next },
    });
    fireEvent.change(screen.getByLabelText("确认新密码"), {
      target: { value: confirm },
    });
  }

  it("提交表单（回车）即修改密码", async () => {
    vi.mocked(changePassword).mockResolvedValue(undefined);
    render(<AccountPage />);

    fillPassword("oldpass123", "newpass123", "newpass123");
    fireEvent.submit(formOf(screen.getByLabelText("当前密码")));

    await waitFor(() =>
      expect(changePassword).toHaveBeenCalledWith("oldpass123", "newpass123"),
    );
  });

  it("两次新密码不一致时提交被拦下并给出原因", () => {
    render(<AccountPage />);

    fillPassword("oldpass123", "newpass123", "newpass124");
    fireEvent.submit(formOf(screen.getByLabelText("当前密码")));

    expect(changePassword).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("不一致");
  });

  it("带上密码管理器需要的 autocomplete（含账号字段）", () => {
    render(<AccountPage />);

    expect(
      screen.getByLabelText("当前密码").getAttribute("autocomplete"),
    ).toBe("current-password");
    expect(
      screen.getByLabelText("新密码（至少 8 位）").getAttribute("autocomplete"),
    ).toBe("new-password");
    expect(
      screen.getByLabelText("确认新密码").getAttribute("autocomplete"),
    ).toBe("new-password");

    // The password form carries the account it belongs to, or a manager can't
    // match the new credential to an existing entry.
    const passwordForm = formOf(screen.getByLabelText("当前密码"));
    const account = passwordForm.querySelector<HTMLInputElement>(
      'input[autocomplete="username"]',
    );
    expect(account?.value).toBe("admin");
  });
});
