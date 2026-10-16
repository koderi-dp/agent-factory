import { currentExecutions, type Execution, type Step } from "@/features/board/model";

export type CompactState = "pending" | "running" | "done" | "changes" | "failed";

/** Shared step projection for compact mode; full geometry follows in session 3.4 (§13.6). */
export function compactLayout(steps: Step[], executions: Execution[]) {
  const current = currentExecutions(steps, executions);
  return steps.map((step) => {
    const rows = current.filter((e) => e.step_id === step.id);
    let state: CompactState = "pending";
    if (rows.some((e) => e.state === "failed" || ["BLOCKED", "ESCALATE"].includes(e.result ?? "")))
      state = "failed";
    else if (rows.some((e) => e.result === "CHANGES")) state = "changes";
    else if (rows.some((e) => ["running", "claimed"].includes(e.state))) state = "running";
    else if (
      rows.length === step.executions.length &&
      rows.length &&
      rows.every((e) => e.state === "done")
    )
      state = "done";
    return { id: step.id, label: step.label || step.id, state };
  });
}
