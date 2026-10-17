import { describe, expect, it } from "vitest";
import { jobSummary, needsYouJobs, type Job } from "./model";

function job(id: string, state: Job["state"]): Job {
  return { id, state } as Job;
}

describe("jobSummary", () => {
  it("counts each factory bucket once", () => {
    const jobs = [
      job("a", "running"),
      job("b", "queued"),
      job("c", "paused"),
      job("d", "needs_human"),
      job("e", "merged"),
      job("f", "approved"),
    ];
    expect(jobSummary(jobs)).toEqual({ running: 1, queued: 1, needsYou: 2, merged: 1 });
  });
});

describe("needsYouJobs", () => {
  it("returns only paused and needs-human jobs, in order", () => {
    const jobs = [job("a", "running"), job("b", "needs_human"), job("c", "paused")];
    expect(needsYouJobs(jobs).map((j) => j.id)).toEqual(["b", "c"]);
  });
});
