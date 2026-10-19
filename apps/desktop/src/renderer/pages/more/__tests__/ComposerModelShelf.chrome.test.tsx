// @vitest-environment jsdom
import { PLATFORM_POINTER_ID } from "@/lib/llmDefaults";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ComposerModelShelf } from "../ComposerModelShelf";

afterEach(cleanup);

const groups = [
  {
    providerId: PLATFORM_POINTER_ID,
    providerLabel: "平台",
    models: [
      {
        model: "opus",
        label: "Claude Opus 5.5",
        available: true,
        origin: "platform" as const,
        price: {
          currency: "CNY" as const,
          cache_miss: "8.64",
          output: "43.2",
          multiplier: "0.3",
          quota_multiplier: "41",
        },
      },
    ],
  },
];

const slot = {
  origin: "platform" as const,
  provider_id: null,
  model: "opus",
};

describe("ComposerModelShelf columns", () => {
  it("anchors the detail card to the selected row", () => {
    render(
      <MemoryRouter>
        <ComposerModelShelf
          layout="columns"
          groups={groups}
          main={slot}
          catalogModels={[]}
          effort={null}
          budget={null}
          onPickMain={() => undefined}
          onTune={() => undefined}
        />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("assembly-model-row").className).toContain(
      "w-52",
    );
    const detail = screen.getByTestId("composer-model-detail");
    expect(detail.getAttribute("data-side")).toBe("right");
    expect(detail.className).not.toContain("left-full");
    expect(detail.textContent).toContain("Claude Opus 5.5");
    expect(detail.textContent).not.toContain("消耗积分为默认的 41x");
    expect(screen.getByText("此倍率相对该模型官网价")).toBeTruthy();
  });
});
