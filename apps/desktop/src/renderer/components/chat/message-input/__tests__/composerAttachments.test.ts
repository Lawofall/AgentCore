import { inlineToken } from "@/lib/inlineBody";
import { describe, expect, it } from "vitest";
import {
  composerHasSendableDraft,
  mergeComposerStripImages,
} from "../composerAttachments";

describe("composerHasSendableDraft", () => {
  it("allows attachment-only (empty / whitespace text)", () => {
    expect(composerHasSendableDraft("", [{ id: "1" }])).toBe(true);
    expect(composerHasSendableDraft("  \n", [{ id: "1" }])).toBe(true);
  });

  it("allows mention-only and marker-only pills", () => {
    expect(composerHasSendableDraft("", [], [{ id: "m1" }])).toBe(true);
    expect(composerHasSendableDraft(inlineToken("A", 0), [], [])).toBe(true);
    expect(composerHasSendableDraft(inlineToken("M", 0), [], [])).toBe(true);
  });

  it("requires non-blank text when there are no attachments", () => {
    expect(composerHasSendableDraft("", [])).toBe(false);
    expect(composerHasSendableDraft("   ", [])).toBe(false);
    expect(composerHasSendableDraft("hi", [])).toBe(true);
  });
});

describe("mergeComposerStripImages", () => {
  const image = {
    id: "img",
    name: "shot.png",
    kind: "file" as const,
  };
  const pdf = {
    id: "pdf",
    name: "brief.pdf",
    kind: "file" as const,
  };

  it("keeps strip images when the editor pass dropped them", () => {
    expect(mergeComposerStripImages([image, pdf], [pdf])).toEqual([image, pdf]);
  });

  it("drops a non-image the editor removed and keeps later images in place", () => {
    expect(mergeComposerStripImages([pdf, image], [])).toEqual([image]);
  });
});
