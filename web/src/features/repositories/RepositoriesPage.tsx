import type { z } from "zod";
import type { overviewSchema } from "@/lib/api";
import { Badge } from "@/ui/primitives/badge";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";

const STATUS = {
  available: { label: "Available", variant: "live" },
  missing: { label: "Missing", variant: "danger" },
  version_mismatch: { label: "Upgrade required", variant: "warn" },
} as const;

export function RepositoriesPage({ repos }: z.infer<typeof overviewSchema>) {
  return (
    <PageShell gap="loose">
      <PageHeader
        title="Repositories"
        description="Registered repositories with their status and current workload."
      />
      {!repos.length && (
        <p className="text-sm text-muted-foreground">
          No repositories registered. Run factory init in a project to get started.
        </p>
      )}
      <ul className="divide-y divide-border rounded-lg border border-border">
        {repos.map((repo) => {
          const status = STATUS[repo.status];
          return (
            <li key={repo.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3 text-sm">
              <div className="min-w-0 flex-1">
                {repo.status === "available" ? (
                  <a
                    className="font-medium hover:underline"
                    href={`#/repo/${encodeURIComponent(repo.id)}`}
                  >
                    {repo.name}
                  </a>
                ) : (
                  <p className="font-medium">{repo.name}</p>
                )}
                <p className="break-all font-mono text-xs text-muted-foreground">{repo.path}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={status.variant}>{status.label}</Badge>
                {repo.detail && <span className="text-xs text-warn-fg">{repo.detail}</span>}
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="live">{repo.counts.running} active</Badge>
                <Badge>{repo.counts.queued} queued</Badge>
                <Badge variant={repo.counts["needs-you"] ? "warn" : "outline"}>
                  {repo.counts["needs-you"]} need you
                </Badge>
              </div>
            </li>
          );
        })}
      </ul>
    </PageShell>
  );
}
