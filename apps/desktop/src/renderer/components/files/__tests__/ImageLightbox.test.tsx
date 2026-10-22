// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageLightbox } from "../ImageLightbox";

describe("ImageLightbox", () => {
  it("closes on Escape and backdrop click", () => {
    const onClose = vi.fn();
    render(
      <ImageLightbox
        images={[{ src: "data:image/png;base64,AAAA", name: "shot.png" }]}
        index={0}
        onClose={onClose}
      />,
    );
    expect(screen.getByTestId("image-lightbox")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("arrows switch slides when more than one image", () => {
    const onIndexChange = vi.fn();
    render(
      <ImageLightbox
        images={[
          { src: "data:image/png;base64,AA", name: "a.png" },
          { src: "data:image/png;base64,BB", name: "b.png" },
        ]}
        index={0}
        onClose={vi.fn()}
        onIndexChange={onIndexChange}
      />,
    );
    expect(screen.getByRole("dialog", { name: "a.png · 1/2" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一张" }));
    expect(onIndexChange).toHaveBeenCalledWith(1);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onIndexChange).toHaveBeenCalledTimes(2);
  });
});
