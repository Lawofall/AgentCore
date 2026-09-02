// @vitest-environment jsdom
import { Button, IconButton } from "@/components/ui";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

const FOCUS_RING = /focus-visible:ring-2/;

describe("L2 control focus ring", () => {
  it("Button carries focus-visible ring", () => {
    render(<Button>Go</Button>);
    expect(screen.getByRole("button").className).toMatch(FOCUS_RING);
  });

  it("IconButton carries focus-visible ring", () => {
    render(<IconButton aria-label="more" />);
    expect(screen.getByRole("button").className).toMatch(FOCUS_RING);
  });
});
