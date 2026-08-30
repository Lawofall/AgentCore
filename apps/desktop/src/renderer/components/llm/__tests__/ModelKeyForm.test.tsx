// @vitest-environment jsdom
/**
 * Tests for BYOK ModelKeyForm — advanced「连接测试用模型」Input + datalist.
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
    default_model: moonshot.defaultModel,
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

/** Open「高级选项」<details> so nested controls become accessible to queries. */
function openAdvancedOptions(): HTMLDetailsElement {
  const details = screen.getByText("高级选项").closest("details");
  if (!(details instanceof HTMLDetailsElement)) {
    throw new Error("expected 高级选项 <details>");
  }
  details.open = true;
  return details;
}

/** Query「连接测试用模型」after opening advanced options. */
function defaultModelControl(): HTMLElement {
  openAdvancedOptions();
  return screen.getByLabelText("连接测试用模型");
}

/** Options from the datalist bound to the connection-test model Input. */
function defaultModelDatalistOptions(input: HTMLInputElement): string[] {
  const listId = input.getAttribute("list");
  expect(listId).toBeTruthy();
  if (!listId) return [];
  const list = document.getElementById(listId);
  expect(list?.tagName).toBe("DATALIST");
  if (!list) return [];
  return Array.from(list.querySelectorAll("option")).map(
    (o) => (o as HTMLOptionElement).value,
  );
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

  it("keeps default model off the main path; advanced holds 连接测试用模型", () => {
    renderForm();

    expect(screen.queryByText("默认模型")).toBeNull();
    expect(screen.getByText("厂商预设")).toBeTruthy();
    expect(screen.getByText("名称")).toBeTruthy();
    expect(screen.getByText(/^API Key/)).toBeTruthy();
    expect(screen.getByText("高级选项")).toBeTruthy();
    expect(
      screen.getByText(/选择后将预填名称与端点；日常选用请到「模型组合」/),
    ).toBeTruthy();
    expect(defaultModelControl()).toBeTruthy();
    expect(
      screen.getByText(/可直接粘贴模型 ID；连接测试与目录兜底用/),
    ).toBeTruthy();
  });

  it("shows DeepSeek preset models as Input + datalist including deepseek-v4-flash", () => {
    renderForm();

    fireEvent.change(providerSelect(), { target: { value: "deepseek" } });

    const modelInput = defaultModelControl() as HTMLInputElement;
    expect(modelInput.tagName).toBe("INPUT");
    expect(modelInput.value).toBe(deepseek.defaultModel);

    const optionValues = defaultModelDatalistOptions(modelInput);
    for (const model of deepseek.models) {
      expect(optionValues).toContain(model);
    }
    expect(screen.queryByText("其他…")).toBeNull();
  });

  it("lets preset vendors free-type a custom default model in the visible Input", async () => {
    vi.mocked(createLlmProvider).mockResolvedValue(savedProvider());
    const { onSaved } = renderForm();

    fireEvent.change(providerSelect(), { target: { value: "moonshot" } });

    const modelInput = defaultModelControl() as HTMLInputElement;
    expect(modelInput.tagName).toBe("INPUT");
    expect(modelInput.value).toBe(moonshot.defaultModel);

    fireEvent.change(modelInput, {
      target: { value: "kimi-custom-test" },
    });
    fireEvent.change(screen.getByPlaceholderText("sk-..."), {
      target: { value: "sk-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(createLlmProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          label: moonshot.label,
          base_url: moonshot.baseUrl,
          default_model: "kimi-custom-test",
          api_key: "sk-test-key",
        }),
      ),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("preserves a custom typed model when switching preset vendors", () => {
    renderForm();

    fireEvent.change(providerSelect(), { target: { value: "moonshot" } });
    const modelInput = defaultModelControl() as HTMLInputElement;
    fireEvent.change(modelInput, {
      target: { value: "my-custom-model-id" },
    });

    fireEvent.change(providerSelect(), { target: { value: "openai" } });

    const after = defaultModelControl() as HTMLInputElement;
    expect(after.value).toBe("my-custom-model-id");
    expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
      openai.label,
    );
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe(
      openai.baseUrl,
    );
  });

  it("opens advanced when editing a stored model not in the preset list", async () => {
    vi.mocked(updateLlmProvider).mockResolvedValue(
      savedProvider({
        id: "p1",
        default_model: "already-saved-model",
      }),
    );
    renderForm({
      providerId: "p1",
      initialLabel: moonshot.label,
      initialBaseUrl: moonshot.baseUrl,
      initialModel: "already-saved-model",
    });

    const details = screen.getByText("高级选项").closest("details");
    expect(details?.open).toBe(true);
    const modelInput = screen.getByLabelText(
      "连接测试用模型",
    ) as HTMLInputElement;
    expect(modelInput.tagName).toBe("INPUT");
    expect(modelInput.value).toBe("already-saved-model");

    fireEvent.change(modelInput, {
      target: { value: "edited-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(updateLlmProvider).toHaveBeenCalledWith(
        "p1",
        expect.objectContaining({
          default_model: "edited-model",
          label: moonshot.label,
          base_url: moonshot.baseUrl,
        }),
      ),
    );
  });

  it("keeps custom provider Base URL on main path; connection-test model in advanced", () => {
    renderForm();
    fireEvent.change(providerSelect(), { target: { value: "custom" } });

    expect(screen.getByLabelText("Base URL").tagName).toBe("INPUT");
    expect(screen.getByText("高级选项")).toBeTruthy();
    expect(
      screen.getByText(
        /自定义地址通常需含 \/v1（例 https:\/\/api\.example\.com\/v1）/,
      ),
    ).toBeTruthy();

    const defaultModelInput = defaultModelControl() as HTMLInputElement;
    expect(defaultModelInput.tagName).toBe("INPUT");
    expect(defaultModelInput.getAttribute("list")).toBeNull();
    expect(screen.queryByText("其他…")).toBeNull();
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

  it("offers OpenCode Go preset with its own endpoint and chat/completions seed", async () => {
    const go = getByokProviderPreset("opencode_go");
    vi.mocked(createLlmProvider).mockResolvedValue(
      savedProvider({
        id: "p-go",
        label: go.label,
        base_url: go.baseUrl,
        default_model: go.defaultModel,
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

    const modelInput = defaultModelControl() as HTMLInputElement;
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe(
      "https://opencode.ai/zen/go/v1",
    );
    expect(modelInput.value).toBe("deepseek-v4-flash");
    expect(defaultModelDatalistOptions(modelInput)).toEqual([
      "deepseek-v4-flash",
      "deepseek-v4-pro",
      "glm-5.2",
    ]);
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
          default_model: "deepseek-v4-flash",
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
      initialModel: "deepseek-v4-flash",
    });
    expect(providerSelect().value).toBe("opencode_go");
    unmount();

    renderForm({
      providerId: "p-zen",
      initialLabel: "OpenCode Zen",
      initialBaseUrl: "https://opencode.ai/zen/v1",
      initialModel: "deepseek-v4-flash",
    });
    expect(providerSelect().value).toBe("opencode_zen");
  });

  it("pre-fills default_model on preset change when still on prior default and still submits it", async () => {
    vi.mocked(createLlmProvider).mockResolvedValue(savedProvider());
    renderForm();

    fireEvent.change(providerSelect(), { target: { value: "deepseek" } });
    fireEvent.change(screen.getByPlaceholderText("sk-..."), {
      target: { value: "sk-test-key" },
    });
    // Main path must not expose「默认模型」; save without opening advanced.
    expect(screen.queryByText("默认模型")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(createLlmProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          label: deepseek.label,
          base_url: deepseek.baseUrl,
          default_model: deepseek.defaultModel,
          api_key: "sk-test-key",
        }),
      ),
    );
  });
});
