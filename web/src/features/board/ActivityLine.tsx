import type { RepoState } from "@/lib/api";
import { duration, relativeTime } from "@/lib/format";
import { StatusBadge } from "@/ui/status";
import { Badge } from "@/ui/primitives/badge";
import { activityState, type Attempt, type Execution } from "./model";

export function ActivityLine({
  execution,
  attempt,
  activity,
  now,
}: {
  execution: Execution;
  attempt?: Attempt;
  activity: RepoState["activity"];
  now: number;
}) {
  const live = activityState(execution, attempt, activity, now);
  const elapsed =
    attempt?.t_launch && attempt.t_exit
      ? (Date.parse(attempt.t_exit) - Date.parse(attempt.t_launch)) / 1000
      : null;
  return (
    <li className="space-y-1 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-muted-foreground">
          {execution.step_id} · {execution.exec_id} ({execution.provider})
        </span>
        <StatusBadge state={execution.result ?? execution.state} />
      </div>
      {execution.state === "running" ? (
        <div className="flex flex-wrap gap-2" aria-live="polite">
          <span className="min-w-0 break-words">{live.text}</span>
          <span className="text-muted-foreground">{relativeTime(live.at, now)}</span>
          {live.quiet && <Badge variant="warn">Quiet · no activity for 10 min</Badge>}
        </div>
      ) : execution.state === "done" ? (
        <span className="font-mono text-muted-foreground">{duration(elapsed)}</span>
      ) : null}
    </li>
  );
}
