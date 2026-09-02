// @vitest-environment jsdom
/**
 * Mermaid SVG display normalize: pin native px (drop useMaxWidth's max-width
 * cap) so the lightbox can contain-fit. jsdom required for DOMParser.
 */
import { describe, expect, it } from "vitest";
import {
  normalizeMermaidSvg,
  readMermaidSvgSize,
  readMermaidSvgWidth,
} from "../mermaidSvg";

describe("normalizeMermaidSvg", () => {
  it("replaces width=100% + max-width cap with native pixel size", () => {
    const input =
      '<svg width="100%" height="120" style="max-width: 320px;" viewBox="0 0 320 120" class="flowchart"></svg>';
    const doc = new DOMParser().parseFromString(
      normalizeMermaidSvg(input),
      "image/svg+xml",
    );
    const svg = doc.documentElement;
    expect(svg.getAttribute("width")).toBe("320");
    expect(svg.getAttribute("height")).toBe("120");
    expect(svg.getAttribute("viewBox")).toBe("0 0 320 120");
    expect(svg.getAttribute("style") ?? "").not.toMatch(/max-width/i);
  });

  it("does not treat width=100% as 100 pixels", () => {
    const input =
      '<svg width="100%" viewBox="0 0 480 90" style="max-width: 480px;"></svg>';
    const svg = new DOMParser().parseFromString(
      normalizeMermaidSvg(input),
      "image/svg+xml",
    ).documentElement;
    expect(svg.getAttribute("width")).toBe("480");
    expect(svg.getAttribute("height")).toBe("90");
  });

  it("leaves a healthy chart class intact (error-icon CSS must survive)", () => {
    const input =
      '<svg id="acmmd-1" class="flowchart"><style>#acmmd-1 .error-icon{fill:#552222;}</style><g></g></svg>';
    const out = normalizeMermaidSvg(input);
    const svg = new DOMParser().parseFromString(
      out,
      "image/svg+xml",
    ).documentElement;
    expect(svg.getAttribute("class")).toBe("flowchart");
    expect(out).toContain("error-icon");
  });

  it("passes through empty / non-svg input", () => {
    expect(normalizeMermaidSvg("")).toBe("");
    expect(normalizeMermaidSvg("<div>nope</div>")).toContain("nope");
  });
});

describe("readMermaidSvgSize", () => {
  it("reads the pinned pixel width after normalize", () => {
    const out = normalizeMermaidSvg(
      '<svg width="100%" style="max-width: 320px;" viewBox="0 0 320 120"></svg>',
    );
    expect(readMermaidSvgWidth(out)).toBe(320);
    expect(readMermaidSvgSize(out)).toEqual({ w: 320, h: 120 });
  });

  it("falls back to viewBox when height is omitted", () => {
    expect(
      readMermaidSvgSize(
        '<svg width="200" viewBox="0 0 200 1000" class="flowchart"></svg>',
      ),
    ).toEqual({ w: 200, h: 1000 });
  });
});
