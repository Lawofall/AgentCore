// @vitest-environment jsdom
import { FolderCollabMark } from "@/components/folders/FolderCollabMark";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("FolderCollabMark", () => {
  it("announces 协作 · N 人 and never writes 已共享", () => {
    render(<FolderCollabMark count={3} />);
    expect(screen.getByText("协作 · 3 人")).toBeTruthy();
    expect(screen.queryByText("已共享")).toBeNull();
  });
});
