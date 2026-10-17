import { currentExecutions, type Execution, type Step } from "@/features/board/model";
import { Button } from "@/ui/primitives/button";
import { StatusBadge } from "@/ui/status";
import { nodeIdFor } from "./intervention";

const outcomes: Record<string, string> = {
  PASS: "Passed",
  CHANGES: "Changes requested",
  APPROVE: "Approved",
  ESCALATE: "Escalated to you",
};

export function ReviewerCards({
  steps,
  executions,
  onOpenReport,
}: {
  steps: Step[];
  executions: Execution[];
  onOpenReport: (nodeId: string) => void;
}) {
  const current = currentExecutions(steps, executions);
  const reviewSteps = steps.filter((s) => s.type === "review" || s.type === "approval");
  if (!reviewSteps.length) return <p>This pipeline has no review stages.</p>;
  return (
    <ul aria-label="Reviewer outcomes" className="space-y-5">
      {reviewSteps.map((step) => {
        const parallel = step.executions.length > 1;
        return (
          <li key={step.id}>
            <h3 className="mb-2 font-medium">
              {step.label || step.id}
              {parallel && (
                <span className="ml-2 text-sm text-muted-foreground">
                  parallel · {step.executions.length} reviewers
                </span>
              )}
            </h3>
            <ul className="grid gap-3 sm:grid-cols-2">
              {step.executions.map((entry) => {
                const row = current.find((e) => e.step_id === step.id && e.exec_id === entry.id);
                const result = row?.state === "done" && row.result ? row.result : "";
                const profile = entry.profiles?.[entry.provider ?? ""];
                const provider = row?.provider ?? entry.provider;
                const model = row?.model ?? profile?.model;
                return (
                  <li key={entry.id} className="space-y-2 rounded-lg border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <strong>{entry.id}</strong>
                      <StatusBadge
                        state={result ? result.toLowerCase() : (row?.state ?? "pending")}
                      />
                    </div>
                    <p className="text-muted-foreground">
                      {[provider, model].filter(Boolean).join(" · ") || "—"}
                    </p>
                    <p>{result ? (outcomes[result] ?? result) : "Not finished"}</p>
                    {row?.report_path ? (
                      <Button variant="outline" onClick={() => onOpenReport(nodeIdFor(steps, row))}>
                        Open full report
                      </Button>
                    ) : (
                      <span className="text-muted-foreground">No report yet</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </li>
        );
      })}
    </ul>
  );
}
