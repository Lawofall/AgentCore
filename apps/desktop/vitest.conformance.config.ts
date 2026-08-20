import { resolve } from "path";
import { defineConfig } from "vitest/config";

/** Isolated runner for `pnpm conformance` — tsx cannot load `import.meta.env`. */
export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/renderer/protocol/conformance.run.ts"],
    exclude: ["**/node_modules/**", "e2e/**"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src/renderer"),
      "@shared": resolve(__dirname, "src/shared"),
    },
  },
});
