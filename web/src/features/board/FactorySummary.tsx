import { Badge } from "@/ui/primitives/badge";
import { Card, CardContent } from "@/ui/primitives/card";
import { jobSummary, needsYouJobs, type Job } from "./model";

// The factory's at-a-glance state: what is moving, what is waiting on you, what shipped.
export function FactorySummary({ repo, jobs }: { repo: string; jobs: Job[] }) {
  const totals = jobSummary(jobs);
  const waiting = needsYouJobs(jobs);
  const tiles: { label: string; value: number; warn?: boolean }[] = [
    { label: "Running", value: totals.running },
    { label: "Needs you", value: totals.needsYou, warn: totals.needsYou > 0 },
    { label: "Queued", value: totals.queued },
    { label: "Merged", value: totals.merged },
  ];
  return (
    <section aria-label="Factory summary" className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map((tile) => (
          <Card key={tile.label}>
            <CardContent className="space-y-1 p-4">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{tile.label}</p>
              <p
                className={`font-mono text-2xl font-medium tabular-nums ${tile.warn ? "text-warn-fg" : ""}`}
              >
                {tile.value}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
      {waiting.length > 0 && (
        <Card>
          <CardContent className="space-y-3 p-4">
            <h2 className="text-sm font-medium">Waiting on you</h2>
            <ul className="space-y-2">
              {waiting.map((job) => (
                <li key={job.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant="warn">{job.state === "paused" ? "Paused" : "Decision"}</Badge>
                  <a
                    className="font-medium hover:underline"
                    href={`#/repo/${encodeURIComponent(repo)}/job/${encodeURIComponent(job.id)}`}
                  >
                    {job.title}
                  </a>
                  {job.pause_reason && (
                    <span className="text-muted-foreground">· {job.pause_reason}</span>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </section>
  );
}
