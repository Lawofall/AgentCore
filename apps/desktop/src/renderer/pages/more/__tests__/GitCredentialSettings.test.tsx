// @vitest-environment jsdom
import {
  type GitCredentialView,
  deleteGitCredentials,
  getGitCredentials,
  upsertGitCredentials,
} from "@/services/gitCredentials";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GitCredentialSettings } from "../GitCredentialSettings";

vi.mock("@/services/gitCredentials", () => ({
  getGitCredentials: vi.fn(),
  upsertGitCredentials: vi.fn(),
  deleteGitCredentials: vi.fn(),
}));

const getMock = vi.mocked(getGitCredentials);
const upsertMock = vi.mocked(upsertGitCredentials);
const deleteMock = vi.mocked(deleteGitCredentials);

function view(over: Partial<GitCredentialView> = {}): GitCredentialView {
  return { configured: false, ...over };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <GitCredentialSettings />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GitCredentialSettings", () => {
  it("leads with the token form and no manuals", async () => {
    getMock.mockResolvedValue(view());
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("尚未配置")).toBeTruthy();
    });
    expect(
      screen.getByText(
        "云端私有仓库用账户 Token。公网仓不用配。GitHub 勾选 repo 权限即可。",
      ),
    ).toBeTruthy();
    expect(screen.getByLabelText("Token")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "保存凭据" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    expect(screen.queryByText("高级")).toBeNull();
    expect(screen.queryByLabelText("用户名（可选）")).toBeNull();
    expect(screen.queryByText("本机仓库")).toBeNull();
    expect(screen.queryByText(/工具永不收密码/)).toBeNull();
    expect(screen.queryByText(/浅克隆/)).toBeNull();
    expect(screen.queryByText(/x-access-token/)).toBeNull();
    expect(screen.queryByRole("link", { name: "文件" })).toBeNull();
  });

  it("shows a compact configured status and save/clear actions", async () => {
    getMock.mockResolvedValue(
      view({
        configured: true,
        masked_token: "ghp_****abcd",
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText("已配置 · ghp_****abcd · 已加密保存"),
      ).toBeTruthy();
    });
    expect(
      (screen.getByRole("button", { name: "更新凭据" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "清除" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("saves a trimmed token", async () => {
    getMock.mockResolvedValue(view());
    upsertMock.mockResolvedValue(
      view({ configured: true, masked_token: "ghp_****abcd" }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("尚未配置")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Token"), {
      target: { value: "  ghp_secret  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存凭据" }));

    await waitFor(() => {
      expect(upsertMock).toHaveBeenCalledWith({ token: "ghp_secret" });
    });
  });

  it("clears after confirm", async () => {
    getMock.mockResolvedValue(
      view({ configured: true, masked_token: "ghp_****abcd" }),
    );
    deleteMock.mockResolvedValue({ status: "ok" });
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "清除" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("清除账户 Git 凭据？")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "清除" }));

    await waitFor(() => {
      expect(deleteMock).toHaveBeenCalledTimes(1);
    });
  });
});
