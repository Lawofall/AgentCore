// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ImageThumbStrip } from "../ImageThumbStrip";

beforeAll(() => {
  URL.createObjectURL = vi.fn(
    () => "blob:mock-thumb",
  ) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
});

afterEach(() => {
  document.body.replaceChildren();
});

describe("ImageThumbStrip", () => {
  it("opens a lightbox from a blob thumb and closes on Escape", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "shot.png", {
      type: "image/png",
    });
    render(
      <ImageThumbStrip
        attachments={[
          {
            id: "a1",
            name: "shot.png",
            kind: "file",
            fileBlob: file,
          },
        ]}
        onRemove={vi.fn()}
      />,
    );
    const open = await waitFor(() =>
      screen.getByRole("button", { name: "查看 shot.png" }),
    );
    fireEvent.click(open);
    expect(screen.getByTestId("image-lightbox")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("image-lightbox")).toBeNull();
  });

  it("workspace path without a file source is a broken thumb, not a spinner", () => {
    render(
      <ImageThumbStrip
        attachments={[
          {
            id: "a1",
            name: "shot.png",
            kind: "file",
            workspacePath: "attachments/shot.png",
          },
        ]}
      />,
    );
    expect(screen.getByRole("img", { name: "shot.png" })).toBeTruthy();
    expect(document.querySelector(".animate-spin")).toBeNull();
  });

  it("workspace path spins only while the file source is still resolving", () => {
    render(
      <ImageThumbStrip
        attachments={[
          {
            id: "a1",
            name: "shot.png",
            kind: "file",
            workspacePath: "attachments/shot.png",
          },
        ]}
        sourcePending
      />,
    );
    expect(document.querySelector(".animate-spin")).toBeTruthy();
  });

  it("remove control does not open the lightbox", async () => {
    const onRemove = vi.fn();
    const file = new File([new Uint8Array([1])], "shot.png", {
      type: "image/png",
    });
    render(
      <ImageThumbStrip
        attachments={[
          { id: "a1", name: "shot.png", kind: "file", fileBlob: file },
        ]}
        onRemove={onRemove}
      />,
    );
    await waitFor(() => screen.getByRole("button", { name: "查看 shot.png" }));
    fireEvent.click(screen.getByRole("button", { name: "移除附件" }));
    expect(onRemove).toHaveBeenCalledWith("a1");
    expect(screen.queryByTestId("image-lightbox")).toBeNull();
  });
});
