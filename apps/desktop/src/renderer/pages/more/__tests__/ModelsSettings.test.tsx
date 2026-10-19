// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmProviders", () => ({ useLlmProviders: vi.fn() }));
vi.mock("@/hooks/useModels", () => ({ useModels: vi.fn() }));
vi.mock("@/services/models", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/models")>()),
  putPickerVisibility: vi.fn(),
  putModelDisplayName: vi.fn(),
}));

import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { putModelDisplayName, putPickerVisibility } from "@/services/models";
import { ModelsSettings } from "../ModelsSettings";

afterEach(cleanup);

function renderPage() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ModelsSettings />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(useLlmProviders).mockReturnValue({
    data: {
      providers: [
        {
          id: "p1",
          label: "DeepSeek",
          base_url: "https://api.deepseek.com/v1",
          vendor_id: "deepseek",
          status: "active",
        },
        {
          id: "p-go",
          label: "OpenCode Go",
          base_url: "https://opencode.ai/zen/go/v1",
          vendor_id: "opencode_go",
          status: "active",
        },
      ],
      platform_available: true,
      billing_mode: "hybrid",
      default_assembly_id: null,
      platform_model: null,
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLlmProviders>);
  vi.mocked(useModels).mockReturnValue({
    data: {
      byok_configured: true,
      hidden_refs: [],
      enabled_refs: [],
      current: {
        id: "flash-free",
        origin: "platform",
        provider_id: null,
        ref: "@platform/flash-free",
      },
      models: [
        {
          id: "flash-free",
          ref: "@platform/flash-free",
          origin: "platform",
          display_name: "Flash Free",
          vendor: "DeepSeek",
          capabilities: [],
          available: true,
          provider_id: null,
        },
        {
          id: "deepseek-v4-pro",
          ref: "@byok/p1/deepseek-v4-pro",
          origin: "byok",
          display_name: "DeepSeek V4 Pro",
          vendor: "DeepSeek",
          capabilities: [],
          available: true,
          provider_id: "p1",
          provider_label: "DeepSeek",
        },
        {
          id: "glm-5.2",
          ref: "@byok/p-go/glm-5.2",
          origin: "byok",
          display_name: "GLM-5.2",
          vendor: "Zhipu",
          capabilities: [],
          available: true,
          provider_id: "p-go",
          provider_label: "OpenCode Go",
        },
        {
          id: "grok-4.6",
          ref: "@byok/p-go/grok-4.6",
          origin: "byok",
          display_name: "grok 4.6",
          vendor: "xAI",
          capabilities: [],
          available: true,
          provider_id: "p-go",
          provider_label: "OpenCode Go",
        },
        {
          id: "glm-5.1",
          ref: "@byok/p-go/glm-5.1",
          origin: "byok",
          display_name: "glm 5.1",
          vendor: "Zhipu",
          capabilities: [],
          available: true,
          provider_id: "p-go",
          provider_label: "OpenCode Go",
        },
        {
          id: "grok-4.7",
          ref: "@byok/p-go/grok-4.7",
          origin: "byok",
          display_name: "grok 4.7",
          vendor: "xAI",
          capabilities: [],
          available: true,
          provider_id: "p-go",
          provider_label: "OpenCode Go",
        },
        {
          id: "muse-spark-1.2",
          ref: "@byok/p-go/muse-spark-1.2",
          origin: "byok",
          display_name: "muse spark 1.2 contributor",
          vendor: "Muse",
          capabilities: [],
          available: true,
          provider_id: "p-go",
          provider_label: "OpenCode Go",
        },
      ],
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useModels>);
  vi.mocked(putPickerVisibility).mockResolvedValue({
    refs: [],
    enabled_refs: ["@byok/p1/deepseek-v4-pro"],
  });
  vi.mocked(putModelDisplayName).mockResolvedValue({
    ref: "@byok/p1/deepseek-v4-pro",
    display_name: "公司 Pro",
  });
});

describe("ModelsSettings", () => {
  it("keeps platform on and connected-provider rows off until opened", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "模型" })).toBeTruthy();
    expect(screen.getByText("平台")).toBeTruthy();
    expect(screen.getByText("DeepSeek")).toBeTruthy();
    expect(screen.getByRole("button", { name: /OpenCode Go/ })).toBeTruthy();
    expect(screen.queryByText("GLM-5.2")).toBeNull();
    expect(
      screen
        .getByRole("switch", { name: "隐藏 Flash Free" })
        .getAttribute("aria-checked"),
    ).toBe("true");
    const open = screen.getByRole("switch", { name: "显示 DeepSeek V4 Pro" });
    expect(open.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(open);
    await waitFor(() =>
      expect(putPickerVisibility).toHaveBeenCalledWith({
        refs: [],
        enabled_refs: ["@byok/p1/deepseek-v4-pro"],
      }),
    );
  });

  it("opens a whole connected-provider section at once while collapsed", async () => {
    renderPage();
    const go = screen
      .getByRole("button", { name: /OpenCode Go/ })
      .closest("section");
    expect(go).toBeTruthy();
    fireEvent.click(
      within(go as HTMLElement).getByRole("button", { name: "全部打开" }),
    );
    await waitFor(() =>
      expect(putPickerVisibility).toHaveBeenCalledWith({
        refs: [],
        enabled_refs: [
          "@byok/p-go/glm-5.2",
          "@byok/p-go/grok-4.6",
          "@byok/p-go/glm-5.1",
          "@byok/p-go/grok-4.7",
          "@byok/p-go/muse-spark-1.2",
        ],
      }),
    );
  });

  it("expands an aggregator catalog into vendor groups", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /OpenCode Go/ }));
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
    expect(screen.getByText("Zhipu")).toBeTruthy();
    expect(screen.getByText("xAI")).toBeTruthy();
    expect(screen.getByText("Muse")).toBeTruthy();
  });

  it("filters across channels", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("筛选模型"), {
      target: { value: "Free" },
    });
    expect(screen.getByText("Flash Free")).toBeTruthy();
    expect(screen.queryByText("DeepSeek V4 Pro")).toBeNull();
    expect(screen.queryByText("GLM-5.2")).toBeNull();
  });

  it("opens a long channel when the filter hits it", () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("筛选模型"), {
      target: { value: "grok 4.6" },
    });
    expect(screen.getByText("grok 4.6")).toBeTruthy();
    expect(screen.queryByText("GLM-5.2")).toBeNull();
  });

  it("renames a connected-provider row from the pencil, not the name", async () => {
    renderPage();
    fireEvent.click(screen.getByText("DeepSeek V4 Pro"));
    expect(screen.queryByPlaceholderText("目录名")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "改名 DeepSeek V4 Pro" }),
    );
    const field = screen.getByPlaceholderText("目录名");
    fireEvent.change(field, { target: { value: "公司 Pro" } });
    fireEvent.blur(field);
    await waitFor(() =>
      expect(putModelDisplayName).toHaveBeenCalledWith({
        ref: "@byok/p1/deepseek-v4-pro",
        display_name: "公司 Pro",
      }),
    );
  });

  it("does not rename platform rows", () => {
    renderPage();
    expect(
      screen.queryByRole("button", { name: "改名 Flash Free" }),
    ).toBeNull();
    expect(screen.getByText("Flash Free")).toBeTruthy();
  });

  it("offers 使用其他模型 on connected channels, not platform", () => {
    renderPage();
    const platform = screen.getByText("平台").closest("section");
    expect(platform).toBeTruthy();
    expect(
      within(platform as HTMLElement).queryByRole("button", {
        name: "使用其他模型",
      }),
    ).toBeNull();
    const deepseek = screen.getByText("DeepSeek").closest("section");
    expect(deepseek).toBeTruthy();
    expect(
      within(deepseek as HTMLElement).getByRole("button", {
        name: "使用其他模型",
      }),
    ).toBeTruthy();
    const go = screen
      .getByRole("button", { name: /OpenCode Go/ })
      .closest("section");
    expect(go).toBeTruthy();
    expect(within(go as HTMLElement).queryByText("GLM-5.2")).toBeNull();
    expect(
      within(go as HTMLElement).getByRole("button", { name: "使用其他模型" }),
    ).toBeTruthy();
  });

  it("adds a typed model id to the connected-provider allowlist", async () => {
    renderPage();
    const deepseek = screen.getByText("DeepSeek").closest("section");
    expect(deepseek).toBeTruthy();
    fireEvent.click(
      within(deepseek as HTMLElement).getByRole("button", {
        name: "使用其他模型",
      }),
    );
    const input = within(deepseek as HTMLElement).getByLabelText(
      "自定义 model id",
    );
    fireEvent.change(input, { target: { value: "ep-next" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(putPickerVisibility).toHaveBeenCalledWith({
        refs: [],
        enabled_refs: ["@byok/p1/ep-next"],
      }),
    );
  });
});
