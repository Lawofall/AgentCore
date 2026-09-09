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

  it("omits a section heading that already names the reader", () => {
    render(
      <PromptDocument
        compact={false}
        hideHeading="跨文件夹"
        text={`<跨文件夹>
派前认桌。
</跨文件夹>`}
      />,
    );
    expect(screen.queryByRole("heading", { name: "跨文件夹" })).toBeNull();
    expect(screen.getByText("派前认桌。")).toBeTruthy();
  });

  it("labels constitution tags in Chinese", () => {
    render(
      <PromptDocument
        compact={false}
        text={`<身份>
你是 CEO。
</身份>

<按需目录>
web_search — 搜索互联网
</按需目录>`}
      />,
    );
    expect(screen.getByRole("heading", { name: "身份" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "按需目录" })).toBeTruthy();
    expect(screen.getByText("你是 CEO。")).toBeTruthy();
  });
});
