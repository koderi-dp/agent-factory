import type { Job, Execution } from "@/features/board/model";
export function PauseBanner({ job, executions = [] }: { job: Job; executions?: Execution[] }) {
  if (!["paused", "needs_human"].includes(job.state)) return null;
  return (
    <div role="status" className="rounded-lg bg-warn/10 p-4 text-sm text-warn">
      <strong>{job.state === "paused" ? "Paused" : "Needs you"}</strong> ·{" "}
      {job.pause_reason ?? job.note ?? "Review the job before continuing."}
      {job.pause_reason === "auth" && (
        <p>
          Run{" "}
          <code>
            {executions.find((e) => ["stopped", "failed", "running"].includes(e.state))
              ?.provider === "claude"
              ? "claude login"
              : "codex login"}
          </code>
          , then resume.
        </p>
      )}
      {job.note && job.pause_reason && <p>{job.note}</p>}
    </div>
  );
}
