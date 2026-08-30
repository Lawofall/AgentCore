import { StageCardDock } from "@/components/chat/StageCardDock";
// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("StageCardDock", () => {
  it("does not surface leftover stage cards", () => {
    const { container } = render(<StageCardDock />);
    expect(
      container.querySelector('[data-testid="stage-card-dock"]'),
    ).toBeNull();
  });
});
