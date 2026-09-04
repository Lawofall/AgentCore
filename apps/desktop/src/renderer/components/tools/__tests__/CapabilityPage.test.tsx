import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { Capabilities } from "@/services/capabilities";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CapabilityPage } from "../CapabilityPage";
import { __resetCapabilitiesCacheForTests } from "../useCapabilities";

vi.mock("@/services/capabilities", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/capabilities")>();
  return { ...actual, getCapabilities: vi.fn() };
});

const { getCapabilities } = await import("@/services/capabilities");

const catalog: Capabilities = {
  guidelines: {
    shared_base: "共享准则",
    worker_leaf: "叶子身份",
    worker_captain: "可再委派队员身份",
    ceo_addon: "CEO 附加",
    ceo: "CEO",
  },
  skills: [],
  tools: [],
  packs: [],
};

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
});

afterEach(cleanup);

function renderPage(note?: string) {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.tools]}>
      <CapabilityPage note={note}>{() => <div>目录正文</div>}</CapabilityPage>
    </MemoryRouter>,
  );
}

describe("CapabilityPage 统一页头", () => {
  it("用工具箱页头承担返回与定位，不再自挂 h1 标题", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(catalog);
    const { container } = renderPage();

    expect(
      screen.getByRole("link", { name: "工具箱" }).getAttribute("href"),
    ).toBe(APP_PATHS.toolbox.root);
    expect(screen.getByRole("navigation", { name: "工具箱能力" })).toBeTruthy();
    expect(container.querySelector("h1")).toBeNull();
    await waitFor(() => expect(screen.getByText("目录正文")).toBeTruthy());
  });

  it("note 降级为内容区第一行 muted 小字", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(catalog);
    renderPage("「全员」CEO 与队员都可用。");

    const note = screen.getByText("「全员」CEO 与队员都可用。");
    expect(note.className).toContain("text-xs");
    expect(note.className).toContain("text-muted-foreground");
    await waitFor(() => expect(screen.getByText("目录正文")).toBeTruthy());
  });

  it("保留加载 / 失败重试 / 就绪三态", async () => {
    vi.mocked(getCapabilities).mockRejectedValueOnce(new Error("boom"));
    renderPage();

    expect(screen.getByText("加载中…")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByText("能力列表加载失败")).toBeTruthy(),
    );

    vi.mocked(getCapabilities).mockResolvedValueOnce(catalog);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByText("目录正文")).toBeTruthy());
  });
});
