import { foldedIntoPreset } from "@/lib/assemblyFold";
import type { LlmModelProfileView } from "@/services/llmModelProfiles";
import { describe, expect, it } from "vitest";

function row(
  partial: Pick<LlmModelProfileView, "id" | "name" | "kind"> &
    Partial<LlmModelProfileView>,
): LlmModelProfileView {
  return {
    is_default: false,
    ...partial,
  } as LlmModelProfileView;
}

const chat = row({
  id: "sys-chat",
  name: "极简",
  kind: "system",
  recipe: "chat",
});

describe("foldedIntoPreset", () => {
  it("同名且配方还在时折进预置名字", () => {
    const owned = row({
      id: "owned",
      name: "极简",
      kind: "user",
      recipe: "chat",
    });
    expect(foldedIntoPreset(owned, [chat])?.id).toBe("sys-chat");
  });

  it("改名、松开配方、或预置已拿掉时另排一颗", () => {
    const renamed = row({
      id: "renamed",
      name: "闲聊",
      kind: "user",
      recipe: "chat",
    });
    const released = row({
      id: "released",
      name: "极简",
      kind: "user",
      recipe: null,
    });
    expect(foldedIntoPreset(renamed, [chat])).toBeUndefined();
    expect(foldedIntoPreset(released, [chat])).toBeUndefined();
    expect(foldedIntoPreset(renamed, [])).toBeUndefined();
  });
});
