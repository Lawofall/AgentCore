// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InventoryRow, InventoryRowAction } from "../InventoryRow";

describe("InventoryRow", () => {
  it("title click opens; action click does not", () => {
    const onOpen = vi.fn();
    const onAction = vi.fn();
    render(
      <InventoryRow
        title="周报流水线"
        meta="3 步骤"
        onOpen={onOpen}
        actions={
          <InventoryRowAction label="跑一次" onClick={onAction}>
            go
          </InventoryRowAction>
        }
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /周报流水线/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onAction).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "跑一次" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
