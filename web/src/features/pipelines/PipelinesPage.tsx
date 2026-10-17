import { useQueries } from "@tanstack/react-query";
import type { z } from "zod";
import { api, keys, type configSchema, type overviewSchema } from "@/lib/api";
import { Badge } from "@/ui/primitives/badge";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";

type Steps = z.infer<typeof configSchema>["pipelines"][string];

const executionCount = (steps: Steps) =>
  steps.reduce((n, step) => n + ("parallel" in step ? step.parallel.length : 1), 0);

function pipelineText(name: string, steps: Steps) {
  const stages = steps.length;
  const executions = executionCount(steps);
  return `${name} · ${stages} ${stages === 1 ? "stage" : "stages"}${
    executions > stages ? ` · ${executions} executions` : ""
  }`;
}

export function PipelinesPage({ repos }: z.infer<typeof overviewSchema>) {
  const states = useQueries({
    queries: repos.map((repo) => ({
      queryKey: keys.state(repo.id),
      queryFn: () => api.state(repo.id),
      enabled: repo.status === "available",
    })),
  });
  return (
    <PageShell gap="loose">
      <PageHeader
        title="Pipelines"
        description="Configured pipelines for every registered repository."
      />
      {!repos.length && (
        <p className="text-sm text-muted-foreground">
          No repositories registered. Run factory init in a project to get started.
        </p>
      )}
      {repos.map((repo, index) => {
        const query = states[index];
        const heading = `pipelines-${repo.id}`;
        return (
          <section key={repo.id} aria-labelledby={heading} className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id={heading} className="text-sm font-medium">
                {repo.name}
              </h2>
              {repo.status === "available" && (
                <a
                  className="text-sm underline"
                  href={`#/repo/${encodeURIComponent(repo.id)}/settings`}
                >
                  Edit in settings
                </a>
              )}
            </div>
            {repo.status !== "available" ? (
              <p className="text-sm text-warn-fg">
                {repo.status === "missing" ? "Repository missing" : "Upgrade required"}
                {repo.detail ? ` · ${repo.detail}` : ""}
              </p>
            ) : query.isPending ? (
              <p role="status" className="text-sm text-muted-foreground">
                Loading pipelines…
              </p>
            ) : query.isError ? (
              <p role="alert" className="text-sm">
                Could not load pipelines: {query.error.message}
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {Object.entries(query.data.config.pipelines).map(([name, steps]) => (
                  <li key={name} className="flex flex-wrap items-center gap-3 p-3 text-sm">
                    <span>{pipelineText(name, steps)}</span>
                    {name === query.data.config.defaultPipeline && (
                      <Badge variant="outline">default</Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </PageShell>
  );
}
