import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, keys, report, type LegacyJob } from "@/lib/api";
import { Button } from "@/ui/primitives/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { ReportView } from "@/features/job/ReportView";

function LegacyReport({ repo, job, path }: { repo: string; job: string; path: string }) {
  const query = useQuery({
    queryKey: ["legacy-report", repo, job, path],
    queryFn: () => report(repo, job, path),
  });
  return query.isPending ? (
    <p role="status">Loading report…</p>
  ) : query.isError ? (
    <p role="alert">{query.error.message}</p>
  ) : (
    <ReportView text={query.data} />
  );
}

export function LegacyJobCard({ repo, job }: { repo: string; job: LegacyJob }) {
  const [path, setPath] = useState("");
  const [action, setAction] = useState<"approve" | "delete" | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      api.action(repo, job.id, action!, { confirm_sensitive: confirmed, force: confirmed }),
    onSuccess: async () => {
      setAction(null);
      await client.invalidateQueries({ queryKey: keys.state(repo) });
    },
  });
  return (
    <article className="mt-3 space-y-3 rounded border p-3">
      <h3 className="text-sm font-medium">
        {job.id}: {job.title}
      </h3>
      <p className="text-sm text-muted-foreground">Legacy (read-only) · {job.stage}</p>
      <div className="flex flex-wrap gap-2">
        {job.reports.map((name) => (
          <Button key={name} variant="ghost" onClick={() => setPath(name)}>
            {name}
          </Button>
        ))}
        {["approve", "approved", "needs-human"].includes(job.stage) && (
          <Button
            onClick={() => {
              setConfirmed(false);
              setAction("approve");
            }}
          >
            Merge
          </Button>
        )}
        <Button
          variant="destructive"
          onClick={() => {
            setConfirmed(false);
            setAction("delete");
          }}
        >
          Delete
        </Button>
      </div>
      {path && <LegacyReport repo={repo} job={job.id} path={path} />}
      <ConfirmDialog
        open={action !== null}
        title={action === "delete" ? "Delete legacy job" : "Merge legacy job"}
        description={
          action === "delete"
            ? "Remove the worktree, branch and job folder."
            : "Merge the completed legacy job into main."
        }
        destructive={action === "delete"}
        pending={mutation.isPending}
        onOpenChange={(open) => {
          if (!open) setAction(null);
        }}
        onConfirm={() => mutation.mutate()}
      >
        <label className="text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          {action === "delete"
            ? " Force deletion of an unfinished legacy job (stop its old session first)"
            : " Confirm changes to sensitive paths"}
        </label>
        {mutation.isError && <p role="alert">{mutation.error.message}</p>}
      </ConfirmDialog>
    </article>
  );
}
