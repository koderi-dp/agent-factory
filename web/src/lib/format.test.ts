import { describe, expect, it } from "vitest";
import { duration, relativeTime, tokens } from "./format";

describe("format", () => {
  it.each([
    [0, "0s"],
    [59.9, "59s"],
    [60, "1m 0s"],
    [3600, "1h 0m"],
    [-1, "0s"],
    [null, "—"],
    [NaN, "—"],
  ])("duration %s", (value, expected) => expect(duration(value as number | null)).toBe(expected));
  it.each([
    [0, "0"],
    [999, "999"],
    [1500, "1.5k"],
    [2_000_000, "2M"],
    [undefined, "—"],
    [Infinity, "—"],
  ])("tokens %s", (value, expected) => expect(tokens(value as number | undefined)).toBe(expected));
  it("formats relative times deterministically and tolerates future/malformed times", () => {
    const now = Date.parse("2026-10-16T12:00:00Z");
    expect(relativeTime("2026-10-16T11:59:00Z", now)).toBe("1m 0s ago");
    expect(relativeTime("2026-10-17T12:00:00Z", now)).toBe("just now");
    expect(relativeTime("invalid", now)).toBe("—");
    expect(relativeTime(null, now)).toBe("—");
  });
});
