import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { api, keys } from "@/lib/api";
import { connectOverview, connectRepo } from "@/lib/sse";
import { Board } from "@/features/board/Board";
import { JobDetail } from "@/features/job/JobDetail";
import { JobsList } from "@/features/jobs/JobsList";
import { Overview } from "@/features/overview/Overview";
import { Button } from "@/ui/primitives/button";
import { Select } from "@/ui/primitives/select";
import { PageShell } from "@/ui/page-shell";
import { StatusBadge } from "@/ui/status";

import { Backlog } from "@/features/backlog/Backlog";
import { RepoSettings } from "@/features/settings/RepoSettings";
import { MachineSettings } from "@/features/settings/MachineSettings";

type Connection = "connecting" | "connected" | "disconnected";

export function Shell() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const pathname = useLocation({ select: (location) => location.pathname });
  const params = useParams({ strict: false }) as { repo?: string; job?: string };
  const repo = params.repo;
  const search = useSearch({ strict: false }) as { state?: string; repo?: string };
  const [overviewConnection, setOverviewConnection] = useState<Connection>("connecting");
  const [repoConnection, setRepoConnection] = useState<{ repo?: string; state: Connection }>({
    state: "connecting",
  });
  const [now, setNow] = useState(() => Date.now());
  const overview = useQuery({ queryKey: keys.overview, queryFn: api.overview });
  const selected = overview.data?.repos.find((r) => r.id === repo);
  const available = selected?.status === "available";
  useEffect(() => connectOverview(client, setOverviewConnection), [client]);
  useEffect(() => {
    if (repo && available)
      return connectRepo(client, repo, (state) => setRepoConnection({ repo, state }));
  }, [client, repo, available]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10_000);
    return () => clearInterval(timer);
  }, []);
  const needsYou = overview.data?.repos.reduce((n, r) => n + r.counts["needs-you"], 0) ?? 0;
  useEffect(() => {
    document.title = `${needsYou ? `(${needsYou}) ` : ""}Agent Factory`;
  }, [needsYou]);
  useEffect(() => {
    if (
      pathname === "/" &&
      overview.data?.repos.length === 1 &&
      overview.data.repos[0].status === "available"
    )
      void navigate({
        to: "/repo/$repo",
        params: { repo: overview.data.repos[0].id },
        replace: true,
      });
  }, [navigate, pathname, overview.data]);
  const connection =
    overviewConnection !== "connected"
      ? overviewConnection
      : repo && available
        ? repoConnection.repo === repo
          ? repoConnection.state
          : "connecting"
        : overviewConnection;
  const base = repo ? `/repo/${encodeURIComponent(repo)}` : "";
  const nav = [
    // With one repository, "/" redirects to its board, so Overview would be unreachable.
    ...(overview.data?.repos.length === 1
      ? []
      : [{ label: "Overview", href: "/", active: pathname === "/" }]),
    { label: "Jobs", href: "/jobs", active: pathname === "/jobs" },
    ...(repo
      ? [
          { label: "Board", href: base, active: pathname === `/repo/${repo}` },
          { label: "Backlog", href: `${base}/backlog`, active: pathname.endsWith("/backlog") },
          {
            label: "Repo settings",
            href: `${base}/settings`,
            active: pathname.endsWith("/settings"),
          },
        ]
      : []),
    { label: "Machine settings", href: "/settings", active: pathname === "/settings" },
  ];
  let page;
  if (overview.isPending)
    page = (
      <PageShell>
        <p role="status">Loading repositories…</p>
      </PageShell>
    );
  else if (overview.isError)
    page = (
      <PageShell>
        <p role="alert">Could not load repositories: {overview.error.message}</p>
        <Button onClick={() => void overview.refetch()}>Retry</Button>
      </PageShell>
    );
  else if (repo && !selected)
    page = (
      <PageShell>
        <p role="alert">Repository not found.</p>
      </PageShell>
    );
  else if (selected && !available)
    page = (
      <PageShell>
        <p role="alert">
          {selected.name}:{" "}
          {selected.detail ??
            (selected.status === "missing" ? "Repository missing" : "Upgrade required")}
        </p>
      </PageShell>
    );
  else if (
    selected &&
    !params.job &&
    !pathname.endsWith("/backlog") &&
    !pathname.endsWith("/settings")
  )
    page = <Board repo={selected.id} name={selected.name} now={now} />;
  else if (selected && params.job)
    page = (
      <JobDetail
        key={`${repo}:${params.job}`}
        repo={selected.id}
        job={params.job}
        name={selected.name}
      />
    );
  else if (pathname === "/jobs")
    page = (
      <JobsList
        state={search.state}
        repo={search.repo}
        repos={overview.data.repos}
        onFilter={(next) =>
          void navigate({
            to: "/jobs",
            search: {
              ...(next.state ? { state: next.state } : {}),
              ...(next.repo ? { repo: next.repo } : {}),
            },
          })
        }
      />
    );
  else if (pathname === "/") page = <Overview repos={overview.data.repos} />;
  else if (selected && pathname.endsWith("/backlog"))
    page = <Backlog key={selected.id} repo={selected.id} />;
  else if (selected && pathname.endsWith("/settings"))
    page = <RepoSettings key={selected.id} repo={selected.id} />;
  else page = <MachineSettings />;
  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <button
        onClick={() => document.getElementById("factory-content")?.focus()}
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-3"
      >
        Skip to content
      </button>
      <aside className="flex shrink-0 flex-col gap-4 border-b border-border bg-inset p-4 md:w-60 md:border-r md:border-b-0">
        <Link to="/" className="text-lg font-medium tracking-tight">
          Agent Factory
        </Link>
        <label className="space-y-2 text-xs text-muted-foreground">
          Repository
          <Select
            value={repo ?? ""}
            onChange={(e) =>
              void navigate({
                to: e.target.value ? "/repo/$repo" : "/",
                params: { repo: e.target.value },
              })
            }
          >
            <option value="">All repositories</option>
            {overview.data?.repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} · {r.counts["needs-you"]} need you
                {r.status !== "available" ? " · unavailable" : ""}
              </option>
            ))}
          </Select>
        </label>
        <nav aria-label="Main navigation" className="flex flex-wrap gap-1 md:flex-col">
          {nav.map((item) => (
            <Button
              key={item.label}
              asChild
              variant={item.active ? "secondary" : "ghost"}
              className="justify-start"
            >
              <Link to={item.href} aria-current={item.active ? "page" : undefined}>
                {item.label}
              </Link>
            </Button>
          ))}
        </nav>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border px-4 py-3 text-xs">
          <span className="text-muted-foreground">
            {selected?.name ?? "All repositories"} · {needsYou} need you
          </span>
          <span role="status">
            <StatusBadge state={connection} />
          </span>
        </header>
        <main id="factory-content" tabIndex={-1} className="min-h-0 flex-1 overflow-auto">
          {page}
        </main>
      </div>
    </div>
  );
}
