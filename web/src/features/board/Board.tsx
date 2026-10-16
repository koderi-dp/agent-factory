import { useQuery } from "@tanstack/react-query";
import { api, keys } from "@/lib/api";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";
import { Badge } from "@/ui/primitives/badge";
import { JobCard } from "./JobCard";
import { boardColumns, boardGroups, jobColumn } from "./model";

export function Board({ repo, name, now }: { repo: string; name: string; now: number }) {
  const query = useQuery({ queryKey: keys.state(repo), queryFn: () => api.state(repo) });
  if (query.isPending)
    return (
      <PageShell>
        <p role="status">Loading board…</p>
      </PageShell>
    );
  if (query.isError)
    return (
      <PageShell>
        <p role="alert">Could not load board: {query.error.message}</p>
      </PageShell>
    );
  const state = query.data;
  return (
    <PageShell gap="loose">
      <PageHeader
        kicker="Board"
        title={name}
        description={`${state.jobs.length} jobs · ${state.backlog.length} in backlog`}
      />
      {!state.jobs.length && (
        <p className="text-sm text-muted-foreground">
          No jobs yet. Start a job with factory start or from the backlog.
        </p>
      )}
      {boardGroups(state).map((group, i) => {
        const columns = boardColumns(group.steps, group.jobs);
        return (
          <section key={i} aria-label={`${group.name} pipeline`} className="space-y-3">
            <h2 className="text-sm font-medium">{group.name}</h2>
            <div
              className="flex gap-4 overflow-x-auto pb-3"
              tabIndex={0}
              aria-label={`${group.name} board columns`}
            >
              {columns.map((column) => {
                const jobs = group.jobs.filter((j) => jobColumn(j, group.steps) === column.id);
                return (
                  <section
                    key={column.id}
                    aria-label={column.label}
                    className="w-80 shrink-0 space-y-3 rounded-lg bg-inset p-3"
                  >
                    <h3 className="flex items-center justify-between text-sm font-medium">
                      {column.label}
                      <Badge>{jobs.length}</Badge>
                    </h3>
                    {jobs.map((job) => (
                      <JobCard key={job.id} repo={repo} job={job} state={state} now={now} />
                    ))}
                    {!jobs.length && <p className="py-4 text-xs text-muted-foreground">No jobs</p>}
                  </section>
                );
              })}
            </div>
          </section>
        );
      })}
      <details className="rounded-lg bg-card p-4 shadow-[var(--shadow-border)]">
        <summary className="cursor-pointer text-sm font-medium">Legacy jobs · read-only</summary>
        {/* TODO (session 4.2, §15.6): render migrated legacy jobs when the API exposes them. */}
        <p className="mt-3 text-sm text-muted-foreground">
          Legacy job history will appear here after migration.
        </p>
      </details>
    </PageShell>
  );
}
