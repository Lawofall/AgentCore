// @vitest-environment jsdom
/**
 * Tests for 设置·服务商 (BYOK providers; platform-on has no quota copy).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmProviders", () => ({ useLlmProviders: vi.fn() }));
vi.mock("@/services/llmProviders", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/llmProviders")>()),
  deleteLlmProvider: vi.fn(() => Promise.resolve({ status: "ok" })),
  testLlmProvider: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/components/llm/ModelKeyForm", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/components/llm/ModelKeyForm")>();
  return {
    ...actual,
    ModelKeyForm: () => <div data-testid="provider-form" />,
  };
});

import { useLlmProviders } from "@/hooks/useLlmProviders";
import { ApiError } from "@/services/api";
import type { LlmProvidersResponse } from "@/services/llmProviders";
import { deleteLlmProvider } from "@/services/llmProviders";
import { ProviderSettings } from "../ProviderSettings";

const useLlmProvidersMock = vi.mocked(useLlmProviders);

function providersResponse(
  over: Partial<LlmProvidersResponse> = {},
): LlmProvidersResponse {
  return {
    providers: [
      {
        id: "p1",
        label: "DeepSeek",
        base_url: "https://api.deepseek.com/v1",
        default_model: "deepseek-v4-pro",
        status: "active",
        masked_key: "••••abcd",
        supports_tools: true,
      },
      {
        id: "p2",
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        default_model: "gpt-4o",
        status: "unchecked",
        masked_key: "••••wxyz",
      },
    ],
    default_model_profile_id: "sys-52",
    billing_mode: "byok",
    platform_available: false,
    platform_model: null,
    ...over,
  };
}

function mockProviders(data: LlmProvidersResponse | undefined): void {
  useLlmProvidersMock.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLlmProviders>);
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ProviderSettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(deleteLlmProvider).mockClear();
});

afterEach(cleanup);

describe("ProviderSettings", () => {
  it("renders provider cards and the add affordance", () => {
    mockProviders(providersResponse());
    renderPage();
    expect(screen.getByText("DeepSeek")).toBeTruthy();
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.getByText(/api\.deepseek\.com/)).toBeTruthy();
    expect(screen.getByText(/••••abcd/)).toBeTruthy();
    expect(screen.getByText(/测试用模型 deepseek-v4-pro/)).toBeTruthy();
    expect(screen.getByText(/测试用模型 gpt-4o/)).toBeTruthy();
    expect(screen.queryByText(/默认模型/)).toBeNull();
    expect(screen.getByRole("button", { name: "添加服务商" })).toBeTruthy();
    expect(screen.queryByText("模型组合")).toBeNull();
    expect(
      screen.getByText("测连绿≠可聊天；自定义 Base URL 常需 /v1"),
    ).toBeTruthy();
  });

  it("omits the test-model line when default_model is empty", () => {
    mockProviders(
      providersResponse({
        providers: [
          {
            ...providersResponse().providers[0],
            default_model: "",
          },
        ],
      }),
    );
    renderPage();
    expect(screen.getByText("DeepSeek")).toBeTruthy();
    expect(screen.queryByText(/测试用模型/)).toBeNull();
    expect(screen.queryByText(/默认模型/)).toBeNull();
  });

  it("does not advertise the page job in a header lede", () => {
    mockProviders(
      providersResponse({
        platform_available: true,
        platform_model: "deepseek-v4-flash",
      }),
    );
    renderPage();
    expect(screen.getByText("服务商")).toBeTruthy();
    expect(screen.queryByText(/接入自己的 OpenAI 兼容端点/)).toBeNull();
    expect(screen.queryByText(/不接入也可用平台额度/)).toBeNull();
    expect(screen.queryByText("无需配置")).toBeNull();
    expect(screen.queryByText(/未接入自己的模型时/)).toBeNull();
    expect(screen.queryByText(/平台模型 deepseek-v4-flash/)).toBeNull();
  });

  it("when platform is off and no providers, empty state offers add", () => {
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: false,
        billing_mode: "byok",
      }),
    );
    renderPage();
    expect(screen.getByText("还没有接入服务商。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "添加服务商" })).toBeTruthy();
    expect(screen.queryByText(/需自行接入服务商后才能对话/)).toBeNull();
    expect(screen.queryByText(/不接入也可用平台额度/)).toBeNull();
    expect(screen.queryByText(/联系管理员/)).toBeNull();
  });

  it("confirms in an in-product dialog, then deletes a provider", async () => {
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[1]);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/删除服务商「OpenAI」？/)).toBeTruthy();
    // 还剩一个服务商兜底 → 软文案，不该吓唬用户说会断供。
    expect(within(dialog).getByText(/不会中断对话/)).toBeTruthy();
    expect(vi.mocked(deleteLlmProvider)).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(vi.mocked(deleteLlmProvider)).toHaveBeenCalledWith("p2"),
    );
  });

  it("cancelling the confirm dialog leaves the provider alone", async () => {
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[1]);

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(vi.mocked(deleteLlmProvider)).not.toHaveBeenCalled();
  });

  it("warns that deleting the last provider cuts off conversations", async () => {
    mockProviders(
      providersResponse({
        providers: [providersResponse().providers[0]],
        platform_available: false,
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/这是唯一的服务商/)).toBeTruthy();
  });

  it("surfaces ADMIN_PRODUCT_FORBIDDEN instead of a generic load failure", () => {
    useLlmProvidersMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(
        403,
        JSON.stringify({
          error: {
            code: "ADMIN_PRODUCT_FORBIDDEN",
            message: "管理员账号请使用管理后台登录",
          },
        }),
      ),
    } as unknown as ReturnType<typeof useLlmProviders>);
    renderPage();
    expect(
      screen.getByText("此账号为管理员账号，请使用管理后台登录"),
    ).toBeTruthy();
    expect(screen.queryByText("加载失败，请重试")).toBeNull();
  });

  it("maps 404 load failure to client version-mismatch copy", () => {
    useLlmProvidersMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(404, "{}"),
    } as unknown as ReturnType<typeof useLlmProviders>);
    renderPage();
    expect(
      screen.getByText("当前客户端版本过旧，请到设置 · 关于检查更新"),
    ).toBeTruthy();
    expect(screen.queryByText("加载失败，请重试")).toBeNull();
  });
});
