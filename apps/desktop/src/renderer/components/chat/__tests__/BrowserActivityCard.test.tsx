// @vitest-environment jsdom
/**
 * L3「团队浏览器」M0 活动卡的渲染 + fold 单测：
 * - 聚合判定：≥2 连续 browser_* 步聚合成一卡，混入他工具 / 单步不聚合。
 * - 卡渲染：折叠态只留「浏览器 · N 步」标题，展开态出步骤列表（action/detail/url）。
 * - 含 frame 的回放重建：卡数据只来自随 tool_use_end 落 journal 的 display —— 给定重建后的
 *   process（display 带 frame），展开即按 conversationId + frame 懒拉原图、点击开 lightbox。
 * 块注释隔开 @vitest-environment 指令，让 organizeImports 保持它在文件首行。
 */

import type { ProcessStep } from "@/types/events";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

vi.mock("@/services/workspace", () => ({
  fetchWorkspaceFileBlob: vi.fn(),
}));

const showBrowser = vi.fn();
vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: typeof showBrowser }) => unknown) =>
      selector({ showBrowser }),
    { getState: () => ({ showBrowser }) },
  ),
}));

const hydrateConversation = vi.fn().mockResolvedValue(undefined);
vi.mock("@/stores/browserSessions", () => ({
  useBrowserSessionsStore: {
    getState: () => ({ hydrateConversation }),
  },
}));

import { isBrowserTool } from "@/lib/browserActivity";
import { fetchWorkspaceFileBlob } from "@/services/workspace";
import {
  BrowserActivityCard,
  BrowserResult,
  browserResultPeek,
  browserResultTail,
  isBrowserActivityGroup,
  isBrowserDisplay,
} from "../BrowserActivityCard";
import { ToolLineGroup } from "../ToolLine";

const mockFetch = vi.mocked(fetchWorkspaceFileBlob);

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

beforeAll(() => {
  // jsdom 不实现 createObjectURL/revoke —— 桩成稳定串，供 <img src> 渲染 + 卸载回收。
  URL.createObjectURL = vi.fn(
    () => "blob:mock-frame",
  ) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
});

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(new Blob(["jpeg-bytes"], { type: "image/jpeg" }));
  showBrowser.mockReset();
  hydrateConversation.mockClear();
});

afterEach(cleanup);

function browserStep(
  id: string,
  over: {
    action: string;
    url: string;
    title?: string;
    detail?: string;
    frame?: string;
    status?: ToolStep["status"];
    tool?: string;
    withDisplay?: boolean;
  },
): ToolStep {
  return {
    kind: "tool",
    id,
    tool_name: over.tool ?? `browser_${over.action}`,
    arguments: { url: over.url, action: over.action },
    result: "ok",
    display:
      over.withDisplay === false
        ? null
        : {
            kind: "browser",
            action: over.action,
            url: over.url,
            title: over.title,
            detail: over.detail,
            frame: over.frame,
          },
    status: over.status ?? "success",
  };
}

function otherStep(id: string): ToolStep {
  return {
    kind: "tool",
    id,
    tool_name: "file_write",
    arguments: { path: "a.txt", content: "x" },
    result: "已写入 a.txt",
    display: null,
    status: "success",
  };
}

describe("browser 聚合判定", () => {
  it("recognizes browser_* tool names", () => {
    expect(isBrowserTool("browser")).toBe(true);
    expect(isBrowserTool("browser_navigate")).toBe(true);
    expect(isBrowserTool("browser_screenshot")).toBe(true);
    expect(isBrowserTool("file_write")).toBe(false);
    expect(isBrowserTool("web_fetch")).toBe(false);
  });

  it("groups ≥2 consecutive all-browser steps, not mixed or single", () => {
    const nav = browserStep("b1", {
      action: "navigate",
      url: "https://ex.com",
    });
    const click = browserStep("b2", { action: "click", url: "https://ex.com" });
    expect(isBrowserActivityGroup([nav, click])).toBe(true);
    // 单步不聚合（走通用 ToolLine）。
    expect(isBrowserActivityGroup([nav])).toBe(false);
    // 混入非 browser 工具不聚合（走默认工具组 chevron）。
    expect(isBrowserActivityGroup([nav, otherStep("f1")])).toBe(false);
  });

  it("groups ≥2 unified browser(action=…) steps", () => {
    const nav = browserStep("b1", {
      action: "navigate",
      url: "https://ex.com",
      tool: "browser",
    });
    const click = browserStep("b2", {
      action: "click",
      url: "https://ex.com",
      tool: "browser",
    });
    expect(isBrowserActivityGroup([nav, click])).toBe(true);
  });

  it("narrows only a well-formed browser display", () => {
    expect(
      isBrowserDisplay({ kind: "browser", action: "click", url: "https://x" }),
    ).toBe(true);
    // web_fetch 形状（无 kind:"browser"）不误判。
    expect(isBrowserDisplay({ url: "https://x", content: "body" })).toBe(false);
    expect(isBrowserDisplay(null)).toBe(false);
    expect(isBrowserDisplay({ kind: "browser", action: "click" })).toBe(false);
  });
});

describe("browserResultPeek · 单步折叠一行", () => {
  it("tail prefers detail over title/url", () => {
    expect(
      browserResultTail({
        kind: "browser",
        action: "click",
        url: "https://ex.com",
        title: "Example",
        detail: "点击元素 e13",
      }),
    ).toBe("点击元素 e13");
  });

  it("prefers detail, falls back to title/url", () => {
    expect(
      browserResultPeek({
        kind: "browser",
        action: "navigate",
        url: "https://ex.com",
        detail: "打开示例首页",
      }),
    ).toBe("Navigate · 打开示例首页");
    expect(
      browserResultPeek({
        kind: "browser",
        action: "scroll",
        url: "https://ex.com/list",
      }),
    ).toBe("Scroll · https://ex.com/list");
  });
});

describe("BrowserActivityCard · 卡渲染", () => {
  const tools = [
    browserStep("b1", {
      action: "navigate",
      url: "https://example.com",
      detail: "打开示例站",
    }),
    browserStep("b2", {
      action: "click",
      url: "https://example.com/login",
      detail: "点击登录按钮",
    }),
  ];

  it("collapses to a count-title header, hiding step detail", () => {
    render(
      <BrowserActivityCard
        tools={tools}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("浏览器 · 2 步")).toBeTruthy();
    // 折叠态不平铺步骤明细。
    expect(screen.queryByText("打开示例站")).toBeNull();
    expect(screen.queryByText("点击登录按钮")).toBeNull();
  });

  it("expands into a step list with action / detail / url", () => {
    render(
      <BrowserActivityCard
        tools={tools}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    fireEvent.click(screen.getByText("浏览器 · 2 步"));
    expect(screen.getByText("Navigate")).toBeTruthy();
    expect(screen.getByText("Click")).toBeTruthy();
    expect(screen.getByText("打开示例站")).toBeTruthy();
    expect(screen.getByText("点击登录按钮")).toBeTruthy();
    expect(screen.getByText("https://example.com/login")).toBeTruthy();
  });

  it("keeps a running (no-display) step's slot from the call args", () => {
    const live = [
      browserStep("b1", {
        action: "navigate",
        url: "https://example.com",
        detail: "打开示例站",
      }),
      browserStep("b2", {
        action: "screenshot",
        url: "https://example.com",
        withDisplay: false,
        status: "running",
      }),
    ];
    render(
      <BrowserActivityCard
        tools={live}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    fireEvent.click(screen.getByText("浏览器 · 2 步"));
    // 无 display 的进行中步仍占一行（verb 由 tool_name 兜底）。
    expect(screen.getByText("Screenshot")).toBeTruthy();
  });

  it("live unified browser step takes verb from args.action, not slice(browser_)", () => {
    const live = [
      browserStep("b1", {
        action: "navigate",
        url: "https://example.com",
        tool: "browser",
        detail: "打开示例站",
      }),
      browserStep("b2", {
        action: "screenshot",
        url: "https://example.com",
        tool: "browser",
        withDisplay: false,
        status: "running",
      }),
    ];
    render(
      <BrowserActivityCard
        tools={live}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    fireEvent.click(screen.getByText("浏览器 · 2 步"));
    expect(screen.getByText("Screenshot")).toBeTruthy();
    expect(screen.queryByText("R")).toBeNull();
    expect(screen.queryByText(/^browser$/i)).toBeNull();
  });
});

describe("BrowserActivityCard · 含 frame 的回放重建", () => {
  const rebuilt = [
    browserStep("b1", {
      action: "navigate",
      url: "https://example.com",
      detail: "打开示例站",
      frame: "browser/step-0001.jpg",
    }),
    browserStep("b2", {
      action: "screenshot",
      url: "https://example.com",
      detail: "首页截图",
      frame: "browser/step-0002.jpg",
    }),
  ];

  it("lazy-loads each key-frame from the conversation workspace only on expand", async () => {
    render(
      <BrowserActivityCard
        tools={rebuilt}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    // 折叠态不拉图（懒加载）。
    expect(mockFetch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("浏览器 · 2 步"));

    await waitFor(() => {
      expect(screen.getByAltText("打开示例站")).toBeTruthy();
      expect(screen.getByAltText("首页截图")).toBeTruthy();
    });
    expect(mockFetch).toHaveBeenCalledWith("conv-1", "browser/step-0001.jpg");
    expect(mockFetch).toHaveBeenCalledWith("conv-1", "browser/step-0002.jpg");
  });

  it("opens a lightbox with the full frame on thumbnail click", async () => {
    render(
      <BrowserActivityCard
        tools={rebuilt}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    fireEvent.click(screen.getByText("浏览器 · 2 步"));
    const thumb = await screen.findByTitle("打开示例站");
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(thumb);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("BrowserResult · 单步富卡", () => {
  it("renders the action header + key-frame from display", async () => {
    render(
      <BrowserResult
        display={{
          kind: "browser",
          action: "navigate",
          url: "https://example.com",
          title: "示例站",
          detail: "打开示例站",
          frame: "browser/step-0001.jpg",
        }}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("Navigate")).toBeTruthy();
    expect(screen.getByText("打开示例站")).toBeTruthy();
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith("conv-1", "browser/step-0001.jpg"),
    );
  });

  it("shows 打开浏览器 CTA and reveals the browser tab on click", () => {
    render(
      <BrowserResult
        display={{
          kind: "browser",
          action: "navigate",
          url: "https://example.com",
          detail: "打开示例站",
        }}
        conversationId="conv-1"
      />,
    );
    const cta = screen.getByText("打开浏览器");
    expect(cta).toBeTruthy();
    fireEvent.click(cta);
    expect(showBrowser).toHaveBeenCalledTimes(1);
  });

  it("hides the browser CTA when conversationId is null", () => {
    render(
      <BrowserResult
        display={{
          kind: "browser",
          action: "click",
          url: "https://example.com",
          detail: "点击链接",
        }}
        conversationId={null}
      />,
    );
    expect(screen.queryByText("打开浏览器")).toBeNull();
  });

  it("shows a no-frame note when the step carries no key-frame", () => {
    render(
      <BrowserResult
        display={{
          kind: "browser",
          action: "click",
          url: "https://example.com",
          detail: "点击链接",
        }}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("（无关键帧）")).toBeTruthy();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("ToolLineGroup · browser 分派", () => {
  it("routes ≥2 browser steps to the activity card", () => {
    render(
      <ToolLineGroup
        tools={[
          browserStep("b1", { action: "navigate", url: "https://ex.com" }),
          browserStep("b2", { action: "click", url: "https://ex.com" }),
        ]}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("浏览器 · 2 步")).toBeTruthy();
  });

  it("routes ≥2 unified browser(action=…) steps to the activity card", () => {
    render(
      <ToolLineGroup
        tools={[
          browserStep("b1", {
            action: "navigate",
            url: "https://ex.com",
            tool: "browser",
          }),
          browserStep("b2", {
            action: "click",
            url: "https://ex.com",
            tool: "browser",
          }),
        ]}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("浏览器 · 2 步")).toBeTruthy();
  });

  it("does not fold a mixed browser+other group into the activity card", () => {
    render(
      <ToolLineGroup
        tools={[
          browserStep("b1", { action: "navigate", url: "https://ex.com" }),
          otherStep("f1"),
        ]}
        isStreaming={false}
        conversationId="conv-1"
      />,
    );
    expect(screen.queryByText(/浏览器 · \d+ 步/)).toBeNull();
  });
});
