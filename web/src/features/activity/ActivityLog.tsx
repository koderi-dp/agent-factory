import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api, keys } from "@/lib/api";
import { clockTime, relativeTime } from "@/lib/format";
import { describeEvent, eventTypeLabel } from "@/features/overview/model";
import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { Select } from "@/ui/primitives/select";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";
import { statusLabel } from "@/ui/status-text";

const PAGE_SIZE = 50;
const ROW = "flex flex-wrap items-center gap-x-4 gap-y-1 p-3 text-sm";

export function ActivityLog({
  repo,
  status,
  onRepoChange,
  onStatusChange,
}: {
  repo?: string;
  status?: string;
  onRepoChange: (repo: string) => void;
  onStatusChange: (status: string) => void;
}) {
  const query = useInfiniteQuery({
    queryKey: keys.activityLog(repo),
    queryFn: ({ pageParam }) => api.activity({ repo, cursor: pageParam, limit: PAGE_SIZE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next ?? undefined,
  });
  const pages = query.data?.pages ?? [];
  const repos = pages[0]?.repos ?? [];
  const loaded = pages.flatMap((page) => page.events);
  const events = status ? loaded.filter((event) => event.state === status) : loaded;
  const availableRepos = new Set(repos.filter((r) => r.status === "available").map((r) => r.id));
  const statuses = [
    ...new Set([
      ...loaded.flatMap((event) => (event.state ? [event.state] : [])),
      ...(status ? [status] : []),
    ]),
  ].sort();
  const unavailable = repos.filter((r) => r.status !== "available");
  return (
    <PageShell>
      <PageHeader
        title="Activity"
        description="What happened across all repositories, newest first."
        actions={
          <>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Repository
              <Select value={repo ?? ""} onChange={(e) => onRepoChange(e.target.value)}>
                <option value="">All repositories</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Status
              <Select value={status ?? ""} onChange={(e) => onStatusChange(e.target.value)}>
                <option value="">All statuses</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </Select>
            </label>
          </>
        }
      />
      {unavailable.map((r) => (
        <p key={r.id} className="text-sm text-warn-fg">
          {r.name}:{" "}
          {r.detail ?? (r.status === "missing" ? "Repository missing" : "Upgrade required")}
        </p>
      ))}
      {query.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading activity…
        </p>
      )}
      {query.isError && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <p role="alert">Could not load activity: {query.error.message}</p>
          <Button variant="secondary" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {query.data && events.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {loaded.length === 0 ? "No activity yet" : "No events match these filters"}
          {loaded.length > 0 && query.hasNextPage ? " (older pages may still match)" : ""}
        </p>
      )}
      {events.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {events.map((event) => {
            const body = (
              <>
                <time
                  dateTime={event.ts}
                  title={relativeTime(event.ts)}
                  className="font-mono text-xs text-muted-foreground"
                >
                  {clockTime(event.ts)}
                </time>
                <Badge variant="outline">{eventTypeLabel(event.type)}</Badge>
                {event.job_id ? (
                  <span className="font-medium">{event.job_title ?? event.job_id}</span>
                ) : (
                  <span className="text-muted-foreground">Repository</span>
                )}
                <span className="text-muted-foreground">{event.repo_name}</span>
                <span>{describeEvent(event)}</span>
              </>
            );
            return (
              <li key={`${event.repo_id}:${event.id}`}>
                {!availableRepos.has(event.repo_id) ? (
                  <div className={ROW}>{body}</div>
                ) : event.job_id && event.job_title ? (
                  <Link
                    to="/repo/$repo/job/$job"
                    params={{ repo: event.repo_id, job: event.job_id }}
                    className={`${ROW} hover:bg-inset`}
                  >
                    {body}
                  </Link>
                ) : (
                  <Link
                    to="/repo/$repo"
                    params={{ repo: event.repo_id }}
                    className={`${ROW} hover:bg-inset`}
                  >
                    {body}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {query.hasNextPage && (
        <div>
          <Button
            variant="secondary"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? "Loading…" : "Load older"}
          </Button>
        </div>
      )}
    </PageShell>
  );
}
