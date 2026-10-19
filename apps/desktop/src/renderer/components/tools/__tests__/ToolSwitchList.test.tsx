// @vitest-environment jsdom
/**
 * 组装页节标题与全部打开 / 全部关闭同一行。输入区不传 showBulk。
 */

import { api } from "@/services/api";
import { __resetToolSwitchStoresForTests } from "@/services/toolSwitches";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ComponentProps, ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));
vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
}));
vi.mock("@/lib/assemblyWrite", () => ({
  refreshAssembliesAfterWrite: vi.fn(async () => undefined),
}));

import { ToolSwitchList } from "../ToolSwitchList";

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

function withQuery(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function renderList(
  props: Partial<ComponentProps<typeof ToolSwitchList>> = {},
) {
  return render(
    withQuery(
      <MemoryRouter>
        <ToolSwitchList scope="draft" showBulk {...props} />
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  __resetToolSwitchStoresForTests();
  get.mockReset();
  put.mockReset();
  get.mockResolvedValue(board([]));
  put.mockImplementation(async (_url: string, body: unknown) =>
    board((body as { disabled: string[] }).disabled),
  );
});

afterEach(cleanup);

describe("ToolSwitchList bulk", () => {
  it("都开时全部关闭写入当前这几把", async () => {
    renderList();
    expect(
      await screen.findByRole("button", { name: "全部关闭" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部打开" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "全部关闭" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/users/me/tool-switches", {
        disabled: ["files", "web"],
      });
    });
    expect(screen.getByRole("button", { name: "全部打开" })).toBeTruthy();
  });

  it("官方完整锁定时不拨、不出现全部开关", async () => {
    renderList({ locked: true, heading: "工具" });
    expect(await screen.findByRole("switch", { name: "改文件" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部关闭" })).toBeNull();
    expect(screen.queryByRole("button", { name: "全部打开" })).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "改文件" }));
    expect(put).not.toHaveBeenCalled();
  });

  it("节标题与全部打开同一行", async () => {
    get.mockResolvedValue(board(["files", "web"]));
    renderList({ heading: "工具" });
    const open = await screen.findByRole("button", { name: "全部打开" });
    const title = screen.getByRole("heading", { name: "工具" });
    expect(title.parentElement).toBe(open.parentElement?.parentElement);
  });

  it("都关后全部打开清空拒绝表", async () => {
    get.mockResolvedValue(board(["files", "web"]));
    renderList({ scope: "conversation", conversationId: "c1" });
    expect(
      await screen.findByRole("button", { name: "全部打开" }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部关闭" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "全部打开" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/conversations/c1/tool-switches", {
        disabled: [],
      });
    });
  });

  it("这场只看时全部打开仍不让改文件上台", async () => {
    get.mockResolvedValue(board(["web"]));
    renderList({
      scope: "conversation",
      conversationId: "c1",
      readOnlyBoundary: true,
    });
    expect(await screen.findByText("这场只看，不会上台")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "全部打开" }));
    await waitFor(() => {
      expect(put).toHaveBeenCalledWith("/v1/conversations/c1/tool-switches", {
        disabled: [],
      });
    });
    expect(screen.getByText("这场只看，不会上台")).toBeTruthy();
  });
});

function LocationProbe() {
  const loc = useLocation();
  return (
    <div data-testid="loc">{`${loc.pathname}${loc.search}${loc.hash}`}</div>
  );
}

describe("ToolSwitchList shelf", () => {
  it("一排开关卡，点卡留在本页", async () => {
    get.mockResolvedValue({
      disabled: [],
      switches: [
        {
          id: "files",
          label: "改文件",
          summary: "写",
          doc_tool: "write",
          off: false,
          note: null,
          tools: ["write", "edit"],
        },
      ],
    });
    render(
      withQuery(
        <MemoryRouter initialEntries={["/toolbox"]}>
          <Routes>
            <Route
              path="/toolbox"
              element={
                <>
                  <ToolSwitchList scope="draft" layout="shelf" />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>,
      ),
    );
    expect(await screen.findByRole("heading", { name: "改文件" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "写工作区文件" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "改工作区文件里的一段" }),
    ).toBeNull();
    expect(screen.queryByRole("heading", { name: "其余" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "改文件" }));
    expect(screen.getByTestId("loc").textContent).toBe(
      "/toolbox?tool=write#tools",
    );
  });
});
