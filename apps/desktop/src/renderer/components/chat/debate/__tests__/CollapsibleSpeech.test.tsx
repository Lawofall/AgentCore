// @vitest-environment jsdom
/**
 * 折叠壳的溢出判定必须与「当前是否展开」无关。
 *
 * 回归锁（长文不能收起）：旧实现量的是当前渲染高度差（`scrollHeight - clientHeight`），展开态下
 * 容器没有夹层、两者恒等 → 判定「不溢出」→「展开全文 / 收起」整枚按钮不渲染。而 `sceneKey` 会把
 * 用户点过的展开态记住，于是气泡一旦重挂载（切对话回来 / 编辑取消 / 重启）就以展开态
 * 首渲染，长文永久全展、再也收不起来。
 */
import {
  CollapsibleSpeech,
  USER_BUBBLE_COLLAPSED_MAX_H,
} from "@/components/chat/debate/CollapsibleSpeech";
import { useDisclosureStore } from "@/stores/disclosure";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: "c1" }),
}));

/** jsdom 不排版：按「是否被 maxHeight 夹住」模拟浏览器的高度语义。 */
function stubLayout(contentHeight: number): void {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return contentHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: HTMLElement) {
      const clamp = Number.parseFloat(this.style.maxHeight);
      return Number.isFinite(clamp)
        ? Math.min(clamp, contentHeight)
        : contentHeight;
    },
  });
}

beforeEach(() => {
  useDisclosureStore.setState({ map: {} });
  stubLayout(400);
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const LONG = "很长的一段话\n".repeat(40);

function renderBubble() {
  return render(
    <CollapsibleSpeech
      contentKey={LONG}
      collapsedMaxH={USER_BUBBLE_COLLAPSED_MAX_H}
      sceneKey="user:m1"
    >
      <p>{LONG}</p>
    </CollapsibleSpeech>,
  );
}

/** 夹层容器（maxHeight + 渐隐所在层）= 组件根的第一个子节点。 */
function clampEl(root: HTMLElement): HTMLElement {
  const el = root.firstElementChild?.firstElementChild;
  if (!(el instanceof HTMLElement))
    throw new Error("clamp container not found");
  return el;
}

describe("CollapsibleSpeech", () => {
  it("超长内容首挂载夹住，可展开再收起", () => {
    const { container } = renderBubble();
    expect(clampEl(container).style.maxHeight).toBe(
      `${USER_BUBBLE_COLLAPSED_MAX_H}px`,
    );

    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
    expect(clampEl(container).style.maxHeight).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
    expect(clampEl(container).style.maxHeight).toBe(
      `${USER_BUBBLE_COLLAPSED_MAX_H}px`,
    );
  });

  it("展开态下重挂载仍能收起（持久化展开 + 切对话回来 / 编辑取消 / 重启）", () => {
    const first = renderBubble();
    fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
    first.unmount();

    renderBubble();
    const collapse = screen.getByRole("button", { name: "收起" });
    fireEvent.click(collapse);
    expect(screen.getByRole("button", { name: "展开全文" })).toBeTruthy();
  });

  it("短内容不夹、不长按钮", () => {
    stubLayout(80);
    render(
      <CollapsibleSpeech contentKey="短" collapsedMaxH={144} sceneKey="user:m2">
        <p>短</p>
      </CollapsibleSpeech>,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});
