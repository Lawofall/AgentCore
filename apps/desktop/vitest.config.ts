import { resolve } from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    // Playwright 套件（pnpm e2e 专属 runner）；vitest 误收集会直接报错。
    exclude: ["**/node_modules/**", "e2e/**"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src/renderer"),
      "@shared": resolve(__dirname, "src/shared"),
      "@byok-presets": resolve(
        __dirname,
        "../server/agentcore/llm/byok_provider_presets.json",
      ),
    },
  },
});
