import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { api, keys } from "@/lib/api";
import { connectOverview, connectRepo } from "@/lib/sse";
import { Board } from "@/features/board/Board";
import { JobDetail } from "@/features/job/JobDetail";
import { JobsList } from "@/features/jobs/JobsList";
import { PipelinesPage } from "@/features/pipelines/PipelinesPage";
import { RepositoriesPage } from "@/features/repositories/RepositoriesPage";
import { UsagePage } from "@/features/usage/UsagePage";
import { ActivityLog } from "@/features/activity/ActivityLog";
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
  const search = useSearch({ strict: false }) as {
    state?: string;
    repo?: string;
    status?: string;
  };
  const [overviewConnection, setOverviewConnection] = useState<Connection>("connecting");
  const [repoConnection, setRepoConnection] = useState<{ repo?: string; state: Connection }>({
    state: "connecting",
  });
  const [now, setNow] = useState(() => Date.now());
  const overview = useQuery({ queryKey: keys.overview, queryFn: api.overview });
  const repos = overview.data?.repos ?? [];
  // The sidebar choice: a repo page's path, else ?repo= on any other page, else the only repo.
  const repo = params.repo ?? search.repo ?? (repos.length === 1 ? repos[0].id : undefined);
  const selected = repos.find((r) => r.id === repo);
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
  const connection =
    overviewConnection !== "connected"
      ? overviewConnection
      : repo && available
        ? repoConnection.repo === repo
          ? repoConnection.state
          : "connecting"
        : overviewConnection;
  const base = repo ? `/repo/${encodeURIComponent(repo)}` : "";
  const keep: Record<string, string> = repo ? { repo } : {};
  const onRepoPage = (suffix: string) => !!params.repo && pathname === `${base}${suffix}`;
  // One fixed menu. Pages that need a single repository ask for one when "All" is chosen.
  const nav: { label: string; to: string; search?: Record<string, string>; active: boolean }[] = [
    { label: "Overview", to: "/", search: keep, active: pathname === "/" },
    {
      label: "Board",
      to: repo ? base : "/board",
      active: pathname === "/board" || onRepoPage("") || (!!params.repo && !!params.job),
    },
    { label: "Jobs", to: "/jobs", search: keep, active: pathname === "/jobs" },
    {
      label: "Backlog",
      to: repo ? `${base}/backlog` : "/backlog",
      active: pathname === "/backlog" || onRepoPage("/backlog"),
    },
    { label: "Pipelines", to: "/pipelines", search: keep, active: pathname === "/pipelines" },
    { label: "Activity", to: "/activity", search: keep, active: pathname === "/activity" },
    { label: "Usage", to: "/usage", search: keep, active: pathname === "/usage" },
    {
      label: "Repositories",
      to: "/repositories",
      search: keep,
      active: pathname === "/repositories",
    },
    {
      label: "Repo settings",
      to: repo ? `${base}/settings` : "/repo-settings",
      active: pathname === "/repo-settings" || onRepoPage("/settings"),
    },
    { label: "Machine settings", to: "/settings", search: keep, active: pathname === "/settings" },
  ];
  /** Switch repository and stay on the same kind of page. */
  function chooseRepo(next: string) {
    const section =
      pathname === "/backlog" || onRepoPage("/backlog")
        ? "backlog"
        : pathname === "/repo-settings" || onRepoPage("/settings")
          ? "settings"
          : pathname === "/board" || params.repo
            ? "board"
            : null;
    if (section) {
      const repoPath = { board: "", backlog: "/backlog", settings: "/settings" }[section];
      const allPath = { board: "/board", backlog: "/backlog", settings: "/repo-settings" }[section];
      void navigate({ to: next ? `/repo/${encodeURIComponent(next)}${repoPath}` : allPath });
      return;
    }
    void navigate({ to: pathname, search: next ? { repo: next } : {} });
  }
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
  else if (pathname === "/board" || pathname === "/backlog" || pathname === "/repo-settings")
    page = <PickRepo repos={repos} path={pathname} />;
  else if (params.repo && !selected)
    page = (
      <PageShell>
        <p role="alert">Repository not found.</p>
      </PageShell>
    );
  else if (params.repo && selected && !available)
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
    params.repo &&
    selected &&
    !params.job &&
    !pathname.endsWith("/backlog") &&
    !pathname.endsWith("/settings")
  )
    page = <Board repo={selected.id} name={selected.name} now={now} />;
  else if (params.repo && selected && params.job)
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
        repo={repo}
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
  else if (pathname === "/activity")
    page = (
      <ActivityLog
        repo={repo}
        status={search.status}
        onRepoChange={(next) =>
          void navigate({
            to: "/activity",
            search: {
              ...(next ? { repo: next } : {}),
              ...(search.status ? { status: search.status } : {}),
            },
            replace: true,
          })
        }
        onStatusChange={(next) =>
          void navigate({
            to: "/activity",
            search: {
              ...(search.repo ? { repo: search.repo } : {}),
              ...(next ? { status: next } : {}),
            },
            replace: true,
          })
        }
      />
    );
  else if (pathname === "/usage") page = <UsagePage />;
  else if (pathname === "/pipelines")
    page = <PipelinesPage repos={selected ? [selected] : overview.data.repos} />;
  else if (pathname === "/repositories") page = <RepositoriesPage repos={overview.data.repos} />;
  else if (pathname === "/")
    page = <Overview repos={selected ? [selected] : overview.data.repos} />;
  else if (params.repo && selected && pathname.endsWith("/backlog"))
    page = <Backlog key={selected.id} repo={selected.id} />;
  else if (params.repo && selected && pathname.endsWith("/settings"))
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
          <Select value={repo ?? ""} onChange={(e) => chooseRepo(e.target.value)}>
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
              <Link
                to={item.to}
                search={item.search ?? {}}
                aria-current={item.active ? "page" : undefined}
              >
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

const PICK: Record<string, { title: string; suffix: string }> = {
  "/board": { title: "Board", suffix: "" },
  "/backlog": { title: "Backlog", suffix: "/backlog" },
  "/repo-settings": { title: "Repo settings", suffix: "/settings" },
};

function PickRepo({
  repos,
  path,
}: {
  repos: { id: string; name: string; status: string }[];
  path: string;
}) {
  const { title, suffix } = PICK[path];
  const usable = repos.filter((r) => r.status === "available");
  return (
    <PageShell>
      <h1 className="text-2xl font-medium">{title}</h1>
      <p className="text-sm text-muted-foreground">
        {title} shows one repository. Pick one here or in the sidebar.
      </p>
      {usable.length ? (
        <ul className="grid gap-2">
          {usable.map((r) => (
            <li key={r.id}>
              <Link className="underline" to={`/repo/${encodeURIComponent(r.id)}${suffix}`}>
                {r.name}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          No repositories registered. Run factory init in a project to get started.
        </p>
      )}
    </PageShell>
  );
}
