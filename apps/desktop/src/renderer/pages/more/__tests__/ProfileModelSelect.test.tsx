import type { DefaultProviderGroup } from "@/lib/llmDefaults";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ProfileModelSelect,
  buildChannelPickerSections,
  defaultBrowseProviderId,
  modelOptionSecondary,
} from "../ProfileModelSelect";

afterEach(cleanup);

function opt(
  model: string,
  label: string,
  extra: Partial<DefaultProviderGroup["models"][number]> = {},
): DefaultProviderGroup["models"][number] {
  return { model, label, available: true, ...extra };
}

function groupsFixture(): DefaultProviderGroup[] {
  return [
    {
      providerId: "p1",
      providerLabel: "DeepSeek",
      models: [opt("deepseek-v4.1-flash", "DeepSeek V4.1 Flash")],
    },
    {
      providerId: "p-twin",
      providerLabel: "另一家",
      models: [opt("deepseek-v4.1-flash", "DeepSeek V4.1 Flash")],
    },
    {
      providerId: "p-go",
      providerLabel: "OpenCode Go",
      models: [opt("deepseek-v4-flash", "DeepSeek V4 Flash")],
    },
  ];
}

describe("defaultBrowseProviderId", () => {
  it("follows a live pointer and skips an orphan current channel", () => {
    const groups: DefaultProviderGroup[] = [
      {
        providerId: "gone",
        providerLabel: "已移除的服务商",
        orphan: true,
        models: [opt("old", "old", { custom: true })],
      },
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [opt("flash", "Flash")],
      },
    ];
    expect(defaultBrowseProviderId(groups, "@byok/p1/flash")).toBe("p1");
    expect(defaultBrowseProviderId(groups, "@byok/gone/old")).toBe("p1");
    expect(defaultBrowseProviderId(groups, "")).toBe("p1");
  });
});

describe("buildChannelPickerSections", () => {
  const groups = groupsFixture();

  it("lists only the browsed channel until a filter miss", () => {
    const scoped = buildChannelPickerSections(groups, "p1", "");
    expect(scoped).toHaveLength(1);
    expect(scoped[0]?.group.providerId).toBe("p1");
    expect(scoped[0]?.models.map((m) => m.model)).toEqual([
      "deepseek-v4.1-flash",
    ]);
    expect(scoped[0]?.showCustom).toBe(true);
    expect(scoped[0]?.fallback).toBe(false);
  });

  it("does not surface the other channel's same-named model while the current channel still matches", () => {
    const sections = buildChannelPickerSections(groups, "p1", "v4.1");
    expect(sections).toHaveLength(1);
    expect(sections[0]?.group.providerId).toBe("p1");
    expect(sections.some((s) => s.group.providerId === "p-twin")).toBe(false);
  });

  it("falls back to other channels only when the current channel has no hit", () => {
    const sections = buildChannelPickerSections(groups, "p-go", "v4.1");
    expect(sections[0]?.group.providerId).toBe("p-go");
    expect(sections[0]?.models).toHaveLength(0);
    const fallback = sections.filter((s) => s.fallback);
    expect(fallback.map((s) => s.group.providerId)).toEqual(["p1", "p-twin"]);
  });
});

describe("ProfileModelSelect", () => {
  it("hides the other channel's same-named model until that chip is pressed", () => {
    const onChange = vi.fn();
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    const list = screen.getByRole("listbox");
    expect(
      within(list).getAllByRole("option", { name: /DeepSeek V4.1 Flash/ }),
    ).toHaveLength(1);
    expect(within(list).queryByText("DeepSeek V4 Flash")).toBeNull();
    expect(
      screen.getByRole("button", { name: "DeepSeek", pressed: true }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "另一家" }));
    expect(within(list).getByText("DeepSeek V4.1 Flash")).toBeTruthy();
    expect(within(list).queryByText("DeepSeek V4 Flash")).toBeNull();
    expect(
      screen.getByRole("button", { name: "另一家", pressed: true }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "OpenCode Go" }));
    expect(within(list).getByText("DeepSeek V4 Flash")).toBeTruthy();
    expect(within(list).queryByText("DeepSeek V4.1 Flash")).toBeNull();
  });

  it("offers other-channel hits under 当前渠道无匹配 when the filter misses", () => {
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        value="@byok/p-go/deepseek-v4-flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek V4 Flash/ }));
    fireEvent.change(screen.getByLabelText("筛选模型"), {
      target: { value: "v4.1" },
    });
    expect(screen.getByText("当前渠道无匹配")).toBeTruthy();
    const list = screen.getByRole("listbox");
    expect(within(list).getByText("DeepSeek")).toBeTruthy();
    expect(
      within(list).getAllByRole("option", { name: /DeepSeek V4.1 Flash/ })
        .length,
    ).toBeGreaterThan(0);
  });

  it("does not draw channel chips when there is only one group", () => {
    render(
      <ProfileModelSelect
        groups={[
          {
            providerId: "p1",
            providerLabel: "DeepSeek",
            models: [opt("flash", "Flash")],
          },
        ]}
        value="@byok/p1/flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Flash/ }));
    expect(screen.queryByRole("group", { name: "渠道" })).toBeNull();
    expect(screen.getByRole("option", { name: /Flash/ })).toBeTruthy();
  });

  it("keeps window and price on the open row when the trigger is one line", () => {
    render(
      <ProfileModelSelect
        compactTrigger
        groups={[
          {
            providerId: "p1",
            providerLabel: "DeepSeek",
            models: [
              opt("flash", "Flash", {
                vendor: "DeepSeek",
                contextLength: 1_000_000,
                price: { cache_miss: "1", output: "4", currency: "CNY" },
              }),
            ],
          },
        ]}
        value="@byok/p1/flash"
        onChange={vi.fn()}
      />,
    );
    const trigger = screen.getByRole("button", { name: "Flash" });
    expect(trigger.textContent).not.toContain("1M");
    expect(trigger.textContent).not.toContain("¥");
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: /1M/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /¥1 \/ ¥4/ })).toBeTruthy();
  });

  it("pins 跟随主模型 above the catalog and writes an empty slot", () => {
    const onChange = vi.fn();
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        value="@byok/p1/deepseek-v4.1-flash"
        followLabel="跟随主模型"
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    const follow = screen.getByRole("option", { name: "跟随主模型" });
    expect(follow.getAttribute("aria-selected")).toBe("false");
    fireEvent.change(screen.getByLabelText("筛选模型"), {
      target: { value: "没有这个模型" },
    });
    expect(
      screen.queryByRole("option", { name: /DeepSeek V4.1 Flash/ }),
    ).toBeNull();
    expect(screen.getByRole("option", { name: "跟随主模型" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "跟随主模型" }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("marks 跟随主模型 selected when the slot is empty", () => {
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        value=""
        followLabel="跟随主模型"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "跟随主模型" }));
    expect(
      screen
        .getByRole("option", { name: "跟随主模型" })
        .getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("opens a 24rem menu on the trigger's end for short slots", () => {
    render(
      <ProfileModelSelect
        wideMenu
        groups={groupsFixture()}
        value=""
        followLabel="跟随主模型"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "跟随主模型" }));
    const panel = screen.getByTestId("model-picker-panel");
    expect(panel.className).toContain("w-96");
    expect(panel.className).toContain("right-0");
  });

  it("keeps the main-model menu as wide as its trigger", () => {
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    const panel = screen.getByTestId("model-picker-panel");
    expect(panel.className).toContain("w-full");
    expect(panel.className).not.toContain("w-96");
    expect(screen.queryByRole("option", { name: "跟随主模型" })).toBeNull();
  });

  it("renders catalog unit prices in CNY, not a dollar sign", () => {
    const priced = opt("flash", "Flash", {
      vendor: "DeepSeek",
      price: { cache_miss: "1", output: "4", currency: "CNY" },
    });
    expect(modelOptionSecondary(priced)).toBe("DeepSeek · ¥1 / ¥4");
    expect(modelOptionSecondary(priced)).not.toContain("$");
  });
});
