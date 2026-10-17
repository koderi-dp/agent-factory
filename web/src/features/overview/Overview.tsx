import type { ReactNode } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Link, useSearch } from "@tanstack/react-router";
import type { z } from "zod";
import { api, keys, type overviewSchema } from "@/lib/api";
import { clockTime, relativeTime } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/ui/primitives/card";
import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";
import { StatusBadge } from "@/ui/status";
import {
  attentionItem,
  describeEvent,
  isSummaryKey,
  overviewTotals,
  summaryFilters,
} from "./model";

const ACTIVE_STATES = ["queued", "running", "paused", "needs_human", "approved"] as const;
const ATTENTION_STATES = ["paused", "needs_human"] as const;
const jobHref = (repo: string, job: string) =>
  `#/repo/${encodeURIComponent(repo)}/job/${encodeURIComponent(job)}`;

function Query({
  query,
  what,
  children,
}: {
  query: UseQueryResult;
  what: string;
  children: ReactNode;
}) {
  if (query.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading {what}…
      </p>
    );
  if (query.isError)
    return (
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <p role="alert">
          Could not load {what}: {query.error.message}
        </p>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    );
  return <>{children}</>;
}

export function Overview({ repos }: z.infer<typeof overviewSchema>) {
  const search = useSearch({ strict: false }) as { state?: string };
  const selected = summaryFilters.find(
    (filter) => search.state && isSummaryKey(search.state) && filter.key === search.state,
  );
  const states = selected?.states ?? ACTIVE_STATES;
  const totals = overviewTotals(repos);
  const attention = useQuery({
    queryKey: keys.jobs(ATTENTION_STATES),
    queryFn: () => api.jobs({ states: ATTENTION_STATES }),
  });
  const jobs = useQuery({
    queryKey: keys.jobs(states),
    queryFn: () => api.jobs({ states }),
  });
  const activity = useQuery({
    queryKey: keys.activity(20),
    queryFn: () => api.activity({ limit: 20 }),
  });
  const waiting = attention.data?.jobs ?? [];
  return (
    <PageShell gap="loose">
      <PageHeader
        kicker="Agent Factory"
        title="Factory overview"
        description={`${totals.running} running, ${totals.needsYou} need attention, ${totals.queued} queued across ${repos.length} ${repos.length === 1 ? "repository" : "repositories"}.`}
      />
      <section aria-label="Summary" className="space-y-3">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {summaryFilters.map((filter) => {
            const value =
              filter.key === "attention"
                ? totals.needsYou
                : totals[filter.key as "running" | "queued" | "merged"];
            const active = selected?.key === filter.key;
            return (
              <Link
                key={filter.key}
                to="/"
                search={{ state: filter.key }}
                aria-current={active ? "true" : undefined}
                className="block rounded-lg focus-visible:outline-2"
              >
                <Card className={active ? "ring-2 ring-ring" : "hover:bg-inset"}>
                  <CardContent className="space-y-1 p-4">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">
                      {filter.label}
                    </p>
                    <p
                      className={`font-mono text-2xl font-medium tabular-nums ${filter.key === "attention" && value > 0 ? "text-warn-fg" : ""}`}
                    >
                      {value}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
        {selected && (
          <Link to="/" className="text-sm underline">
            Clear filter
          </Link>
        )}
      </section>
      <Query query={attention} what="jobs needing attention">
        {waiting.length > 0 && (
          <section aria-labelledby="needs-attention">
            <Card className="border border-warn">
              <CardHeader>
                <CardTitle id="needs-attention" className="flex items-center gap-2">
                  <Badge variant="warn">{waiting.length}</Badge> Needs attention
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3">
                  {waiting.map((job) => {
                    const item = attentionItem(job);
                    return (
                      <li
                        key={`${job.repo_id}:${job.id}`}
                        className="flex flex-wrap items-center justify-between gap-3 text-sm"
                      >
                        <div className="min-w-0">
                          <p>
                            <span className="text-muted-foreground">{job.repo_name} · </span>
                            <span className="font-medium">{job.title}</span>
                          </p>
                          <p className="text-muted-foreground">{item.problem}</p>
                        </div>
                        <Button variant="secondary" asChild>
                          <a href={jobHref(job.repo_id, job.id)}>{item.action}</a>
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              </CardContent>
            </Card>
          </section>
        )}
      </Query>
      <section aria-labelledby="jobs-list" className="space-y-3">
        <h2 id="jobs-list" className="text-sm font-medium">
          {selected ? selected.label : "Active jobs"}
        </h2>
        <Query query={jobs} what="jobs">
          {jobs.data && jobs.data.jobs.length === 0 && (
            <p className="text-sm text-muted-foreground">No jobs in this state.</p>
          )}
          {jobs.data && jobs.data.jobs.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {jobs.data.jobs.map((job) => (
                <li
                  key={`${job.repo_id}:${job.id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 p-3 text-sm"
                >
                  <a className="font-medium hover:underline" href={jobHref(job.repo_id, job.id)}>
                    {job.title}
                  </a>
                  <span className="text-muted-foreground">{job.repo_name}</span>
                  <StatusBadge state={job.state} />
                  <span className="text-muted-foreground">{job.current_step ?? "—"}</span>
                  <span className="text-muted-foreground">{relativeTime(job.updated_at)}</span>
                </li>
              ))}
            </ul>
          )}
          {jobs.data?.truncated && (
            <p className="text-sm text-muted-foreground">The list is cut; more jobs exist.</p>
          )}
        </Query>
      </section>
      <section aria-labelledby="recent-activity" className="space-y-3">
        <h2 id="recent-activity" className="text-sm font-medium">
          Recent activity
        </h2>
        <Query query={activity} what="activity">
          {activity.data && activity.data.events.length === 0 && (
            <p className="text-sm text-muted-foreground">No activity yet.</p>
          )}
          {activity.data && activity.data.events.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {activity.data.events.map((event) => (
                <li
                  key={`${event.repo_id}:${event.id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 p-3 text-sm"
                >
                  <time dateTime={event.ts} className="font-mono text-xs text-muted-foreground">
                    {clockTime(event.ts)}
                  </time>
                  <Badge variant="outline">{event.type}</Badge>
                  {event.job_id ? (
                    <a
                      className="font-medium hover:underline"
                      href={jobHref(event.repo_id, event.job_id)}
                    >
                      {event.job_title ?? event.job_id}
                    </a>
                  ) : (
                    <span className="text-muted-foreground">Repository</span>
                  )}
                  <span className="text-muted-foreground">{event.repo_name}</span>
                  <span>{describeEvent(event)}</span>
                </li>
              ))}
            </ul>
          )}
        </Query>
      </section>
      <section aria-labelledby="repositories" className="space-y-3">
        <h2 id="repositories" className="text-sm font-medium">
          Repositories
        </h2>
        {!repos.length && (
          <p className="text-sm text-muted-foreground">
            No repositories registered. Run factory init in a project to get started.
          </p>
        )}
        <ul className="divide-y divide-border rounded-lg border border-border">
          {repos.map((repo) => (
            <li key={repo.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{repo.name}</p>
                <p className="break-all font-mono text-xs text-muted-foreground">{repo.path}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="live">{repo.counts.running} running</Badge>
                <Badge variant={repo.counts["needs-you"] ? "warn" : "outline"}>
                  {repo.counts["needs-you"]} need you
                </Badge>
                <Badge>{repo.counts.queued} queued</Badge>
              </div>
              {repo.status === "available" ? (
                <Button variant="secondary" asChild>
                  <a href={`#/repo/${encodeURIComponent(repo.id)}`}>Open board</a>
                </Button>
              ) : (
                <p className="text-sm text-warn-fg">
                  {repo.status === "missing" ? "Repository missing" : "Upgrade required"}
                  {repo.detail ? ` · ${repo.detail}` : ""}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>
    </PageShell>
  );
}
