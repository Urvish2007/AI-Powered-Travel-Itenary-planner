import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library registers its own cleanup only when Vitest's globals are
// enabled. This suite imports `describe`/`it`/`expect` explicitly instead, so
// the unmount has to be wired by hand — without it, every render stays in the
// document and the next test's queries match the previous test's markup.
afterEach(() => {
  cleanup();
});
