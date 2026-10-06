// @vitest-environment jsdom
/**
 * 工具开关跟权限、模型一样：这场一份；没这场时记草稿；设为默认才写账户。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { notifySuccess } from "@/lib/toast";
import { api } from "@/services/api";
import { __resetToolSwitchStoresForTests } from "@/services/toolSwitches";
import { useConversationStore } from "@/stores/conversation";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

const conversations = vi.hoisted(() => ({
  rows: [] as {
    id: string;
    title: string;
    disabledTools?: string[];
    permissionAxes?: { boundary: "read" | "folder" | "computer" };
  }[],
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => conversations.rows,
  patchConversationCache: vi.fn(),
}));

import { ConversationToolSwitchChip } from "../ConversationToolSwitchChip";

const get = vi.mocked(api.get);
const put = vi.mocked(api.put);

function board(disabled: string[]) {
  const off = new Set(disabled);
  return {
    disabled,
    switches: [
      {
        id: "files",
        label: "改文件",
        summary: "写",
        doc_tool: "write",
        off: off.has("files"),
        note: null,
      },
      {
        id: "web",
        label: "上网",
        summary: "搜",
        doc_tool: "web_search",
        off: off.has("web"),
        note: null,
      },
    ],
  };
}

function renderChip() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ConversationToolSwitchChip />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  __resetToolSwitchStoresForTests();
  conversations.rows = [];
  useConversationStore.setState({ currentConversationId: null });
  get.mockReset();
  put.mockReset();
  vi.mocked(notifySuccess).mockReset();
  get.mockImplementation(async (url: string) => {
    if (url.endsWith("/users/me/tool-switches")) return board([]);
    return board(["web"]);
  });
  put.mockImplementation(async (_url: string, body: unknown) =>
    board((body as { disabled: string[] }).disabled),
  );
});

afterEach(cleanup);

describe("ConversationToolSwitchChip", () => {
  it("没这场时拨开关写星标装配", async () => {
    renderChip();
    fireEvent.click(screen.getByLabelText("工具"));
    const web = await screen.findByRole("switch", { name: "上网" });
    expect(screen.queryByRole("button", { name: "全部关闭" })).toBeNull();
    expect(screen.queryByRole("button", { name: "全部打开" })).toBeNull();
    fireEvent.click(web);
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/users/me/tool-switches", {
        disabled: ["web"],
      });
    });
  });

  it("设为新会话默认把这场的名单写到星标装配", async () => {
    conversations.rows = [{ id: "c1", title: "周报", disabledTools: ["web"] }];
    useConversationStore.setState({ currentConversationId: "c1" });
    renderChip();
    fireEvent.click(screen.getByLabelText("工具"));
    fireEvent.click(
      await screen.findByRole("button", { name: "设为新会话默认" }),
    );
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/users/me/tool-switches", {
        disabled: ["web"],
      });
    });
    expect(notifySuccess).toHaveBeenCalledWith("新会话将默认关掉 1 样");
  });

  it("已有这场时拨开关写这场，和默认不一样就写在徽章上", async () => {
    conversations.rows = [
      {
        id: "c1",
        title: "周报",
        disabledTools: ["web"],
        permissionAxes: { boundary: "read" },
      },
    ];
    useConversationStore.setState({ currentConversationId: "c1" });
    renderChip();
    await waitFor(() => {
      expect(screen.getByLabelText("工具：这场关了 1 样")).toBeTruthy();
    });
    fireEvent.click(screen.getByLabelText("工具：这场关了 1 样"));
    expect(await screen.findByText("这场只看，不会上台")).toBeTruthy();
    expect(screen.getByText(/改的是「周报」/)).toBeTruthy();
    fireEvent.click(screen.getByRole("switch", { name: "改文件" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/conversations/c1/tool-switches", {
        disabled: ["web", "files"],
      });
    });
  });
});
