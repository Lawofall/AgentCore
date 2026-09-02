// @vitest-environment jsdom
/**
 * Regression ratchet for the mermaid「渲染失败」false positive + load-error typing.
 *
 * mermaid v11 inlines its theme stylesheet — which contains an `.error-icon{…}`
 * rule — into EVERY rendered diagram's <style>. The old success-check
 * `svg.includes("error-icon")` therefore matched 100% of *valid* charts and
 * rejected them all as「图表语法无效」. Here mermaid is mocked to return a healthy
 * flowchart SVG carrying exactly that CSS; the component MUST render it, not fall
 * back to source. The block comment keeps the @vitest-environment directive
 * file-leading past organizeImports.
 */

import {
  inlineMermaidBoxPx,
  mermaidInlineMaxHeightPx,
} from "@/lib/inlineMermaidBox";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DiagramBlock, __resetMermaidLoaderForTests } from "../Diagram";

const { mermaidApi, healthySvg } = vi.hoisted(() => {
  const healthySvg =
    '<svg id="acmmd-1" aria-roledescription="flowchart-v2" class="flowchart">' +
    "<style>#acmmd-1 .error-icon{fill:#552222;}</style>" +
    '<g class="node"><rect width="10" height="10" /></g></svg>';
  const mermaidApi = {
    initialize: vi.fn(),
    parse: vi.fn().mockResolvedValue(true),
    // Healthy render output — a real flowchart SVG whose inlined theme CSS
    // carries the `.error-icon` rule mermaid ships with every diagram.
    render: vi.fn().mockResolvedValue({ svg: healthySvg }),
  };
  return { mermaidApi, healthySvg };
});

vi.mock("mermaid", () => ({
  default: mermaidApi,
}));

afterEach(() => {
  cleanup();
  __resetMermaidLoaderForTests();
  mermaidApi.initialize.mockClear();
  mermaidApi.parse.mockClear();
  mermaidApi.parse.mockResolvedValue(true);
  mermaidApi.render.mockClear();
  mermaidApi.render.mockResolvedValue({ svg: healthySvg });
});

const sampleCode = 'flowchart LR\n  A["用户提问"] --> B["直答"]';

describe("MermaidDiagram · 合法图不得误报渲染失败", () => {
  it("renders a healthy SVG that contains .error-icon theme CSS", async () => {
    const { container } = render(
      <DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />,
    );

    // Success path injects mermaid's <svg class="flowchart"> into the card.
    await waitFor(() =>
      expect(container.querySelector("svg.flowchart")).not.toBeNull(),
    );
    // ...and never degrades to the CodeFallback「渲染失败」card.
    expect(screen.queryByText("渲染失败")).toBeNull();
  });

  it("drops mermaid's max-width cap so the card can scale the SVG up", async () => {
    mermaidApi.render.mockResolvedValueOnce({
      svg:
        '<svg class="flowchart" width="100%" height="120" style="max-width: 320px;" viewBox="0 0 320 120">' +
        "<style>#acmmd-1 .error-icon{fill:#552222;}</style></svg>",
    });
    const { container } = render(
      <DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />,
    );
    await waitFor(() =>
      expect(container.querySelector("svg.flowchart")).not.toBeNull(),
    );
    const svg = container.querySelector("svg.flowchart");
    expect(svg).not.toBeNull();
    if (!svg) throw new Error("expected flowchart svg");
    expect(svg.getAttribute("width")).toBe("320");
    expect(svg.getAttribute("style") ?? "").not.toMatch(/max-width/i);
    const wrap = svg.parentElement;
    expect(wrap).not.toBeNull();
    if (!wrap) throw new Error("expected mermaid wrap");
    // 320×120 already fits the preview ceiling; explicit box, no column stretch.
    expect(wrap.style.width).toBe("320px");
    expect(wrap.style.height).toBe("120px");
    expect(mermaidApi.initialize).toHaveBeenCalledWith(
      expect.objectContaining({
        flowchart: expect.objectContaining({
          nodeSpacing: 32,
          rankSpacing: 36,
          useMaxWidth: true,
        }),
      }),
    );
  });

  it("contain-fits a tall native SVG into an explicit pixel box", async () => {
    mermaidApi.render.mockResolvedValueOnce({
      svg:
        '<svg class="flowchart" width="200" height="1000" viewBox="0 0 200 1000">' +
        "<style>#acmmd-1 .error-icon{fill:#552222;}</style></svg>",
    });
    const { container } = render(
      <DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />,
    );
    await waitFor(() =>
      expect(container.querySelector("svg.flowchart")).not.toBeNull(),
    );
    const svg = container.querySelector("svg.flowchart");
    const wrap = svg?.parentElement;
    expect(wrap).not.toBeNull();
    if (!wrap) throw new Error("expected mermaid wrap");
    const host = wrap.parentElement;
    const rem = Number.parseFloat(
      getComputedStyle(document.documentElement).fontSize,
    );
    const expected = inlineMermaidBoxPx(
      200,
      1000,
      host?.clientWidth ?? 0,
      mermaidInlineMaxHeightPx(
        window.innerHeight,
        Number.isFinite(rem) && rem > 0 ? rem : 16,
      ),
    );
    expect(expected).not.toBeNull();
    if (!expected) throw new Error("expected contain box");
    expect(wrap.style.width).toBe(`${expected.w}px`);
    expect(wrap.style.height).toBe(`${expected.h}px`);
  });
});

describe("MermaidDiagram · 模块加载失败分型", () => {
  it("shows actionable load-failure copy instead of raw TypeError", async () => {
    __resetMermaidLoaderForTests({
      retryDelayMs: 0,
      load: async () => {
        throw new TypeError(
          "Failed to fetch dynamically imported module: http://localhost:5173/node_modules/.vite/deps/mermaid.js",
        );
      },
    });

    render(<DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />);

    await waitFor(() => expect(screen.getByText("渲染失败")).toBeTruthy());
    const failLabel = screen.getByText("渲染失败");
    expect(failLabel.className).toContain("text-muted-foreground");
    expect(failLabel.className).not.toContain("destructive");
    expect(screen.getByText(/图表引擎加载失败/)).toBeTruthy();
    expect(screen.getByText(/刷新页面/)).toBeTruthy();
    expect(
      screen.queryByText(/Failed to fetch dynamically imported module/),
    ).toBeNull();
  });

  it("resets a failed Promise so a later mount can retry successfully", async () => {
    let calls = 0;
    __resetMermaidLoaderForTests({
      retryDelayMs: 0,
      load: async () => {
        calls += 1;
        // First getMermaid() burns 3 attempts; all fail → promise nulled.
        if (calls <= 3) {
          throw new TypeError(
            "Failed to fetch dynamically imported module: http://localhost:5173/node_modules/.vite/deps/mermaid.js",
          );
        }
        return {
          default: mermaidApi as unknown as typeof import("mermaid").default,
        };
      },
    });

    const first = render(
      <DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/图表引擎加载失败/)).toBeTruthy(),
    );
    expect(calls).toBe(3);
    first.unmount();

    // Remount: rejected promise was reset, so import runs again and succeeds.
    const { container } = render(
      <DiagramBlock kind="mermaid" code={sampleCode} streaming={false} />,
    );
    await waitFor(() =>
      expect(container.querySelector("svg.flowchart")).not.toBeNull(),
    );
    expect(screen.queryByText("渲染失败")).toBeNull();
    expect(calls).toBe(4);
  });

  it("syntax errors stay distinct from module-load failures", async () => {
    mermaidApi.parse.mockRejectedValueOnce(new Error("Parse error on line 2"));

    render(
      <DiagramBlock
        kind="mermaid"
        code={"flowchart LR\n  A -->"}
        streaming={false}
      />,
    );

    await waitFor(() => expect(screen.getByText("渲染失败")).toBeTruthy());
    expect(screen.getByText(/Parse error on line 2/)).toBeTruthy();
    expect(screen.queryByText(/图表引擎加载失败/)).toBeNull();
  });
});
