// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { ManualCheckpointCardPreview } from "../ManualCheckpointCardPreview";

afterEach(cleanup);

describe("ManualCheckpointCardPreview", () => {
  it("renders without crashing", () => {
    render(
      <MemoryRouter>
        <ManualCheckpointCardPreview />
      </MemoryRouter>,
    );
    expect(screen.getByText("需要你拍板")).toBeTruthy();
    expect(screen.getByText(/试点范围定多大？/)).toBeTruthy();
    expect(screen.getByText("提交")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
  });
});
