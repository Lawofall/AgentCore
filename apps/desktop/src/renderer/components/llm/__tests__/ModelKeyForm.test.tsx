// @vitest-environment jsdom
/**
 * Tests for BYOK ModelKeyForm — Key + Base URL; models live in 模型组合.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { getByokProviderPreset } from "@/lib/byokProviderPresets";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/llmProviders", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/llmProviders")>()),
  createLlmProvider: vi.fn(),
  updateLlmProvider: vi.fn(),
}));

import {
  type LlmProviderView,
  createLlmProvider,
  updateLlmProvider,
} from "@/services/llmProviders";
import { ModelKeyForm } from "../ModelKeyForm";

const moonshot = getByokProviderPreset("moonshot");
const deepseek = getByokProviderPreset("deepseek");
const openai = getByokProviderPreset("openai");

function savedProvider(over: Partial<LlmProviderView> = {}): LlmProviderView {
  return {
    id: "p-new",
    label: moonshot.label,
    base_url: moonshot.baseUrl,
    status: "unchecked",
    masked_key: "••••abcd",
    ...over,
  };
}

function renderForm(props: Partial<ComponentProps<typeof ModelKeyForm>> = {}) {
  const onSaved = vi.fn();
  const result = render(
    <TooltipProvider>
      <ModelKeyForm onSaved={onSaved} {...props} />
    </TooltipProvider>,
  );
  return { ...result, onSaved };
}

function providerSelect(): HTMLSelectElement {
  return screen.getAllByRole("combobox")[0] as HTMLSelectElement;
}

/** Open「高级选项」<details> so nested Base URL becomes accessible. */
function openAdvancedOptions(): HTMLDetailsElement {
  const details = screen.getByText("高级选项").closest("details");
  if (!(details instanceof HTMLDetailsElement)) {
    throw new Error("expected 高级选项 <details>");
  }
  details.open = true;
  return details;
}

beforeEach(() => {
  vi.mocked(createLlmProvider).mockReset();
  vi.mocked(updateLlmProvider).mockReset();
});

afterEach(cleanup);

describe("ModelKeyForm", () => {
  it("puts 取消 before the primary CTA when cancel is offered", () => {
    const onCancel = vi.fn();
    renderForm({ onCancel });
    const cancel = screen.getByRole("button", { name: "取消" });
    const add = screen.getByRole("button", { name: "添加" });
    expect(
      cancel.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps models off the provider form; advanced holds Base URL for presets", () => {
    renderForm();

    expect(screen.queryByText("默认模型")).toBeNull();
    expect(screen.queryByText("连接测试用模型")).toBeNull();
    expect(screen.getByText("厂商预设")).toBeTruthy();
    expect(screen.getByText("名称")).toBeTruthy();
    expect(screen.getByText(/^API Key/)).toBeTruthy();
    expect(screen.getByText("高级选项")).toBeTruthy();
    expect(
      screen.getByText(/选择后将预填名称与端点；日常选用请到「装配」/),
    ).toBeTruthy();
    openAdvancedOptions();
    expect(screen.getByLabelText("Base URL")).toBeTruthy();
  });

  it("submits preset vendor without a model field", async () => {
    vi.mocked(createLlmProvider).mockResolvedValue(savedProvider());
    const { onSaved } = renderForm();

    fireEvent.change(providerSelect(), { target: { value: "moonshot" } });
    fireEvent.change(screen.getByPlaceholderText("sk-..."), {
      target: { value: "sk-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(createLlmProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          label: moonshot.label,
          base_url: moonshot.baseUrl,
          api_key: "sk-test-key",
        }),
      ),
    );
    expect(createLlmProvider).toHaveBeenCalledWith(
      expect.not.objectContaining({ default_model: expect.anything() }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("fills name and Base URL when switching preset vendors", () => {
    renderForm();

    fireEvent.change(providerSelect(), { target: { value: "moonshot" } });
    fireEvent.change(providerSelect(), { target: { value: "openai" } });

    expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
      openai.label,
    );
    openAdvancedOptions();
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe(
      openai.baseUrl,
    );
  });

  it("keeps custom provider Base URL on main path and hides 高级选项", () => {
    renderForm();
    fireEvent.change(providerSelect(), { target: { value: "custom" } });

    expect(screen.getByLabelText("Base URL").tagName).toBe("INPUT");
    expect(screen.queryByText("高级选项")).toBeNull();
    expect(screen.queryByText("连接测试用模型")).toBeNull();
    expect(
      screen.getByText(
        /自定义地址通常需含 \/v1（例 https:\/\/api\.example\.com\/v1）/,
      ),
    ).toBeTruthy();
  });

  it("shows Base URL /v1 hint in advanced for preset vendors", () => {
    renderForm();
    openAdvancedOptions();
    expect(
      screen.getByText(
        /自定义地址通常需含 \/v1（例 https:\/\/api\.example\.com\/v1）/,
      ),
    ).toBeTruthy();
  });

  it("offers OpenCode Go preset with its own endpoint", async () => {
    const go = getByokProviderPreset("opencode_go");
    vi.mocked(createLlmProvider).mockResolvedValue(
      savedProvider({
        id: "p-go",
        label: go.label,
        base_url: go.baseUrl,
      }),
    );
    renderForm();

    const vendor = providerSelect();
    expect(
      [...vendor.options].some((o) => o.textContent === "OpenCode Go"),
    ).toBe(true);
    expect(
      [...vendor.options].some((o) => o.textContent === "OpenCode Zen"),
    ).toBe(true);

    fireEvent.change(vendor, { target: { value: "opencode_go" } });
    expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
      "OpenCode Go",
    );
    openAdvancedOptions();
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe(
      "https://opencode.ai/zen/go/v1",
    );
    expect(
      screen
        .getByRole("link", { name: /前往 OpenCode Go/ })
        .getAttribute("href"),
    ).toBe("https://opencode.ai/auth");

    fireEvent.change(screen.getByPlaceholderText("sk-..."), {
      target: { value: "sk-go" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(createLlmProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          label: "OpenCode Go",
          base_url: "https://opencode.ai/zen/go/v1",
          api_key: "sk-go",
        }),
      ),
    );
  });

  it("resolves stored OpenCode Go and Zen base_urls to distinct presets", () => {
    const { unmount } = renderForm({
      providerId: "p-go",
      initialLabel: "OpenCode Go",
      initialBaseUrl: "HTTPS://OPENCODE.AI/ZEN/GO/V1/",
    });
    expect(providerSelect().value).toBe("opencode_go");
    unmount();

    renderForm({
      providerId: "p-zen",
      initialLabel: "OpenCode Zen",
      initialBaseUrl: "https://opencode.ai/zen/v1",
    });
    expect(providerSelect().value).toBe("opencode_zen");
  });

  it("saves a DeepSeek preset from the main path without opening advanced", async () => {
    vi.mocked(createLlmProvider).mockResolvedValue(savedProvider());
    renderForm();

    fireEvent.change(providerSelect(), { target: { value: "deepseek" } });
    fireEvent.change(screen.getByPlaceholderText("sk-..."), {
      target: { value: "sk-test-key" },
    });
    expect(screen.queryByText("默认模型")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(createLlmProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          label: deepseek.label,
          base_url: deepseek.baseUrl,
          api_key: "sk-test-key",
        }),
      ),
    );
  });

  it("patches label and base_url without a model field", async () => {
    vi.mocked(updateLlmProvider).mockResolvedValue(savedProvider({ id: "p1" }));
    renderForm({
      providerId: "p1",
      initialLabel: moonshot.label,
      initialBaseUrl: moonshot.baseUrl,
    });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateLlmProvider).toHaveBeenCalledWith(
        "p1",
        expect.objectContaining({
          label: moonshot.label,
          base_url: moonshot.baseUrl,
        }),
      ),
    );
    expect(updateLlmProvider).toHaveBeenCalledWith(
      "p1",
      expect.not.objectContaining({ default_model: expect.anything() }),
    );
  });
});
