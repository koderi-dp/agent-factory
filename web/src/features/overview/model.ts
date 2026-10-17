import type { z } from "zod";
import type { ActivityEntry, CrossJob, overviewSchema } from "@/lib/api";

type Repos = z.infer<typeof overviewSchema>["repos"];

export type SummaryKey = "running" | "queued" | "attention" | "merged";

export function overviewTotals(repos: Repos) {
  const totals = { running: 0, queued: 0, needsYou: 0, merged: 0 };
  for (const repo of repos) {
    totals.running += repo.counts.running;
    totals.queued += repo.counts.queued;
    totals.needsYou += repo.counts["needs-you"];
    totals.merged += repo.counts.merged;
  }
  return totals;
}

export const summaryFilters: readonly {
  key: SummaryKey;
  label: string;
  states: readonly string[];
}[] = [
  { key: "running", label: "Running", states: ["running"] },
  { key: "queued", label: "Queued", states: ["queued"] },
  { key: "attention", label: "Needs attention", states: ["paused", "needs_human"] },
  { key: "merged", label: "Merged", states: ["merged"] },
];

export function isSummaryKey(value: string): value is SummaryKey {
  return summaryFilters.some((filter) => filter.key === value);
}

const humanProblems: Record<string, string> = {
  sensitive: "A sensitive change needs approval.",
  contract: "An agent's output needs a decision.",
  verify: "Approval or a merge decision is required.",
  escalate: "Approval or a merge decision is required.",
  busy_merge: "Approval or a merge decision is required.",
};
const pausedProblems: Record<string, string> = {
  ports: "Stopped because no ports were available.",
  process: "Stopped because an agent process exited unexpectedly.",
  timeout: "Stopped because a stage timed out.",
};

export function attentionItem(job: Pick<CrossJob, "state" | "pause_reason" | "note">) {
  const reason = job.pause_reason ?? "";
  if (job.state === "needs_human")
    return {
      problem: humanProblems[reason] ?? "A decision is needed.",
      action: "Review and decide",
    };
  const base = pausedProblems[reason] ?? (reason ? `Stopped: ${reason}.` : "The job is paused.");
  return {
    problem: job.note ? `${base} ${job.note}` : base,
    action: "Resolve and resume",
  };
}

export function describeEvent(entry: Pick<ActivityEntry, "type" | "state" | "stage">): string {
  const state = entry.state ? entry.state.replace(/_/g, " ") : null;
  if (entry.type === "job.state" && state)
    return entry.state === "merged" ? "Job merged" : `Job is now ${state}`;
  if (entry.type === "execution.state" && state)
    return entry.stage ? `Stage ${entry.stage} is now ${state}` : `Stage is now ${state}`;
  if (entry.type === "attempt.state" && state)
    return entry.stage ? `Attempt for ${entry.stage} is now ${state}` : `Attempt is now ${state}`;
  if (entry.type === "usage") return "Usage recorded";
  if (entry.type === "note") return "Note added";
  if (entry.type === "config") return "Configuration changed";
  if (entry.type === "backlog") return "Backlog updated";
  return entry.type;
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  "job.state": "Job",
  "execution.state": "Stage",
  "attempt.state": "Attempt",
  usage: "Usage",
  note: "Note",
  config: "Config",
  backlog: "Backlog",
};

export function eventTypeLabel(type: string): string {
  return EVENT_TYPE_LABELS[type] ?? type;
}
