// @vitest-environment jsdom
import { PromptWorkbench } from "@/components/prompt/PromptWorkbench";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let lastEditorProps: {
  onChange?: (value: string) => void;
  onSave?: () => void;
  initialDoc?: string;
  editable?: boolean;
};
let editorValue = "";

vi.mock("@/components/markdown/MarkdownSourceEditor", async () => {
  const React = await import("react");
  return {
    MarkdownSourceEditor: React.forwardRef(function Stub(
      props: {
        onChange?: (value: string) => void;
        onSave?: () => void;
        initialDoc?: string;
        editable?: boolean;
      },
      ref: React.Ref<unknown>,
    ) {
      lastEditorProps = props;
      React.useImperativeHandle(ref, () => ({
        getValue: () => editorValue,
        getView: () => null,
        getSelectionContext: () => null,
        startRewriteReview: () => false,
        endRewriteReview: () => undefined,
      }));
      return React.createElement("div", { "data-testid": "cm-stub" });
    }),
  };
});
vi.mock("@/components/markdown/sourceToolbar", () => ({
  SourceToolbar: () => null,
}));

afterEach(() => {
  cleanup();
  editorValue = "";
});

describe("PromptWorkbench", () => {
  it("可写直接出源码，没有预览切换", () => {
    render(
      <PromptWorkbench title="团队拆法" initialBody="<团队拆法>\n派单。\n" />,
    );
    expect(screen.queryByRole("button", { name: "预览" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.getByTestId("cm-stub")).toBeTruthy();
    expect(lastEditorProps.editable).not.toBe(false);
  });

  it("只读直接出源码，没有编辑切换", () => {
    render(
      <PromptWorkbench
        title="全员共享准则"
        initialBody="共享准则正文"
        readOnly
      />,
    );
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "预览" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "全员共享准则" })).toBeNull();
    expect(screen.queryByLabelText("名称")).toBeNull();
    expect(screen.getByTestId("cm-stub")).toBeTruthy();
    expect(lastEditorProps.editable).toBe(false);
    expect(lastEditorProps.initialDoc).toBe("共享准则正文");
  });

  it("停敲后自动保存，手动保存取消待触发的自动存", async () => {
    vi.useFakeTimers();
    const onSave = vi.fn(async () => true);
    try {
      render(
        <PromptWorkbench
          title="团队拆法"
          initialBody="old"
          initialTrigger="团队拆法"
          onSave={onSave}
        />,
      );
      editorValue = "old";

      editorValue = "v1";
      act(() => lastEditorProps.onChange?.("v1"));
      editorValue = "v2";
      act(() => lastEditorProps.onChange?.("v2"));
      expect(onSave).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onSave).toHaveBeenCalledWith({
        title: "团队拆法",
        trigger: "团队拆法",
        body: "v2",
      });

      editorValue = "v3";
      act(() => lastEditorProps.onChange?.("v3"));
      fireEvent.click(screen.getByRole("button", { name: "保存" }));
      await act(async () => {});
      expect(onSave).toHaveBeenCalledTimes(2);
      expect(onSave).toHaveBeenLastCalledWith({
        title: "团队拆法",
        trigger: "团队拆法",
        body: "v3",
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(onSave).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("目录句始终可见，与标题相同也不藏，源码面上就能改", () => {
    render(
      <PromptWorkbench
        title="团队拆法"
        titleEditable
        initialBody="body"
        initialTrigger="团队拆法"
        onSave={async () => true}
      />,
    );
    expect(screen.queryByRole("heading", { name: "团队拆法" })).toBeNull();
    expect(screen.getByLabelText("名称")).toHaveProperty("value", "团队拆法");
    expect(screen.getByText("一句话介绍")).toBeTruthy();
    const line = screen.getByLabelText("一句话介绍");
    expect(line).toHaveProperty("value", "团队拆法");
    expect(line.getAttribute("placeholder")).toBe("用一句话说这是什么");
    expect(line.parentElement?.className).toContain("flex");
    expect(line.parentElement?.className).toContain("items-baseline");
    expect(screen.getByTestId("cm-stub")).toBeTruthy();
  });

  it("目录句与标题不同时两句都在", () => {
    render(
      <PromptWorkbench
        title="团队拆法"
        titleEditable
        initialBody="怎么派"
        initialTrigger="派子队、拆里程碑时用"
        onSave={async () => true}
      />,
    );
    expect(screen.queryByRole("heading", { name: "团队拆法" })).toBeNull();
    expect(screen.getByLabelText("名称")).toHaveProperty("value", "团队拆法");
    expect(screen.getByLabelText("一句话介绍")).toHaveProperty(
      "value",
      "派子队、拆里程碑时用",
    );
  });

  it("常驻档出加载开关、不出目录句", () => {
    const onApplyModeChange = vi.fn();
    render(
      <PromptWorkbench
        title="短约束"
        initialBody="要短"
        applyMode="always"
        onApplyModeChange={onApplyModeChange}
        initialTrigger="短约束"
      />,
    );
    expect(screen.queryByRole("heading", { name: "短约束" })).toBeNull();
    expect(screen.getByRole("tablist", { name: "加载方式" })).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: "常驻" }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(screen.queryByLabelText("一句话介绍")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "按需" }));
    expect(onApplyModeChange).toHaveBeenCalledWith("on_demand");
  });

  it("官方槽可改目录句，不出加载档", () => {
    render(
      <PromptWorkbench
        title="派单进阶"
        initialBody="HOW"
        initialTrigger="派单进阶"
      />,
    );
    expect(screen.queryByRole("heading", { name: "派单进阶" })).toBeNull();
    expect(screen.queryByLabelText("名称")).toBeNull();
    expect(screen.queryByRole("switch", { name: "按需目录" })).toBeNull();
    expect(screen.queryByRole("tablist", { name: "加载方式" })).toBeNull();
    expect(screen.getByLabelText("一句话介绍")).toHaveProperty(
      "value",
      "派单进阶",
    );
  });

  it("只读官方 HOW 不画封面大标题", () => {
    render(
      <PromptWorkbench
        title="途中提问"
        initialBody={"<途中提问>\n问。\n</途中提问>"}
        readOnly
      />,
    );
    expect(screen.queryByRole("heading", { name: "途中提问" })).toBeNull();
    expect(screen.queryByLabelText("名称")).toBeNull();
    expect(screen.queryByLabelText("一句话介绍")).toBeNull();
    expect(lastEditorProps.initialDoc).toContain("<途中提问>");
  });
});
