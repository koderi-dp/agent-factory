import type { ReactNode } from "react";
import { jobTiming, type Attempt, type Job } from "@/features/board/model";
import { clockTime, duration } from "@/lib/format";
import { PageHeader } from "@/ui/page-header";
import { StatusBadge } from "@/ui/status";
import { useNow } from "./use-now";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function JobHeader({
  job,
  attempts,
  repo,
  name,
  actions,
}: {
  job: Job;
  repo: string;
  attempts: Attempt[];
  name?: string;
  actions?: ReactNode;
}) {
  const live = jobTiming(job, attempts, 0).live;
  const now = useNow(live);
  const timing = jobTiming(job, attempts, now);
  return (
    <div className="space-y-4">
      <PageHeader title={job.title} description={job.note ?? undefined} actions={actions} />
      <dl className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
        <Field label="Job">{job.id}</Field>
        <Field label="Repository">{name || repo}</Field>
        <Field label="Pipeline">{job.pipeline_name}</Field>
        <Field label="Branch">
          <span className="font-mono">{job.branch}</span>
        </Field>
        <Field label="Status">
          <StatusBadge state={job.state} />
        </Field>
        <Field label="Started">
          <time dateTime={timing.startedAt ?? undefined}>{clockTime(timing.startedAt)}</time>
        </Field>
        <Field label="Elapsed">
          <span role="timer" aria-label="Elapsed time">
            {duration(timing.elapsedSeconds)}
          </span>
        </Field>
      </dl>
    </div>
  );
}
