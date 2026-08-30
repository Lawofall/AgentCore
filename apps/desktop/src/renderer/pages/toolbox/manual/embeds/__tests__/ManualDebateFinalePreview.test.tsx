// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ManualDebateFinalePreview } from "../ManualDebateFinalePreview";

afterEach(cleanup);

describe("ManualDebateFinalePreview", () => {
  it("renders without crashing", () => {
    render(<ManualDebateFinalePreview />);
    expect(screen.getByText("主持人终审")).toBeTruthy();
    expect(screen.getByText("倾向加速派")).toBeTruthy();
    expect(screen.getByText("留给你的")).toBeTruthy();
    expect(screen.getByText(/要不要牺牲速度换更稳的回滚/)).toBeTruthy();
    expect(screen.queryByText(/建议：/)).toBeNull();
  });
});
