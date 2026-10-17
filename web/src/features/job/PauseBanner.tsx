import type { Action, Execution, Job, Step } from "@/features/board/model";
import { Button } from "@/ui/primitives/button";
import { interventionFor } from "./intervention";

const actionLabels: Partial<Record<Action, string>> = {
  resume: "Resume",
  stop: "Stop",
  approve: "Merge",
  rework: "Rework",
};

export function InterventionPanel({
  job,
  steps,
  executions,
  actions,
  onAction,
  onOpenReport,
}: {
  job: Job;
  steps: Step[];
  executions: Execution[];
  actions: Action[];
  onAction: (action: Action) => void;
  onOpenReport: (nodeId: string) => void;
}) {
  const info = interventionFor(job, steps, executions);
  if (!info) return null;
  const buttons = actions.filter((a) => a !== "delete");
  return (
    <section
      aria-label="Intervention needed"
      className="space-y-3 rounded-lg bg-warn/10 p-4 text-sm text-warn"
    >
      <div>
        <p>
          <strong>{job.state === "paused" ? "Paused" : "Needs you"}</strong> · {info.title}
        </p>
        <p>{info.reason}</p>
        {info.detail && <p>{info.detail}</p>}
      </div>
      {info.stage && (
        <p>
          <strong>Requested by</strong> {info.stage.label}
          {info.stage.exec ? ` (${info.stage.exec})` : ""}
        </p>
      )}
      <div>
        <p>
          <strong>What is needed</strong>
        </p>
        <p>{info.required}</p>
        {job.pause_reason === "auth" && (
          <p>
            Run <code>{info.loginCommand}</code>, then resume.
          </p>
        )}
        {info.sensitivePaths.length > 0 && (
          <ul className="list-disc pl-5">
            {info.sensitivePaths.map((p) => (
              <li key={p}>
                <code>{p}</code>
              </li>
            ))}
          </ul>
        )}
      </div>
      {info.reports.length > 0 && (
        <div>
          <p>
            <strong>Relevant reports</strong>
          </p>
          <div className="flex flex-wrap gap-2">
            {info.reports.map((r) => (
              <Button key={r.nodeId} variant="outline" onClick={() => onOpenReport(r.nodeId)}>
                {r.step}/{r.exec} · {r.result}
              </Button>
            ))}
          </div>
        </div>
      )}
      {buttons.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {buttons.map((a) => (
            <Button key={a} variant="outline" onClick={() => onAction(a)}>
              {actionLabels[a]}
            </Button>
          ))}
        </div>
      ) : (
        <p>This job can only be inspected or deleted.</p>
      )}
    </section>
  );
}
