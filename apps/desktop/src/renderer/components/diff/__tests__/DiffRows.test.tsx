// @vitest-environment jsdom
import { DiffRows } from "@/components/diff/DiffRows";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("DiffRows", () => {
  it("paints add/del with dedicated washes and a stronger sign gutter", () => {
    render(
      <DiffRows
        lines={[
          { type: "del", text: "old" },
          { type: "add", text: "new" },
        ]}
      />,
    );
    expect(screen.getByText("old").closest("div")?.className).toContain(
      "bg-diff-del",
    );
    expect(screen.getByText("new").closest("div")?.className).toContain(
      "bg-diff-add",
    );
    expect(screen.getByText("+").className).toContain("bg-diff-add-gutter");
    expect(screen.getByText("-").className).toContain("bg-diff-del-gutter");
  });
});
