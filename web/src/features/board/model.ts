import { z } from "zod";
import type { JobDetail, RepoState } from "@/lib/api";

export type Job = RepoState["jobs"][number];
export type Execution = RepoState["executions"][number];
export type Attempt = JobDetail["attempts"][number];
const stepSchema = z.object({
  id: z.string(),
  label: z.string().nullish(),
  executions: z.array(z.object({ id: z.string() })),
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
