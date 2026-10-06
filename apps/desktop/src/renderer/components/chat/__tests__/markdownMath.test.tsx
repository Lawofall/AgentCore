// @vitest-environment jsdom
/**
 * Chat Markdown math: remark-math dollars plus Claude `\(`/`\[` rewritten at render.
 */

import { Markdown } from "@/components/chat/Markdown";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("Markdown math", () => {
  it("renders dollar and tex-delimiter formulas through KaTeX", () => {
    const { container } = render(
      <Markdown content={"inline $a^2$ and \\(b^2\\) and\n\\[\nE=mc^2\n\\]"} />,
    );
    const math = container.querySelectorAll(".katex");
    expect(math.length).toBeGreaterThanOrEqual(3);
    expect(container.querySelector(".katex-display")).toBeTruthy();
    expect(container.textContent).toContain("E");
  });

  it("typesets a display formula that re-wraps a symbol", () => {
    const content = [
      "\\[",
      "\\operatorname{Delegate}(t,\\delta) \\iff \\begin{cases}",
      "\\forall i\\ \\operatorname{Unit}(i)，或整个 \\(t\\) 作为不可分单元；\\\\",
      "H(\\phi_t\\mid I_t)\\le\\varepsilon",
      "\\end{cases}",
      "\\]",
    ].join("\n");
    const { container } = render(<Markdown content={content} />);
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.querySelector(".katex-display")).toBeTruthy();
    expect(container.textContent).toContain("Delegate");
    expect(container.textContent).toContain("不可分单元");
  });

  it("does not typeset \\( inside a fenced code block", () => {
    const { container } = render(<Markdown content={"```\n\\(x^2\\)\n```"} />);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.querySelector("code")?.textContent).toContain("\\(x^2\\)");
  });
});
