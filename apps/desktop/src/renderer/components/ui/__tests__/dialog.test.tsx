// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Dialog, DialogContent, DialogTitle } from "../dialog";

describe("DialogContent size", () => {
  it("defaults to lg and maps each token to a max-width class", () => {
    const { rerender } = render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>默认</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("max-w-lg");

    rerender(
      <Dialog open>
        <DialogContent size="md">
          <DialogTitle>短表单</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("max-w-md");

    rerender(
      <Dialog open>
        <DialogContent size="xl">
          <DialogTitle>命令</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("max-w-xl");

    rerender(
      <Dialog open>
        <DialogContent size="2xl">
          <DialogTitle>双栏</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog").className).toContain("max-w-2xl");
  });
});
