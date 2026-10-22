// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AttachSourceInputs,
  AttachSourcePopover,
  AttachSourceRows,
} from "../AttachSourceInputs";

afterEach(() => {
  cleanup();
});

describe("AttachSourceInputs", () => {
  it("wires camera / gallery / files attributes", () => {
    render(<AttachSourceInputs onPicked={() => undefined} />);
    const camera = screen.getByTestId("attach-source-input-camera");
    const gallery = screen.getByTestId("attach-source-input-gallery");
    const files = screen.getByTestId("attach-source-input-files");
    expect(camera.getAttribute("capture")).toBe("environment");
    expect(camera.getAttribute("accept")).toBe("image/*");
    expect(camera.hasAttribute("multiple")).toBe(false);
    expect(gallery.getAttribute("accept")).toBe("image/*");
    expect(gallery.hasAttribute("multiple")).toBe(true);
    expect(gallery.hasAttribute("capture")).toBe(false);
    expect(files.hasAttribute("multiple")).toBe(true);
    expect(files.hasAttribute("accept")).toBe(false);
    expect(files.hasAttribute("capture")).toBe(false);
  });
});

describe("AttachSourceRows", () => {
  it("exposes four sources when cite is available", () => {
    const onCamera = vi.fn();
    const onCite = vi.fn();
    render(
      <AttachSourceRows
        onCamera={onCamera}
        onGallery={() => undefined}
        onFiles={() => undefined}
        onCite={onCite}
      />,
    );
    fireEvent.click(screen.getByTestId("attach-source-camera"));
    expect(onCamera).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("attach-source-cite"));
    expect(onCite).toHaveBeenCalled();
  });

  it("hides cite for IM", () => {
    render(
      <AttachSourceRows
        onCamera={() => undefined}
        onGallery={() => undefined}
        onFiles={() => undefined}
      />,
    );
    expect(screen.queryByTestId("attach-source-cite")).toBeNull();
    expect(screen.getByTestId("attach-source-files")).toBeTruthy();
  });
});

describe("AttachSourcePopover", () => {
  it("opens from the trigger then picks a source", () => {
    const onFiles = vi.fn();
    render(
      <AttachSourcePopover
        trigger={<button type="button">添加附件</button>}
        onCamera={() => undefined}
        onGallery={() => undefined}
        onFiles={onFiles}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "添加附件" }));
    fireEvent.click(screen.getByTestId("attach-source-files"));
    expect(onFiles).toHaveBeenCalled();
  });
});
