// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import {
  GALLERY_COMPRESS_MIN_BYTES,
  compressImageFile,
  isHeicImage,
  prepareAttachFiles,
  shouldCompressImage,
} from "../compressImageFile";

function file(name: string, type: string, size = 16): File {
  return new File([new Uint8Array(size)], name, { type });
}

describe("shouldCompressImage", () => {
  it("always compresses camera stills, never files or gifs", () => {
    const jpeg = file("a.jpg", "image/jpeg");
    expect(shouldCompressImage(jpeg, "camera")).toBe(true);
    expect(shouldCompressImage(jpeg, "files")).toBe(false);
    expect(shouldCompressImage(file("a.gif", "image/gif"), "camera")).toBe(
      false,
    );
    expect(shouldCompressImage(file("a.txt", "text/plain"), "camera")).toBe(
      false,
    );
  });

  it("compresses gallery HEIC or large bitmaps only", () => {
    const small = file("shot.png", "image/png", 800);
    expect(shouldCompressImage(small, "gallery")).toBe(false);
    const big = file("shot.jpg", "image/jpeg", GALLERY_COMPRESS_MIN_BYTES);
    expect(shouldCompressImage(big, "gallery")).toBe(true);
    expect(
      shouldCompressImage(file("img.heic", "image/heic", 100), "gallery"),
    ).toBe(true);
  });
});

describe("isHeicImage", () => {
  it("matches type and suffix", () => {
    expect(isHeicImage(file("a.HEIC", ""))).toBe(true);
    expect(isHeicImage(file("a.jpg", "image/heif"))).toBe(true);
    expect(isHeicImage(file("a.jpg", "image/jpeg"))).toBe(false);
  });
});

describe("compressImageFile", () => {
  it("returns the original when decode is unavailable", async () => {
    const original = file("a.jpg", "image/jpeg", 32);
    expect(await compressImageFile(original)).toBe(original);
  });
});

describe("prepareAttachFiles", () => {
  it("leaves non-images and file-picker images untouched", async () => {
    const doc = file("a.pdf", "application/pdf", 64);
    const pic = file("a.jpg", "image/jpeg", 64);
    expect(await prepareAttachFiles([doc, pic], "files")).toEqual([doc, pic]);
  });
});
