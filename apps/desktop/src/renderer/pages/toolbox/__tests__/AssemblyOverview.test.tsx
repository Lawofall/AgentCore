// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const select = vi.fn();
const create = vi.fn();
const rename = vi.fn(async () => true);

const editing = {
  profile: null as {
    id: string;
    name: string;
    kind: "system" | "user";
    is_default: boolean;
    main: { origin: "platform"; provider_id: null; model: string };
    enabled_mcp_server_ids: string[];
    recipe?: string | null;
  } | null,
  profiles: [] as {
    id: string;
    name: string;
    kind: "system" | "user";
    is_default: boolean;
    main: { origin: "platform"; provider_id: null; model: string };
    enabled_mcp_server_ids: string[];
    recipe?: string | null;
  }[],
  conversationId: null as string | null,
  conversation: undefined,
  pending: false,
  loading: false,
  select,
  create,
  rename,
};

vi.mock("@/pages/toolbox/useEditingAssembly", () => ({
  useEditingAssembly: () => editing,
}));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({ data: { models: [] } }),
}));
vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({
    data: { providers: [], platform_available: true },
    isLoading: false,
    isError: false,
  }),
}));

import {
  AssemblyOverview,
  assemblyPromptSummary,
  countPromptModes,
  envelopeOffLine,
  plugEnableLine,
  promptRosterLine,
  toolOffLine,
} from "@/pages/toolbox/AssemblyOverview";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const preset = {
  id: "sys",
  name: "GLM-5.2",
  kind: "system" as const,
  is_default: true,
  main: { origin: "platform" as const, provider_id: null, model: "glm-5.2" },
  enabled_mcp_server_ids: ["fs"],
};

const mine = {
  id: "mine",
  name: "写稿",
  kind: "user" as const,
  is_default: false,
  main: { origin: "platform" as const, provider_id: null, model: "flash" },
  enabled_mcp_server_ids: [],
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/toolbox"]}>
        <AssemblyOverview />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  editing.profile = preset;
  editing.profiles = [preset, mine];
  editing.conversationId = null;
  editing.pending = false;
  editing.loading = false;
  select.mockReset();
  create.mockReset();
  rename.mockReset();
  rename.mockResolvedValue(true);
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("装配摘要文案", () => {
  it("关了才计数，碰到文件有条目才写", () => {
    expect(toolOffLine(0)).toBe("都开");
    expect(toolOffLine(2)).toBe("关了 2 把");
    expect(envelopeOffLine(0)).toBe("都在");
    expect(envelopeOffLine(1)).toBe("关了 1 行");
    expect(plugEnableLine(0)).toBe("启用 0");
    expect(promptRosterLine(countPromptModes([{ applyMode: "always" }]))).toBe(
      "常驻 1 · 按需 0",
    );
    expect(
      promptRosterLine(
        countPromptModes([{ applyMode: "always" }, { applyMode: "paths" }]),
      ),
    ).toBe("常驻 1 · 按需 0 · 碰到文件 1");
    expect(assemblyPromptSummary("常驻 1 · 按需 0", 0, 4)).toBe(
      "常驻 1 · 按需 0 · 出厂 开",
    );
    expect(assemblyPromptSummary("常驻 1 · 按需 0", 4, 4)).toBe(
      "常驻 1 · 按需 0 · 出厂 关",
    );
    expect(assemblyPromptSummary("常驻 1 · 按需 0", 1, 4)).toBe(
      "常驻 1 · 按需 0 · 出厂 关了 1 本",
    );
    expect(assemblyPromptSummary("加载中…", 4, 4)).toBe("加载中…");
  });
});

describe("装配胶囊", () => {
  it("名字胶囊含当前这份，点名字换这份", () => {
    renderPage();
    expect(screen.queryByText(/改的是星标这份/)).toBeNull();
    expect(screen.queryByText(/还没有这场/)).toBeNull();

    expect(screen.queryByRole("group", { name: "预置" })).toBeNull();
    const presetChip = screen.getByRole("button", {
      name: "GLM-5.2",
      pressed: true,
    });
    const mineChip = screen.getByRole("button", { name: "写稿" });
    expect(presetChip.parentElement).toBe(mineChip.parentElement);
    expect(mineChip.className).toContain("border");

    fireEvent.click(screen.getByRole("button", { name: "写稿" }));
    expect(select).toHaveBeenCalledWith("mine");
    expect(
      screen.queryByRole("button", { name: "以后新建也用这份" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /设为新会话默认/ })).toBeNull();

    const copyActions = screen.getByTestId("assembly-copy-actions");
    const createCopy = within(copyActions).getByRole("button", {
      name: "新建一份",
    });
    expect(createCopy.textContent?.trim()).toBe("");
    fireEvent.click(createCopy);
    expect(create).toHaveBeenCalledTimes(1);
    expect(
      within(copyActions).queryByRole("button", { name: "删除这份" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "删除GLM-5.2" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除写稿" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收成空白" })).toBeNull();
    expect(screen.queryByText("主模型")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "模型" })).toBeNull();
    expect(screen.queryByText(/只对之后用这份装配新建的对话成立/)).toBeNull();
  });

  it("自建这份可以删除，点另一颗只换这份", () => {
    editing.conversationId = "c1";
    editing.profile = mine;
    renderPage();
    expect(
      screen.queryByText(
        "这场用这份。正在生成的这一轮不换，从下一次进入回合生效。",
      ),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "写稿", pressed: true }),
    ).toBeTruthy();
    const copyActions = screen.getByTestId("assembly-copy-actions");
    expect(
      within(copyActions).getByRole("button", { name: "删除这份" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "删除写稿" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收成空白" })).toBeNull();
    expect(screen.queryByTestId("assembly-model-block")).toBeNull();
    expect(screen.queryByTestId("assembly-model-row")).toBeNull();
    const nameChip = screen.getByRole("button", {
      name: "写稿",
      pressed: true,
    });
    expect(
      screen.queryByRole("button", { name: "以后新建也用这份" }),
    ).toBeNull();
    expect(screen.queryByText("主模型")).toBeNull();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "GLM-5.2" }));
    expect(select).toHaveBeenCalledWith("sys");
    select.mockClear();
    fireEvent.click(nameChip);
    expect(select).not.toHaveBeenCalled();
    expect(screen.queryByText(/只对之后用这份装配新建的对话成立/)).toBeNull();
    expect(screen.queryByRole("switch", { name: "出厂" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "出厂目录" })).toBeNull();
  });

  it("官方标准和完整与自建同一套胶囊，自建不折进官方", () => {
    const standard = {
      id: "sys-standard",
      name: "标准",
      kind: "system" as const,
      is_default: true,
      recipe: "standard" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const full = {
      id: "sys-full",
      name: "完整",
      kind: "system" as const,
      is_default: false,
      recipe: "full" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const owned = {
      id: "owned",
      name: "写稿",
      kind: "user" as const,
      is_default: true,
      recipe: null,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    editing.profile = owned;
    editing.profiles = [standard, full, owned];
    renderPage();

    expect(screen.queryByRole("group", { name: "预置" })).toBeNull();
    expect(screen.queryByRole("button", { name: "极简" })).toBeNull();
    expect(screen.queryByRole("button", { name: "轻量" })).toBeNull();
    expect(screen.getByRole("button", { name: "标准" })).toBeTruthy();
    const presetChip = screen.getByRole("button", { name: "完整" });
    const ownedChip = screen.getByRole("button", {
      name: "写稿",
      pressed: true,
    });
    expect(presetChip.parentElement).toBe(ownedChip.parentElement);
    expect(presetChip.className).toContain("rounded-full");
    expect(ownedChip.className).toContain("rounded-full");

    fireEvent.click(screen.getByRole("button", { name: "删除这份" }));
    expect(screen.getByText(/还指着它的对话会改用剩下的另一份/)).toBeTruthy();
  });

  it("看着标准点新建直接从标准复制，极简不进胶囊", () => {
    const chat = {
      id: "sys-chat",
      name: "极简",
      kind: "system" as const,
      is_default: false,
      recipe: "chat" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const standard = {
      id: "sys-standard",
      name: "标准",
      kind: "system" as const,
      is_default: true,
      recipe: "standard" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const full = {
      id: "sys-full",
      name: "完整",
      kind: "system" as const,
      is_default: false,
      recipe: "full" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    editing.profile = standard;
    editing.profiles = [standard, full, chat, mine];
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建一份" }));
    expect(create).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "空白" })).toBeNull();
    expect(screen.queryByRole("button", { name: "从完整复制" })).toBeNull();
    expect(screen.queryByRole("button", { name: "极简" })).toBeNull();
    expect(
      screen.getByText("标准不能改。要自己关工具，点新建。"),
    ).toBeTruthy();
  });

  it("这场用极简时胶囊不选中，说明只聊天", () => {
    const chat = {
      id: "sys-chat",
      name: "极简",
      kind: "system" as const,
      is_default: false,
      recipe: "chat" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const standard = {
      id: "sys-standard",
      name: "标准",
      kind: "system" as const,
      is_default: true,
      recipe: "standard" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const full = {
      id: "sys-full",
      name: "完整",
      kind: "system" as const,
      is_default: false,
      recipe: "full" as const,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    editing.profile = chat;
    editing.profiles = [standard, full, chat, mine];
    editing.conversationId = "c1";
    renderPage();
    expect(screen.queryByRole("button", { name: "极简" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "标准", pressed: false }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "完整", pressed: false }),
    ).toBeTruthy();
    expect(
      screen.getByText("这场只聊天，不能改。要自己关工具，点新建。"),
    ).toBeTruthy();
  });

  it("官方胶囊再点不改名，点另一颗仍是换份", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "GLM-5.2" }));
    expect(select).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "能力名称" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "写稿" }));
    expect(select).toHaveBeenCalledWith("mine");
  });

  it("自建再点当前胶囊就地改名", () => {
    editing.profile = mine;
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "写稿" }));
    const input = screen.getByRole("textbox", { name: "能力名称" });
    expect((input as HTMLInputElement).value).toBe("写稿");
    fireEvent.change(input, { target: { value: "草稿" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(rename).toHaveBeenCalledWith("草稿");
  });

  it("新建和删除交给标题行，市场不在这一行里", () => {
    editing.conversationId = "c1";
    editing.profile = mine;
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const renderNames = (names: ReactNode, actions?: ReactNode) => (
      <div data-testid="assembly-name-row">
        {names}
        <div data-testid="assembly-title-slot">{actions}</div>
      </div>
    );
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AssemblyOverview renderNames={renderNames} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const slot = screen.getByTestId("assembly-title-slot");
    const row = screen.getByTestId("assembly-name-row");
    expect(within(slot).getByRole("button", { name: "新建一份" })).toBeTruthy();
    expect(within(slot).queryByRole("button", { name: "收成空白" })).toBeNull();
    expect(within(slot).getByRole("button", { name: "删除这份" })).toBeTruthy();
    expect(
      within(row).getByRole("button", { name: "写稿", pressed: true }),
    ).toBeTruthy();
    expect(
      within(slot).queryByRole("button", { name: "以后新建也用这份" }),
    ).toBeNull();
    expect(
      within(row).queryByRole("button", { name: "以后新建也用这份" }),
    ).toBeNull();
    expect(
      within(row).queryByRole("textbox", { name: "搜提示词、工具" }),
    ).toBeNull();
  });

  it("装配页不再改模型", () => {
    renderPage();
    expect(screen.queryByTestId("assembly-model-block")).toBeNull();
    expect(screen.queryByTestId("assembly-model-row")).toBeNull();
    expect(screen.queryByLabelText("主模型")).toBeNull();
    expect(screen.queryByText("组队队员")).toBeNull();
  });
});
