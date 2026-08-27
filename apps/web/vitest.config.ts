import path from "node:path";

import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // The default forked-process pool times out in some sandboxed shells
    // (subprocess spawning restricted); the in-process threads pool doesn't
    // need to spawn a subprocess and runs reliably everywhere.
    pool: "threads",
    // Playwright's e2e specs use @playwright/test's own `test`/`expect`, not
    // Vitest's - excluded so Vitest never tries to collect them.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
