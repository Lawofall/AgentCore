// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ManualDebateScoreboardPreview } from "../ManualDebateScoreboardPreview";

afterEach(cleanup);

describe("ManualDebateScoreboardPreview", () => {
  it("renders without crashing", () => {
    render(
      <MemoryRouter>
        <ManualDebateScoreboardPreview />
      </MemoryRouter>,
    );
    expect(screen.getByText("是否先做云端试点，再扩本地引擎？")).toBeTruthy();
    expect(screen.getAllByText("加速派").length).toBeGreaterThan(0);
    expect(screen.getAllByText("审慎派").length).toBeGreaterThan(0);
    expect(screen.getByText("主持人")).toBeTruthy();
  });
});
