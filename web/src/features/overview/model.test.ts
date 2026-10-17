import { describe, expect, it } from "vitest";
import { attentionItem, describeEvent, eventTypeLabel, isSummaryKey } from "./model";

describe("overview model", () => {
  it("describes attention items in plain words", () => {
    expect(attentionItem({ state: "needs_human", pause_reason: "sensitive", note: null })).toEqual({
      problem: "A sensitive change needs approval.",
      action: "Review and decide",
    });
    const paused = attentionItem({ state: "paused", pause_reason: "ports", note: "none free" });
    expect(paused.problem).toContain("none free");
    expect(paused.action).toBe("Resolve and resume");
    expect(attentionItem({ state: "paused", pause_reason: "odd", note: null }).problem).toContain(
      "odd",
    );
  });
  it("describes events and validates filter keys", () => {
    expect(describeEvent({ type: "job.state", state: "merged", stage: null })).toBe("Job merged");
    expect(describeEvent({ type: "note", state: null, stage: null })).toBe("Note added");
    expect(describeEvent({ type: "usage", state: null, stage: null })).toBe("Usage recorded");
    expect(describeEvent({ type: "mystery", state: null, stage: null })).toBe("mystery");
    expect(eventTypeLabel("job.state")).toBe("Job");
    expect(eventTypeLabel("mystery")).toBe("mystery");
    expect(isSummaryKey("attention")).toBe(true);
    expect(isSummaryKey("bogus")).toBe(false);
  });
});
