import { describe, expect, it } from "vitest";
import { monthLabel, n, pct, shortHash, t } from "@/lib/format";

describe("format", () => {
  it("shows a dash for missing numbers, never 0", () => {
    expect(n(null)).toBe("—");
    expect(n(undefined)).toBe("—");
    expect(n(Number.NaN)).toBe("—");
    expect(t(null)).toBe("—");
    expect(pct(undefined)).toBe("—");
  });
  it("groups thousands and fixes decimals", () => {
    expect(n(15297.94)).toBe("15,298");
    expect(n(0.1234, 2)).toBe("0.12");
  });
  it("uses one decimal for small tonnages and none for large", () => {
    expect(t(12.34)).toBe("12.3 tCO₂e");
    expect(t(695.2)).toBe("695 tCO₂e");
  });
  it("formats periods and hashes", () => {
    expect(monthLabel("2025-09")).toBe("Sep 2025");
    expect(pct(0.123)).toBe("12.3%");
    expect(shortHash("a".repeat(64))).toBe("aaaaaaaaaa…");
  });
});
