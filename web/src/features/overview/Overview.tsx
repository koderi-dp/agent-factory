import type { z } from "zod";
import type { overviewSchema } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/ui/primitives/card";
import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";

export function Overview({ repos }: z.infer<typeof overviewSchema>) {
  return (
    <PageShell gap="loose">
      <PageHeader
        kicker="Agent Factory"
        title="Overview"
        description="All your repositories, in one place."
      />
      {!repos.length && (
        <p className="text-sm text-muted-foreground">
          No repositories registered. Run factory init in a project to get started.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {repos.map((repo) => (
          <Card key={repo.id}>
            <CardHeader>
              <CardTitle>{repo.name}</CardTitle>
              <p className="break-all font-mono text-xs text-muted-foreground">{repo.path}</p>
            </CardHeader>
            <CardContent className="space-y-4">
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
            </CardContent>
          </Card>
        ))}
      </div>
    </PageShell>
  );
}
