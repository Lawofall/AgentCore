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
const star = vi.fn();
const createFromCurrent = vi.fn();
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
  star,
  createFromCurrent,
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
  star.mockReset();
  createFromCurrent.mockReset();
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
      "必带 1 · 按需 0",
    );
    expect(
      promptRosterLine(
        countPromptModes([{ applyMode: "always" }, { applyMode: "paths" }]),
      ),
    ).toBe("必带 1 · 按需 0 · 碰到文件 1");
    expect(assemblyPromptSummary("必带 1 · 按需 0", false)).toBe(
      "必带 1 · 按需 0 · 出厂 开",
    );
    expect(assemblyPromptSummary("必带 1 · 按需 0", true)).toBe(
      "必带 1 · 按需 0 · 出厂 关",
    );
    expect(assemblyPromptSummary("加载中…", true)).toBe("加载中…");
  });
});

describe("装配胶囊", () => {
  it("名字胶囊含当前这份，点名字换这份", () => {
    renderPage();
    expect(screen.queryByText(/改的是星标这份/)).toBeNull();
    expect(screen.queryByText(/还没有这场/)).toBeNull();

    expect(screen.queryByRole("group", { name: "预置" })).toBeNull();
    const presetChip = screen.getByRole("button", {
      name: "GLM-5.2，新建对话用这份",
      pressed: true,
    });
    const mineChip = screen.getByRole("button", { name: "写稿" });
    expect(presetChip.parentElement).toBe(mineChip.parentElement);
    expect(mineChip.className).toContain("border");

    fireEvent.click(screen.getByRole("button", { name: "写稿" }));
    expect(select).toHaveBeenCalledWith("mine");
    expect(star).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "以后新建也用这份" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /设为新会话默认/ })).toBeNull();

    const copyActions = screen.getByTestId("assembly-copy-actions");
    const createCopy = within(copyActions).getByRole("button", {
      name: "新建一份",
    });
    const deleteCopy = within(copyActions).getByRole("button", {
      name: "删除这份",
    });
    expect(createCopy.textContent?.trim()).toBe("");
    expect(deleteCopy.textContent?.trim()).toBe("");
    fireEvent.click(createCopy);
    expect(createFromCurrent).toHaveBeenCalled();
    expect(deleteCopy).toBeTruthy();
    expect(screen.queryByRole("button", { name: "删除GLM-5.2" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删除写稿" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收成空白" })).toBeNull();
    expect(screen.getByText("主模型")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "模型" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "删除这份" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(
        "从这排拿掉。星标改到剩下的另一份，还指着它的对话也改过去。",
      ),
    ).toBeTruthy();
    expect(within(dialog).queryByText(/跟随主模型/)).toBeNull();
    expect(screen.queryByText(/只对之后用这份装配新建的对话成立/)).toBeNull();
  });

  it("自建这份可以删除，有这场时才能改星标", () => {
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
    const model = screen.getByTestId("assembly-model-block");
    expect(
      within(copyActions).getByRole("button", { name: "删除这份" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "删除写稿" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收成空白" })).toBeNull();
    expect(
      within(model).queryByRole("button", { name: "新建一份" }),
    ).toBeNull();
    expect(
      within(model).queryByRole("button", { name: "删除这份" }),
    ).toBeNull();
    expect(
      within(model).getByRole("button", { name: "以后新建也用这份" }),
    ).toBeTruthy();
    expect(screen.getByText("主模型")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "以后新建也用这份" }));
    expect(star).toHaveBeenCalledWith("mine");
    expect(screen.queryByText(/只对之后用这份装配新建的对话成立/)).toBeNull();
    expect(screen.queryByRole("switch", { name: "出厂" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "出厂目录" })).toBeNull();
  });

  it("配方还在且同名的落成行不另画，预置和自建同一套胶囊", () => {
    const chat = {
      id: "sys-chat",
      name: "极简",
      kind: "system" as const,
      is_default: false,
      recipe: "chat",
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const owned = {
      id: "owned-chat",
      name: "极简",
      kind: "user" as const,
      is_default: true,
      recipe: "chat",
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const renamed = {
      id: "renamed",
      name: "闲聊",
      kind: "user" as const,
      is_default: false,
      recipe: "chat",
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const released = {
      id: "released",
      name: "极简",
      kind: "user" as const,
      is_default: false,
      recipe: null,
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    const web = {
      id: "sys-web",
      name: "轻量",
      kind: "system" as const,
      is_default: false,
      recipe: "web",
      main: { origin: "platform" as const, provider_id: null, model: "flash" },
      enabled_mcp_server_ids: [],
    };
    editing.profile = owned;
    editing.profiles = [chat, web, owned, renamed, released];
    renderPage();

    expect(screen.queryByRole("group", { name: "预置" })).toBeNull();
    const presetChip = screen.getByRole("button", {
      name: "极简，新建对话用这份",
      pressed: true,
    });
    const idlePreset = screen.getByRole("button", { name: "轻量" });
    const ownedChip = screen.getByRole("button", { name: "闲聊" });
    expect(presetChip.parentElement).toBe(ownedChip.parentElement);
    expect(idlePreset.parentElement).toBe(ownedChip.parentElement);
    expect(idlePreset.className).toBe(ownedChip.className);
    expect(screen.getAllByRole("button", { name: /^极简$/ })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "删除这份" }));
    expect(screen.getByText(/预置还在这排/)).toBeTruthy();
  });

  it("再点当前胶囊就地改名，点另一颗仍是换份", () => {
    renderPage();
    fireEvent.click(
      screen.getByRole("button", { name: "GLM-5.2，新建对话用这份" }),
    );
    expect(select).not.toHaveBeenCalled();
    const input = screen.getByRole("textbox", { name: "装配名称" });
    expect((input as HTMLInputElement).value).toBe("GLM-5.2");
    fireEvent.change(input, { target: { value: "草稿" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(rename).toHaveBeenCalledWith("草稿");

    cleanup();
    rename.mockClear();
    select.mockClear();
    renderPage();
    fireEvent.click(
      screen.getByRole("button", { name: "GLM-5.2，新建对话用这份" }),
    );
    fireEvent.keyDown(screen.getByRole("textbox", { name: "装配名称" }), {
      key: "Escape",
    });
    expect(rename).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "GLM-5.2，新建对话用这份" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "写稿" }));
    expect(select).toHaveBeenCalledWith("mine");
  });

  it("新建和删除交给标题行，排在搜索左边", () => {
    editing.conversationId = "c1";
    editing.profile = mine;
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const renderNames = (names: ReactNode, actions?: ReactNode) => (
      <div data-testid="assembly-name-row">
        {names}
        <div data-testid="assembly-title-slot">{actions}</div>
        <input aria-label="搜提示词、工具" />
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
    const search = screen.getByRole("textbox", { name: "搜提示词、工具" });
    expect(
      slot.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("assembly-model-block")).getByRole("button", {
        name: "以后新建也用这份",
      }),
    ).toBeTruthy();
  });

  it("模型直接铺在名字下面，不进对话框", () => {
    renderPage();
    expect(screen.queryByRole("dialog", { name: "模型" })).toBeNull();
    expect(screen.queryByLabelText("名称")).toBeNull();
    expect(screen.queryByText("高级 · 其他模型")).toBeNull();
    expect(screen.queryByText("必填，下一回合生效")).toBeNull();
    expect(screen.queryByText("标题等")).toBeNull();
    expect(screen.queryByRole("button", { name: "恢复跟随" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
    const row = screen.getByTestId("assembly-model-row");
    expect(row.className).toContain("flex-wrap");
    const shorts = screen.getByTestId("assembly-model-shorts");
    expect(within(shorts).getByText("组队队员")).toBeTruthy();
    expect(within(shorts).getByText("后台任务")).toBeTruthy();
    expect(within(shorts).queryByText("辩论仍用主模型")).toBeNull();
  });
});
