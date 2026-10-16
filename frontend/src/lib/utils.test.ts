import { describe, expect, it } from "vitest";

import { formatDistance } from "@/lib/utils";

/**
 * Covers the harness as much as the function: a passing test here proves Vitest
 * resolves the `@/` alias and runs TypeScript from `src`.
 *
 * The kilometre cases pass an explicit locale. `Intl.NumberFormat` otherwise
 * follows the host's locale, so an assertion on "12.3 km" would fail on a
 * machine formatting that as "12,3 km" — a real failure for a fake reason.
 */
describe("formatDistance", () => {
  it("uses metres below a kilometre, where '0.4 km' would read as false precision", () => {
    expect(formatDistance(0.4)).toBe("400 m");
  });

  it("uses kilometres at and above the threshold", () => {
    expect(formatDistance(12.345, "en-US")).toBe("12.3 km");
    expect(formatDistance(1, "en-US")).toBe("1 km");
  });

  it("treats zero as a distance, not a missing value", () => {
    expect(formatDistance(0)).toBe("0 m");
  });
});
