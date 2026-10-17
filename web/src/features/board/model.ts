import { z } from "zod";
import type { JobDetail, RepoState } from "@/lib/api";

export type Job = RepoState["jobs"][number];
export type Execution = RepoState["executions"][number];
export type Attempt = JobDetail["attempts"][number];
export const stepSchema = z.object({
  id: z.string(),
  label: z.string().nullish(),
  type: z.enum(["task", "review", "approval"]).optional(),
  onChanges: z.string().nullish(),
  executions: z.array(
    z.object({
      id: z.string(),
      uses: z.string().optional(),
      provider: z.string().optional(),
      profiles: z
        .record(z.string(), z.object({ model: z.string(), effort: z.string() }))
        .optional(),
      role: z.object({ type: z.string() }).optional(),
    }),
  ),
});
export type Step = z.infer<typeof stepSchema>;

export function frozenSteps(job: Job): Step[] {
  const parsed = z.array(stepSchema).safeParse(job.frozen.pipeline);
  return parsed.success ? parsed.data : [];
}

/** Match engine evidence: upstream rewinds invalidate older downstream rounds (§6.1). */
export function currentExecutions(steps: Step[], executions: Execution[]): Execution[] {
  let floor = 1;
  return steps.flatMap((step) => {
    const rows = executions.filter((e) => e.step_id === step.id);
    floor = Math.max(floor, ...rows.map((e) => e.round));
    const latest = new Map<string, Execution>();
    for (const row of rows) {
      if (row.round !== floor || row.state === "superseded") continue;
      if ((latest.get(row.exec_id)?.id ?? -1) < row.id) latest.set(row.exec_id, row);
    }
    return [...latest.values()];
  });
}

export const terminalColumns = ["approved", "merged", "cancelled"] as const;
export function jobColumn(job: Job, steps = frozenSteps(job)): string {
  if (terminalColumns.some((state) => state === job.state)) return `state:${job.state}`;
  return `step:${job.current_step ?? steps[0]?.id ?? "queued"}`;
}

export function boardColumns(steps: Step[], jobs: Job[]) {
  const columns = steps.map((s) => ({ id: `step:${s.id}`, label: s.label || s.id }));
  for (const job of jobs) {
    const id = jobColumn(job, steps);
    if (id.startsWith("step:") && !columns.some((s) => s.id === id))
      columns.push({ id, label: id.slice(5) });
  }
  return [
    ...columns,
    ...terminalColumns.map((state) => ({
      id: `state:${state}`,
      label: state.charAt(0).toUpperCase() + state.slice(1),
    })),
  ];
}

export function boardGroups(state: RepoState) {
  const groups = new Map<string, { name: string; steps: Step[]; jobs: Job[] }>();
  for (const job of state.jobs) {
    const steps = frozenSteps(job);
    const key = JSON.stringify([job.pipeline_name, steps]);
    const group = groups.get(key) ?? { name: job.pipeline_name, steps, jobs: [] };
    group.jobs.push(job);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function latestAttempt(execution: Execution, attempts: Attempt[]) {
  return attempts
    .filter((a) => a.execution_id === execution.id)
    .reduce<Attempt | undefined>((last, a) => (!last || a.n > last.n ? a : last), undefined);
}

export function activityState(
  execution: Execution,
  attempt: Attempt | undefined,
  activity: RepoState["activity"],
  now: number,
) {
  const live = activity.find((a) => a.attempt_id === attempt?.id);
  const at = live?.at ?? attempt?.last_activity_at ?? attempt?.t_first_event ?? attempt?.t_launch;
  const quiet = execution.state === "running" && !!at && now - Date.parse(at) >= 600_000;
  return { text: live?.text ?? attempt?.last_activity ?? "Waiting for activity", at, quiet };
}

export function jobSummary(jobs: Job[]) {
  const count = (state: Job["state"][]) => jobs.filter((job) => state.includes(job.state)).length;
  return {
    running: count(["running"]),
    queued: count(["queued"]),
    needsYou: count(["paused", "needs_human"]),
    merged: count(["merged"]),
  };
}

/** Jobs that stopped for a person: paused on a blocker, or waiting on a decision. */
export function needsYouJobs(jobs: Job[]): Job[] {
  return jobs.filter((job) => job.state === "paused" || job.state === "needs_human");
}

export type Action = "stop" | "resume" | "approve" | "rework" | "delete";

const decisionReasons = ["verify", "escalate", "sensitive", "busy_merge"];

/** True when the backend will only merge after the sensitive-path confirmation. */
export function needsSensitiveConfirm(job: Job): boolean {
  return job.pause_reason === "sensitive";
}

/** Actions the backend accepts for the job right now, in display order. */
export function jobActions(job: Job, executions: Execution[]): Action[] {
  const busy = executions.some((e) => e.state === "claimed" || e.state === "running");
  const waiting = job.state === "needs_human";
  const canMerge =
    job.state === "approved" || (waiting && decisionReasons.includes(job.pause_reason ?? ""));
  return [
    ...(job.state === "running" || (job.state === "paused" && busy) ? ["stop" as const] : []),
    ...(job.state === "paused" ? ["resume" as const] : []),
    ...(canMerge && !busy ? ["approve" as const] : []),
    ...((job.state === "approved" || waiting) && !busy ? ["rework" as const] : []),
    ...(!busy ? ["delete" as const] : []),
  ];
}

/** Whether the job can be merged right now, and the reason when it cannot. */
export function mergeReadiness(
  job: Job,
  executions: Execution[],
): { ready: boolean; label: string; detail: string } {
  if (jobActions(job, executions).includes("approve"))
    return {
      ready: true,
      label: "Ready to merge",
      detail: needsSensitiveConfirm(job)
        ? "Merging needs the sensitive-path confirmation."
        : "The job can be merged now.",
    };
  const busy = executions.some((e) => e.state === "claimed" || e.state === "running");
  const no = (label: string, detail: string) => ({ ready: false, label, detail });
  if (job.state === "merged") return no("Merged", "This job is already merged.");
  if (job.state === "cancelled")
    return no("Cancelled", "This job was cancelled and cannot be merged.");
  if (needsSensitiveConfirm(job))
    return no(
      "Needs confirmation",
      busy
        ? "Merging needs the sensitive-path confirmation, but the job is still working."
        : "Merging needs the sensitive-path confirmation.",
    );
  if (job.state === "running" || job.state === "queued" || busy)
    return no("Still working", "The job is still working and cannot be merged yet.");
  if (job.state === "paused")
    return no("Paused", `The job is paused${job.pause_reason ? `: ${job.pause_reason}` : ""}.`);
  return no("Not approved", "The job has not been approved for merge yet.");
}

function parsed(at: string | null | undefined): number | undefined {
  if (!at) return undefined;
  const ms = Date.parse(at);
  return Number.isFinite(ms) ? ms : undefined;
}

export function jobTiming(job: Job, attempts: Attempt[], now: number) {
  const launches = attempts.map((a) => parsed(a.t_launch)).filter((ms): ms is number => ms != null);
  const start = launches.length ? Math.min(...launches) : parsed(job.created_at);
  const live = job.state !== "merged" && job.state !== "cancelled";
  const endedAt = live ? null : job.updated_at;
  const end = live ? now : parsed(endedAt);
  return {
    startedAt: start == null ? null : new Date(start).toISOString(),
    endedAt,
    elapsedSeconds: start == null || end == null ? null : (end - start) / 1000,
    live,
  };
}

export function stageTiming(attempt: Attempt | undefined, now: number) {
  const start = parsed(attempt?.t_launch);
  const finish = parsed(attempt?.t_exit);
  return {
    start: attempt?.t_launch ?? null,
    finish: attempt?.t_exit ?? null,
    seconds: start == null ? null : ((finish ?? now) - start) / 1000,
  };
}
