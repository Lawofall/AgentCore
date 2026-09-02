// @vitest-environment jsdom
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("PromptDocument", () => {
  it("renders list items in structured view by default", () => {
    render(
      <PromptDocument
        text={`<output_style>
- 第一条
- 第二条
</output_style>`}
      />,
    );
    expect(screen.getByText("输出风格")).toBeTruthy();
    expect(screen.getByText("第一条")).toBeTruthy();
    expect(screen.getByText("第二条")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "输出风格" }).className,
    ).toContain("text-xs");
  });

  it("uses reading density when compact is false", () => {
    render(
      <PromptDocument
        compact={false}
        text={`<output_style>
- 第一条
</output_style>`}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "输出风格" }).className,
    ).toContain("text-sm");
  });
});
