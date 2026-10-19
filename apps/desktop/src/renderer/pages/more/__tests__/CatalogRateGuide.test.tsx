// @vitest-environment jsdom
import { PLATFORM_POINTER_ID } from "@/lib/llmDefaults";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  CatalogRateLegend,
  catalogRateGuideOptions,
  catalogRateGuideRows,
  catalogRateGuideShowsPeakNote,
  catalogShowsRateLegend,
} from "../CatalogRateGuide";

afterEach(cleanup);

function platform(
  model: string,
  label: string,
  price: {
    cache_miss?: string;
    output?: string;
    multiplier: string;
    quota_multiplier?: string;
  },
) {
  return {
    model,
    label,
    available: true,
    origin: "platform" as const,
    price: { currency: "CNY" as const, ...price },
  };
}

const flash = platform("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", {
  cache_miss: "0.25",
  output: "1",
  multiplier: "0.25",
  quota_multiplier: "1",
});
const opus = platform("claude-opus-5-5", "Claude Opus 5.5", {
  cache_miss: "8.64",
  output: "43.2",
  multiplier: "0.3",
  quota_multiplier: "41",
});
const glm = platform("glm-5.2", "GLM-5.2", {
  cache_miss: "1",
  output: "4",
  multiplier: "1",
  quota_multiplier: "1",
});

const platformGroup = {
  providerId: PLATFORM_POINTER_ID,
  providerLabel: "平台",
  models: [flash, opus, glm],
};

describe("catalogRateGuideOptions", () => {
  it("keeps platform priced rows and skips BYOK", () => {
    const options = catalogRateGuideOptions([
      platformGroup,
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [
          {
            model: "flash",
            label: "Flash",
            available: true,
            price: {
              currency: "CNY",
              cache_miss: "1",
              output: "4",
              multiplier: "0.5",
            },
          },
        ],
      },
    ]);
    expect(options.map((opt) => opt.model)).toEqual([
      "deepseek-v4.1-flash",
      "claude-opus-5-5",
      "glm-5.2",
    ]);
  });

  it("drops hidden platform refs", () => {
    expect(
      catalogRateGuideOptions([platformGroup], {
        hiddenRefs: ["@platform/claude-opus-5-5"],
      }).map((opt) => opt.model),
    ).toEqual(["deepseek-v4.1-flash", "glm-5.2"]);
  });
});

describe("catalogRateGuideRows", () => {
  it("writes official rate, per-million cells, and default-relative spend", () => {
    expect(catalogRateGuideRows([flash, opus, glm])).toEqual([
      {
        key: "deepseek-v4.1-flash",
        label: "DeepSeek V4.1 Flash",
        officialRate: "0.25x",
        input: "0.25",
        output: "1",
        vsDefault: "以此为 1",
      },
      {
        key: "claude-opus-5-5",
        label: "Claude Opus 5.5",
        officialRate: "0.3x",
        input: "8.64",
        output: "43.2",
        vsDefault: "41x",
      },
      {
        key: "glm-5.2",
        label: "GLM-5.2",
        officialRate: "1x",
        input: "1",
        output: "4",
        vsDefault: "以此为 1",
      },
    ]);
  });

  it("notes DeepSeek peak only when Flash is in the table", () => {
    expect(catalogRateGuideShowsPeakNote([flash, opus])).toBe(true);
    expect(catalogRateGuideShowsPeakNote([opus])).toBe(false);
  });
});

describe("CatalogRateLegend", () => {
  it("hides when every platform row is ×1 or BYOK", () => {
    expect(catalogShowsRateLegend([{ ...platformGroup, models: [glm] }])).toBe(
      false,
    );
    render(
      <CatalogRateLegend
        groups={[
          {
            providerId: "p1",
            providerLabel: "DeepSeek",
            models: [
              {
                model: "flash",
                label: "Flash",
                available: true,
                price: {
                  currency: "CNY",
                  multiplier: "0.5",
                  cache_miss: "1",
                  output: "4",
                },
              },
            ],
          },
        ]}
      />,
    );
    expect(screen.queryByText("此倍率相对该模型官网价")).toBeNull();
  });

  it("opens the comparison table from the question mark", () => {
    render(<CatalogRateLegend groups={[platformGroup]} />);
    expect(screen.getByText("此倍率相对该模型官网价")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "各模型积分价目" }));
    const table = screen.getByRole("table", { name: "各模型积分价目" });
    expect(table.textContent).toContain("每百万输入");
    expect(table.textContent).toContain("每百万输出");
    expect(table.textContent).toContain("0.3x");
    expect(table.textContent).toContain("8.64");
    expect(table.textContent).toContain("43.2");
    expect(table.textContent).toContain("41x");
    expect(table.textContent).toContain("以此为 1");
    expect(table.textContent).not.toContain("¥");
    expect(table.textContent).not.toContain("行上倍率相对该模型官网价");
    expect(
      screen.getByText("DeepSeek 高峰时段按官网再 ×2，平台倍率仍按行上。"),
    ).toBeTruthy();
  });
});
