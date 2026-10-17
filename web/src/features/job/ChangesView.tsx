import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, keys, type ChangedFile } from "@/lib/api";
import { mergeReadiness, type Execution, type Job } from "@/features/board/model";
import { Badge } from "@/ui/primitives/badge";
import { cn } from "@/ui/utils";

const headerLine = /^(diff |index |new file|deleted file|similarity|rename|old mode|new mode|@@)/;

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-muted-foreground";
  if (line.startsWith("+")) return "bg-signal/15 text-signal-fg";
  if (line.startsWith("-")) return "bg-danger/15 text-danger-fg";
  if (headerLine.test(line)) return "text-muted-foreground";
  return "";
}

function DiffPane({
  repo,
  job,
  file,
  version,
}: {
  repo: string;
  job: string;
  file: ChangedFile;
  version: string;
}) {
  const query = useQuery({
    queryKey: keys.changeDiff(repo, job, file.path, version),
    queryFn: () => api.changeDiff(repo, job, file.path),
  });
  if (query.isPending) return <p role="status">Loading diff…</p>;
  if (query.isError) return <p role="alert">Could not load diff: {query.error.message}</p>;
  const { diff, binary, truncated } = query.data;
  if (binary) return <p className="text-sm text-muted-foreground">Binary file — no text diff</p>;
  return (
    <div className="space-y-2">
      {truncated && (
        <p role="note" className="text-sm text-muted-foreground">
          This diff was truncated; the output was cut.
        </p>
      )}
      <pre
        aria-label={`Diff of ${file.path}`}
        className="overflow-x-auto rounded bg-secondary py-2 font-mono text-xs"
      >
        {diff.split("\n").map((line, i) => (
          <div key={i} className={cn("whitespace-pre px-3", lineClass(line))}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}

export function ChangesView({
  repo,
  job,
  executions,
}: {
  repo: string;
  job: Job;
  executions: Execution[];
}) {
  const version = job.updated_at;
  const query = useQuery({
    queryKey: keys.changes(repo, job.id, version),
    queryFn: () => api.changes(repo, job.id),
  });
  const [picked, setPicked] = useState<string>();
  const readiness = mergeReadiness(job, executions);
  let body;
  if (query.isPending) body = <p role="status">Loading changes…</p>;
  else if (query.isError) body = <p role="alert">Could not load changes: {query.error.message}</p>;
  else if (query.data.files.length === 0) body = <p>This job has not changed any files.</p>;
  else {
    const { files, truncated } = query.data;
    const selected = files.find((f) => f.path === picked) ?? files[0]!;
    body = (
      <div className="space-y-3">
        {truncated && (
          <p role="note" className="text-sm text-muted-foreground">
            The list of changed files was truncated; the output was cut.
          </p>
        )}
        <div className="flex flex-col gap-4 md:flex-row">
          <ul aria-label="Changed files" className="space-y-1 md:w-80 md:shrink-0">
            {files.map((f) => (
              <li key={f.path}>
                <button
                  type="button"
                  aria-pressed={f.path === selected.path}
                  onClick={() => setPicked(f.path)}
                  className={cn(
                    "w-full rounded p-2 text-left text-sm",
                    f.path === selected.path ? "bg-secondary" : "hover:bg-secondary/50",
                  )}
                >
                  <span className="block break-all font-mono text-xs">
                    {f.kind === "renamed" && f.old_path ? `${f.old_path} → ${f.path}` : f.path}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {f.kind.replace("_", " ")} ·{" "}
                    {f.binary || f.additions == null || f.deletions == null
                      ? "binary"
                      : `+${f.additions} −${f.deletions}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0 flex-1">
            <DiffPane
              key={selected.path}
              repo={repo}
              job={job.id}
              file={selected}
              version={version}
            />
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={readiness.ready ? "live" : "warn"}>{readiness.label}</Badge>
        <span>{readiness.detail}</span>
      </p>
      {body}
    </div>
  );
}
