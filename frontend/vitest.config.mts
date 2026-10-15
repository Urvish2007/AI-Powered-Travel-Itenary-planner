import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Unit and component tests.
 *
 * `resolve.tsconfigPaths` is what makes the `@/*` alias resolve the same way it
 * does in the app, so tests import components by the path the source uses
 * rather than a relative chain that would break whenever a file moves. Vite
 * resolves tsconfig paths natively, so no plugin is needed for it.
 */
export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Tests live next to the code they cover. Anything under node_modules or
    // .next is somebody else's test suite.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
