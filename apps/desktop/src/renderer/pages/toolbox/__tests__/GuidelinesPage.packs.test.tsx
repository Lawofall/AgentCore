import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
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
import { GuidelinesPage } from "../GuidelinesPage";

vi.mock("@/services/capabilities", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/capabilities")>();
  return {
    ...actual,
    getCapabilities: vi.fn(),
  };
});

const { getCapabilities } = await import("@/services/capabilities");

const base: Capabilities = {
  guidelines: {
    shared_base: "共享准则正文",
    worker_leaf: "叶子身份正文",
    worker_captain: "可再委派队员身份正文",
    ceo_addon: "主 Agent 身份正文",
    ceo: "完整 CEO 提示词",
  },
  skills: [
    {
      name: "delegate_playbook",
      summary: "派单进阶",
      body: "body",
    },
  ],
  tools: [],
  packs: [],
};

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
});

afterEach(cleanup);

function renderPage() {
  return render(
    <MemoryRouter>
      <GuidelinesPage />
    </MemoryRouter>,
  );
}

describe("GuidelinesPage 提示词阅读器", () => {
  it("packs 缺失时不渲染能力包区，目录仍有准则与薄技能", async () => {
    const { packs: _packs, ...withoutPacks } = base;
    vi.mocked(getCapabilities).mockResolvedValue(withoutPacks as Capabilities);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("全员共享准则")).toBeTruthy();
    });
    expect(screen.queryByTestId("capability-packs")).toBeNull();
    expect(screen.getByText("按需注入")).toBeTruthy();
    expect(screen.getByText("派单进阶")).toBeTruthy();
    expect(screen.queryByText("工具进阶用法（薄技能）")).toBeNull();
  });

  it("packs 为空数组时不渲染能力包区", async () => {
    vi.mocked(getCapabilities).mockResolvedValue({ ...base, packs: [] });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("全员共享准则")).toBeTruthy();
    });
    expect(screen.queryByTestId("capability-packs")).toBeNull();
  });

  it("默认打开角色身份 · 主 Agent，点目录才换正文", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("主 Agent 身份正文")).toBeTruthy();
    });
    expect(screen.queryByText("共享准则正文")).toBeNull();

    fireEvent.click(screen.getByText("全员共享准则"));
    expect(screen.getByText("共享准则正文")).toBeTruthy();
    expect(screen.queryByText("主 Agent 身份正文")).toBeNull();
  });

  it("薄技能目录用人话摘要，详情才露出内部名", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("派单进阶")).toBeTruthy();
    });
    expect(screen.queryByText("delegate_playbook")).toBeNull();

    fireEvent.click(screen.getByText("派单进阶"));
    expect(screen.getByText("delegate_playbook")).toBeTruthy();
    expect(screen.getByText("body")).toBeTruthy();
  });

  it("有 packs 时进目录分组，包内技能从按需区去重", async () => {
    vi.mocked(getCapabilities).mockResolvedValue({
      ...base,
      skills: [
        ...base.skills,
        {
          name: "contract_review",
          summary: "审查合同",
          body: "body",
        },
      ],
      packs: [
        {
          id: "legal",
          name: "法律能力",
          summary: "合同审查与合规",
          skills: [
            {
              name: "contract_review",
              summary: "审查合同",
              body: "body",
            },
          ],
        },
      ],
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("capability-packs")).toBeTruthy();
    });
    expect(screen.getByText("能力包")).toBeTruthy();
    expect(screen.getByText("法律能力")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /启用|停用/ })).toBeNull();
    expect(screen.getAllByText("审查合同")).toHaveLength(1);
    expect(screen.getByText("派单进阶")).toBeTruthy();

    fireEvent.click(screen.getByText("法律能力"));
    expect(screen.getByText("合同审查与合规")).toBeTruthy();
    expect(screen.getByText("包内技能")).toBeTruthy();
    expect(screen.getByText("contract_review")).toBeTruthy();
  });

  it("渲染三选一角色身份，不把身份叠成四层", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "角色身份" })).toBeTruthy();
    });
    expect(screen.getByRole("tab", { name: "主 Agent" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "可再委派的队员" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "叶子队员" })).toBeTruthy();
    expect(screen.getByText("主 Agent 身份正文")).toBeTruthy();
    expect(screen.queryByText("CEO 专属提示词")).toBeNull();
    expect(screen.queryByText("队员身份（队长）")).toBeNull();
    expect(screen.queryByText("队员身份（叶子）")).toBeNull();
    expect(screen.queryByText("队员交付合同")).toBeNull();
  });

  it("切换页签只换身份，队员合同只出现一次且不含按需目录", async () => {
    const contract =
      "你的交付形态是【落盘文件】。\n\n<写工具谨慎>\n谨慎写盘。\n</写工具谨慎>";
    vi.mocked(getCapabilities).mockResolvedValue({
      ...base,
      guidelines: {
        ...base.guidelines,
        worker_leaf: `<身份>\n叶子身份。\n</身份>\n\n${contract}`,
        worker_captain: `<身份>\n可再委派身份。\n</身份>\n\n${contract}`,
        ceo_addon:
          "<身份>\n主 Agent 核。\n</身份>\n\n<按需目录>\n- lead_subteam：子队拆法\n</按需目录>",
      },
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("主 Agent 核。")).toBeTruthy();
    });
    expect(screen.queryByText("lead_subteam：子队拆法")).toBeNull();
    expect(screen.getAllByText("队员交付合同")).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "叶子队员" }));
    expect(screen.getByText("叶子身份。")).toBeTruthy();
    expect(screen.queryByText("主 Agent 核。")).toBeNull();
    expect(screen.getAllByText("队员交付合同")).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "可再委派的队员" }));
    expect(screen.getByText("可再委派身份。")).toBeTruthy();
    expect(screen.getAllByText("队员交付合同")).toHaveLength(1);

    fireEvent.click(screen.getByText("队员交付合同"));
    expect(screen.getByText("你的交付形态是【落盘文件】。")).toBeTruthy();
  });
});
