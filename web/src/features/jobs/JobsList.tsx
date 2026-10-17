import { useQuery } from "@tanstack/react-query";
import { api, jobStates, keys } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { Button } from "@/ui/primitives/button";
import { Select } from "@/ui/primitives/select";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";
import { StatusBadge } from "@/ui/status";
import { statusLabel } from "@/ui/status-text";

const LIMIT = 1000;
const jobHref = (repo: string, job: string) =>
  `#/repo/${encodeURIComponent(repo)}/job/${encodeURIComponent(job)}`;
const REPO_PROBLEMS: Record<string, string> = {
  missing: "Repository missing",
  version_mismatch: "Upgrade required",
  unreadable: "Could not be read",
};

export function JobsList({
  state,
  repo,
  repos,
  onFilter,
}: {
  state?: string;
  repo?: string;
  repos: { id: string; name: string }[];
  onFilter: (filter: { state?: string; repo?: string }) => void;
}) {
  const states = state ? [state] : jobStates;
  const query = useQuery({
    queryKey: [...keys.jobs(states, repo), "limit", LIMIT],
    queryFn: () => api.jobs({ states, repo, limit: LIMIT }),
  });
  const jobs = query.data?.jobs ?? [];
  const repoOptions = repo ? repos : (query.data?.repos ?? repos);
  const unavailable = (query.data?.repos ?? []).filter((r) => r.status !== "available");
  const filtered = Boolean(state || repo);
  return (
    <PageShell>
      <PageHeader
        title="Jobs"
        description={
          query.data
            ? `${jobs.length} ${jobs.length === 1 ? "job" : "jobs"} shown across all repositories.`
            : "Jobs across all repositories."
        }
        actions={
          <>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              State
              <Select
                value={state ?? ""}
                onChange={(e) => onFilter({ state: e.target.value || undefined, repo })}
              >
                <option value="">All states</option>
                {jobStates.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Repository
              <Select
                value={repo ?? ""}
                onChange={(e) => onFilter({ state, repo: e.target.value || undefined })}
              >
                <option value="">All repositories</option>
                {repoOptions.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </label>
          </>
        }
      />
      {unavailable.map((r) => (
        <p key={r.id} className="text-sm text-warn-fg">
          {r.name}: {REPO_PROBLEMS[r.status] ?? r.status}
          {r.detail ? ` · ${r.detail}` : ""}
        </p>
      ))}
      {query.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading jobs…
        </p>
      )}
      {query.isError && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <p role="alert">Could not load jobs: {query.error.message}</p>
          <Button variant="secondary" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </div>
      )}
      {query.data && jobs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {filtered ? "No jobs match these filters" : "No jobs yet"}
        </p>
      )}
      {jobs.length > 0 && (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {jobs.map((job) => (
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
              {job.pause_reason && (
                <span className="text-muted-foreground">{job.pause_reason}</span>
              )}
              <span className="text-muted-foreground">{relativeTime(job.updated_at)}</span>
            </li>
          ))}
        </ul>
      )}
      {query.data?.truncated && (
        <p className="text-sm text-muted-foreground">
          Only the {LIMIT} most recently updated jobs are shown.
        </p>
      )}
    </PageShell>
  );
}
