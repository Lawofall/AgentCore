import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { NARROW_MAX_WIDTH } from "@/lib/useNarrowLayout";
import { describe, expect, it } from "vitest";

const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../../styles/globals.css"),
  "utf8",
);

describe("narrow typography", () => {
  it("remaps reading tokens at the same cutoff as the narrow shell", () => {
    expect(css).toContain(`@media (max-width: ${NARROW_MAX_WIDTH}px)`);
    expect(css).toMatch(/--text-sm:\s*1rem;/);
    expect(css).toMatch(/--text-base:\s*1\.0625rem;/);
    expect(css).toMatch(
      /\.markdown-body \{[\s\S]*?font-size:\s*var\(--text-sm\);/,
    );
  });
});
