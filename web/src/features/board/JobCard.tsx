import { useQuery } from "@tanstack/react-query";
import type { RepoState } from "@/lib/api";
import { api, keys } from "@/lib/api";
import { duration, relativeTime } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/ui/primitives/card";
import { Badge } from "@/ui/primitives/badge";
import { StatusBadge } from "@/ui/status";
import { CompactPipeline } from "./CompactPipeline";
import { ActivityLine } from "./ActivityLine";
import { currentExecutions, frozenSteps, latestAttempt, type Job } from "./model";

export function JobCard({
  repo,
  job,
  state,
  now,
}: {
  repo: string;
  job: Job;
  state: RepoState;
  now: number;
}) {
  const steps = frozenSteps(job);
  const executions = state.executions.filter((e) => e.job_id === job.id);
  const current = currentExecutions(steps, executions);
  const stageStarted =
    current
      .filter((e) => e.step_id === job.current_step)
      .map((e) => e.created_at)
      .sort()[0] ?? job.created_at;
  const detail = useQuery({
    queryKey: keys.job(repo, job.id),
    queryFn: () => api.job(repo, job.id),
  });
  return (
    <Card>
      <CardHeader className="gap-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">#{job.seq}</span>
          <StatusBadge state={job.state} />
          {job.round > 1 && <Badge>Round {job.round}</Badge>}
        </div>
        <CardTitle className="text-sm leading-5">
          <a
            className="hover:underline"
            href={`#/repo/${encodeURIComponent(repo)}/job/${encodeURIComponent(job.id)}`}
          >
            {job.title}
          </a>
        </CardTitle>
        <CompactPipeline steps={steps} executions={executions} />
      </CardHeader>
      <CardContent className="space-y-3 px-4 pb-4">
        <p className="text-xs text-muted-foreground">
          {["merged", "approved", "cancelled"].includes(job.state)
            ? `Updated ${relativeTime(job.updated_at, now)}`
            : `${job.current_step ?? "Queued"} · ${duration((now - Date.parse(stageStarted)) / 1000)} in stage`}
        </p>
        {(job.state === "paused" || job.state === "needs_human") && (
          <div className="space-y-1 rounded-sm bg-warn/10 p-2 text-xs text-warn-fg">
            <Badge variant="warn">
              {job.state === "paused" ? "Paused" : "Needs you"}
              {job.pause_reason ? ` · ${job.pause_reason}` : ""}
            </Badge>
            {job.note && <p className="break-words">{job.note}</p>}
          </div>
        )}
        <ul className="space-y-3" aria-label="Executions">
          {current.map((execution) => (
            <ActivityLine
              key={execution.id}
              execution={execution}
              attempt={latestAttempt(execution, detail.data?.attempts ?? [])}
              activity={state.activity}
              now={now}
            />
          ))}
        </ul>
        {detail.isError && <p className="text-xs text-warn-fg">Attempt details unavailable.</p>}
      </CardContent>
    </Card>
  );
}
