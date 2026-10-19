import {
  type DefaultProviderGroup,
  PLATFORM_POINTER_ID,
  encodePointer,
} from "@/lib/llmDefaults";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render as rtlRender,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

function render(ui: Parameters<typeof rtlRender>[0]) {
  return rtlRender(<MemoryRouter>{ui}</MemoryRouter>);
}
import {
  ProfileModelSelect,
  canChooseFromGroups,
  catalogOfficialRate,
  catalogOptionRate,
  modelOptionCardFacts,
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

function enabledFrom(groups: DefaultProviderGroup[]): string[] {
  return groups.flatMap((g) =>
    g.models
      .filter((m) => !m.custom)
      .map((m) => encodePointer(g.providerId, m.model)),
  );
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

describe("canChooseFromGroups", () => {
  it("treats a live 平台 catalog as choosable without BYOK", () => {
    expect(
      canChooseFromGroups([
        {
          providerId: PLATFORM_POINTER_ID,
          providerLabel: "平台",
          models: [opt("deepseek-v4.1-flash", "DeepSeek V4.1 Flash")],
        },
      ]),
    ).toBe(true);
  });

  it("still treats a BYOK channel with an empty catalog as choosable (hand-fill)", () => {
    expect(
      canChooseFromGroups([
        {
          providerId: "p1",
          providerLabel: "DeepSeek",
          models: [],
        },
      ]),
    ).toBe(true);
  });

  it("rejects an orphan platform leftover", () => {
    expect(
      canChooseFromGroups([
        {
          providerId: PLATFORM_POINTER_ID,
          providerLabel: "当前模型",
          orphan: true,
          models: [opt("old", "old", { custom: true })],
        },
      ]),
    ).toBe(false);
  });
});

describe("ProfileModelSelect", () => {
  it("lists every channel in one searchable list", () => {
    const onChange = vi.fn();
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        enabledRefs={enabledFrom(groupsFixture())}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    const list = screen.getByRole("listbox");
    const options = within(list).getAllByRole("option");
    expect(options[0]?.textContent).toMatch(/DeepSeek V4 Flash/);
    expect(options[0]?.getAttribute("aria-selected")).toBe("false");
    expect(
      options.find((el) => el.getAttribute("aria-selected") === "true")
        ?.textContent,
    ).toMatch(/DeepSeek V4.1 Flash/);
    expect(
      within(list).getAllByRole("option", { name: /DeepSeek V4.1 Flash/ }),
    ).toHaveLength(2);
    expect(within(list).getByText("DeepSeek V4 Flash")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "渠道" })).toBeNull();
    fireEvent.click(
      within(list).getByRole("option", { name: /DeepSeek V4 Flash/ }),
    );
    expect(onChange).toHaveBeenCalledWith("@byok/p-go/deepseek-v4-flash");
  });

  it("does not draw channel chips", () => {
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
                price: {
                  cache_miss: "1",
                  output: "4",
                  currency: "CNY",
                  multiplier: "1",
                },
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
        enabledRefs={enabledFrom(groupsFixture())}
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
        enabledRefs={enabledFrom(groupsFixture())}
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
        enabledRefs={enabledFrom(groupsFixture())}
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
        enabledRefs={enabledFrom(groupsFixture())}
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

  it("keeps a hand-filled id as a checked row and edits it in place", () => {
    const onChange = vi.fn();
    const groups: DefaultProviderGroup[] = [
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [
          opt("flash", "Flash"),
          opt("ep-already", "ep-already", { custom: true }),
        ],
      },
    ];
    render(
      <ProfileModelSelect
        groups={groups}
        value="@byok/p1/ep-already"
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /ep-already/ }));
    const row = screen.getByRole("option", { name: /ep-already/ });
    expect(row.getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByLabelText("自定义 model id")).toBeNull();
    expect(
      screen.queryByRole("option", { name: /自定义 model id/ }),
    ).toBeNull();
    fireEvent.click(row);
    const input = screen.getByLabelText("自定义 model id") as HTMLInputElement;
    expect(input.value).toBe("ep-already");
    fireEvent.change(input, { target: { value: "ep-next" } });
    expect(onChange).toHaveBeenCalledWith("@byok/p1/ep-next");
  });

  it("filter miss is empty, not a custom id field", () => {
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        enabledRefs={enabledFrom(groupsFixture())}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    fireEvent.change(screen.getByLabelText("筛选模型"), {
      target: { value: "没有这个模型" },
    });
    expect(screen.getByText("无匹配模型")).toBeTruthy();
    expect(screen.queryByLabelText("自定义 model id")).toBeNull();
    expect(screen.getByRole("link", { name: "管理模型" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "使用其他模型" })).toBeNull();
  });

  it("keeps listed catalogs to 管理模型; empty BYOK is still the id field", () => {
    const single: DefaultProviderGroup[] = [
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [opt("flash", "Flash")],
      },
    ];
    render(
      <ProfileModelSelect
        groups={single}
        enabledRefs={["@byok/p1/flash"]}
        value="@byok/p1/flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Flash/ }));
    expect(screen.queryByRole("button", { name: "使用其他模型" })).toBeNull();
    expect(screen.queryByLabelText("自定义 model id")).toBeNull();
    expect(screen.getByRole("link", { name: "管理模型" })).toBeTruthy();
    cleanup();

    render(
      <ProfileModelSelect
        groups={[
          {
            providerId: "p1",
            providerLabel: "DeepSeek",
            models: [],
          },
        ]}
        value=""
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("自定义 model id")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "使用其他模型" })).toBeNull();
    cleanup();

    render(
      <ProfileModelSelect
        groups={[
          {
            providerId: PLATFORM_POINTER_ID,
            providerLabel: "平台",
            models: [opt("flash", "Flash", { origin: "platform" })],
          },
        ]}
        value="@platform/flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Flash/ }));
    expect(screen.queryByRole("button", { name: "使用其他模型" })).toBeNull();
    expect(screen.getByRole("link", { name: "管理模型" })).toBeTruthy();
  });

  it("treats persisted handFilled ids as 自定义 rows", () => {
    const onChange = vi.fn();
    const groups: DefaultProviderGroup[] = [
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [
          opt("flash", "Flash"),
          opt("deepseek-v4.1-flash", "deepseek-v4.1-flash", {
            handFilled: true,
          }),
        ],
      },
    ];
    render(
      <ProfileModelSelect
        groups={groups}
        enabledRefs={["@byok/p1/flash", "@byok/p1/deepseek-v4.1-flash"]}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /deepseek-v4.1-flash/ }),
    );
    const row = screen.getByRole("option", { name: /deepseek-v4.1-flash/ });
    expect(row.getAttribute("aria-selected")).toBe("true");
    expect(row.textContent).toContain("自定义");
    expect(screen.queryByLabelText("自定义 model id")).toBeNull();
    fireEvent.click(row);
    const input = screen.getByLabelText("自定义 model id") as HTMLInputElement;
    expect(input.value).toBe("deepseek-v4.1-flash");
    fireEvent.change(input, { target: { value: "deepseek-v4-pro" } });
    expect(onChange).toHaveBeenCalledWith("@byok/p1/deepseek-v4-pro");
  });

  it("keeps unopened provider rows out of the list except the current value", () => {
    render(
      <ProfileModelSelect
        groups={groupsFixture()}
        enabledRefs={[
          "@byok/p1/deepseek-v4.1-flash",
          "@byok/p-twin/deepseek-v4.1-flash",
        ]}
        value="@byok/p1/deepseek-v4.1-flash"
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /DeepSeek V4.1 Flash/ }),
    );
    const list = screen.getByRole("listbox");
    expect(
      within(list).getAllByRole("option", { name: /DeepSeek V4.1 Flash/ }),
    ).toHaveLength(2);
    expect(within(list).queryByText("DeepSeek V4 Flash")).toBeNull();
  });

  it("renders catalog unit prices in CNY, not a dollar sign", () => {
    const priced = opt("flash", "Flash", {
      vendor: "DeepSeek",
      price: {
        cache_miss: "1",
        output: "4",
        currency: "CNY",
        multiplier: "1",
      },
    });
    expect(modelOptionSecondary(priced)).toBe("DeepSeek · ¥1 / ¥4");
    expect(modelOptionSecondary(priced)).not.toContain("$");
  });

  it("keeps window and price, and drops capabilities plus a vendor already in the name", () => {
    const flash = opt("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", {
      vendor: "DeepSeek",
      contextLength: 1_000_000,
      capabilities: ["reasoning", "vision", "tools"],
      price: {
        cache_miss: "1",
        output: "4",
        currency: "CNY",
        multiplier: "1",
      },
    });
    expect(modelOptionSecondary(flash)).toBe("1M · ¥1 / ¥4");
    expect(modelOptionCardFacts(flash)).toBe("¥1 / ¥4");

    const gpt = opt("gpt-4o", "GPT-4o", {
      vendor: "OpenAI",
      contextLength: 128_000,
      capabilities: ["tools", "vision"],
      price: {
        cache_miss: "2.5",
        output: "10",
        currency: "CNY",
        multiplier: "1",
      },
    });
    expect(modelOptionSecondary(gpt)).toBe("OpenAI · 128K · ¥2.5 / ¥10");
    expect(modelOptionCardFacts(gpt)).toBe("OpenAI · ¥2.5 / ¥10");
  });

  it("does not dump the raw id when the badge is 平台", () => {
    const flash = opt("deepseek-v4-flash", "DeepSeek V4 Flash", {
      origin: "platform",
      badge: "平台",
      vendor: "DeepSeek",
      price: {
        cache_miss: "1",
        output: "4",
        currency: "CNY",
        multiplier: "1",
      },
    });
    expect(modelOptionSecondary(flash)).toBe("1 / 4 积分");
    expect(modelOptionSecondary(flash)).not.toContain("deepseek-v4-flash");
    expect(modelOptionCardFacts(flash)).toBe("");
  });

  it("puts official rate after the catalog name; platform card has no spend copy", () => {
    const flash = opt("deepseek-v4-flash", "DeepSeek V4 Flash", {
      origin: "platform",
      badge: "平台",
      vendor: "DeepSeek",
      price: {
        cache_miss: "0.25",
        output: "1",
        currency: "CNY",
        multiplier: "0.25",
        quota_multiplier: "1",
      },
    });
    expect(catalogOptionRate(flash)).toBe("0.25x");
    expect(catalogOfficialRate(flash)).toBe("0.25x");
    expect(modelOptionSecondary(flash)).toBe("0.25 / 1 积分");
    expect(modelOptionCardFacts(flash)).toBe("");
    expect(modelOptionSecondary(flash)).not.toContain("0.25x");

    const opus = opt("claude-opus-5-5", "Claude Opus 5.5", {
      origin: "platform",
      vendor: "Anthropic",
      price: {
        cache_miss: "8.64",
        output: "43.2",
        currency: "CNY",
        multiplier: "0.3",
        quota_multiplier: "41",
      },
    });
    expect(catalogOptionRate(opus)).toBe("0.3x");
    expect(catalogOfficialRate(opus)).toBe("0.3x");
    expect(modelOptionSecondary(opus)).not.toContain("41x");
    expect(modelOptionCardFacts(opus)).toBe("");

    const sol = opt("gpt-6.1-sol", "GPT-6.1 Sol", {
      origin: "platform",
      vendor: "OpenAI",
      price: {
        cache_miss: "2.592",
        output: "12.96",
        currency: "CNY",
        multiplier: "0.18",
        quota_multiplier: "12",
      },
    });
    expect(catalogOptionRate(sol)).toBe("0.18x");
    expect(modelOptionCardFacts(sol)).toBe("");

    const astra = opt("gpt-6-astra", "GPT-6 Astra", {
      origin: "platform",
      vendor: "OpenAI",
      price: {
        cache_miss: "12.96",
        output: "64.8",
        currency: "CNY",
        multiplier: "0.18",
        quota_multiplier: "62",
      },
    });
    expect(catalogOptionRate(astra)).toBe("0.18x");
    expect(modelOptionCardFacts(astra)).toBe("");
  });

  it("puts GLM-5.3 official markup after the catalog name, not on the card", () => {
    const glm = opt("glm-5.3", "GLM-5.3", {
      origin: "platform",
      badge: "平台",
      vendor: "智谱 AI",
      price: {
        cache_miss: "16",
        output: "56",
        currency: "CNY",
        multiplier: "2",
        quota_multiplier: "58",
      },
    });
    expect(catalogOptionRate(glm)).toBe("2x");
    expect(catalogOfficialRate(glm)).toBe("2x");
    expect(modelOptionSecondary(glm)).toBe("智谱 AI · 16 / 56 积分");
    expect(modelOptionCardFacts(glm)).toBe("");
  });

  it("does not write ×1 or BYOK as a catalog rate", () => {
    const listed = opt("glm-5.2", "GLM-5.2", {
      origin: "platform",
      price: {
        cache_miss: "1",
        output: "4",
        currency: "CNY",
        multiplier: "1",
      },
    });
    const byok = opt("flash", "Flash", {
      price: {
        cache_miss: "1",
        output: "4",
        currency: "CNY",
        multiplier: "0.5",
      },
    });
    expect(catalogOptionRate(listed)).toBeNull();
    expect(catalogOptionRate(byok)).toBeNull();
  });

  it("shows official rate after the name in the open list, not on the closed trigger", () => {
    render(
      <ProfileModelSelect
        compactTrigger
        groups={[
          {
            providerId: PLATFORM_POINTER_ID,
            providerLabel: "平台",
            models: [
              opt("claude-opus-5-5", "Claude Opus 5.5", {
                origin: "platform",
                vendor: "Anthropic",
                price: {
                  cache_miss: "8.64",
                  output: "43.2",
                  currency: "CNY",
                  multiplier: "0.3",
                  quota_multiplier: "41",
                },
              }),
            ],
          },
        ]}
        value="@platform/claude-opus-5-5"
        onChange={vi.fn()}
      />,
    );
    const trigger = screen.getByRole("button", { name: "Claude Opus 5.5" });
    expect(trigger.textContent).not.toContain("41x");
    expect(trigger.textContent).not.toContain("0.3x");
    fireEvent.click(trigger);
    const row = screen.getByRole("option", { name: /Claude Opus 5.5/ });
    expect(row.textContent).toContain("0.3x");
    expect(row.textContent).not.toContain("41x");
    expect(screen.getByText("此倍率相对该模型官网价")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "各模型积分价目" }));
    const table = screen.getByRole("table", { name: "各模型积分价目" });
    expect(table.textContent).toContain("41x");
    expect(table.textContent).toContain("8.64");
  });
});
