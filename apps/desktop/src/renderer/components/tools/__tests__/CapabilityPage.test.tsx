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
};

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
});

afterEach(cleanup);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.mine.skills]}>
      <CapabilityPage>{() => <div>目录正文</div>}</CapabilityPage>
    </MemoryRouter>,
  );
}

describe("CapabilityPage", () => {
  it("不再自带页头，由工具箱壳认路", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(catalog);
    renderPage();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.queryByRole("link", { name: "工具箱" })).toBeNull();
    await waitFor(() => expect(screen.getByText("目录正文")).toBeTruthy());
  });

  it("does not render a page-level lede", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(catalog);
    renderPage();
    expect(screen.queryByText(/全员/)).toBeNull();
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
