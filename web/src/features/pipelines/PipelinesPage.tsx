import { useState } from "react";
import { Plus } from "lucide-react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { z } from "zod";
import { api, keys, libraryApi, type configSchema, type overviewSchema } from "@/lib/api";
import { PipelineSettingsForm } from "@/features/settings/PipelineSettingsForm";
import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { Card } from "@/ui/primitives/card";
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

type Editing = { repo: string; pipeline: string; focusNew: boolean };

export function PipelinesPage({ repos }: z.infer<typeof overviewSchema>) {
  const [editing, setEditing] = useState<Editing>();
  const client = useQueryClient();
  const library = useQuery({ queryKey: ["library"], queryFn: libraryApi.list });
  const remove = useMutation({
    mutationFn: libraryApi.remove,
    onSuccess: () => void client.invalidateQueries({ queryKey: ["library"] }),
  });
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
      <section aria-labelledby="pipeline-library" className="space-y-3">
        <h2 id="pipeline-library" className="text-sm font-medium">
          Library
        </h2>
        <p className="text-xs text-muted-foreground">
          Pipelines saved on this machine. Adding one to a repository copies it into that
          repository&apos;s config, so the repository keeps working for anyone who clones it.
        </p>
        {library.error && (
          <p role="alert" className="text-sm">
            Could not load the library: {library.error.message}
          </p>
        )}
        {remove.error && (
          <p role="alert" className="text-sm text-danger">
            {remove.error.message}
          </p>
        )}
        <ul className="divide-y divide-border rounded-lg border border-border">
          {library.data?.pipelines.map((item) => (
            <li key={item.name} className="flex flex-wrap items-center gap-3 p-3 text-sm">
              <span>{pipelineText(item.name, item.steps as Steps)}</span>
              {item.builtin && <Badge variant="outline">built-in</Badge>}
              {!item.builtin && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  aria-label={`Delete library pipeline ${item.name}`}
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(item.name)}
                >
                  Delete
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
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
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  icon={Plus}
                  onClick={() => setEditing({ repo: repo.id, pipeline: "", focusNew: true })}
                >
                  New pipeline
                </Button>
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
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="ml-auto"
                      aria-label={`Edit pipeline ${name}`}
                      onClick={() => setEditing({ repo: repo.id, pipeline: name, focusNew: false })}
                    >
                      Edit
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {editing?.repo === repo.id && (
              <Card className="grid gap-4 p-4">
                <h3 className="text-sm font-medium">Edit pipelines · {repo.name}</h3>
                <PipelineSettingsForm
                  key={`${editing.repo}:${editing.pipeline}:${editing.focusNew}`}
                  repo={repo.id}
                  initialPipeline={editing.pipeline}
                  focusNew={editing.focusNew}
                  onClose={() => setEditing(undefined)}
                />
              </Card>
            )}
          </section>
        );
      })}
    </PageShell>
  );
}
