// @vitest-environment jsdom
/**
 * Admin Markdown: fenced-code highlight + the PI-001 image downgrade.
 * Highlight is lowlight class spans (CSP-safe); images must never become <img>.
 */

import { Markdown } from "@/components/Markdown";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(() => {
  cleanup();
});

describe("Markdown", () => {
  it("caps the body so an unbreakable token cannot blow the replay column", () => {
    const { container } = render(
      <Markdown content={"1000+500+50+".repeat(40)} />,
    );
    const body = container.querySelector(".markdown-body");
    expect(body?.className).toContain("min-w-0");
    expect(body?.className).toContain("max-w-full");
  });

  it("tokenizes a fenced code block with highlight.js classes", () => {
    render(
      <Markdown content={"```python\ndef greet():\n    return 'hi'\n```"} />,
    );
    const code = document.querySelector("pre code");
    expect(code).not.toBeNull();
    expect(code?.className).toMatch(/language-python/);
    expect(code?.querySelector(".hljs-keyword")).not.toBeNull();
    expect(code?.textContent).toContain("def");
  });

  it("does not throw on an unknown fence language", () => {
    expect(() =>
      render(<Markdown content={"```not-a-real-lang\nfoo bar\n```"} />),
    ).not.toThrow();
    expect(document.querySelector("pre code")?.textContent).toContain("foo bar");
  });

  it("renders mermaid fences as code, not a diagram", () => {
    render(<Markdown content={"```mermaid\ngraph TD; A-->B;\n```"} />);
    expect(document.querySelector("svg")).toBeNull();
    expect(document.querySelector("pre code")?.textContent).toContain("graph TD");
  });

  it("downgrades images to links and never mounts img (PI-001)", () => {
    render(<Markdown content={"![screenshot](https://evil.example/x.png)"} />);
    expect(document.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: "screenshot" });
    expect((link as HTMLAnchorElement).href).toBe("https://evil.example/x.png");
    expect((link as HTMLAnchorElement).target).toBe("_blank");
  });
});
