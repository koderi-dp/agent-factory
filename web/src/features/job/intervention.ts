import type { Execution, Job, Step } from "@/features/board/model";

type Entry = { title: string; reason: string; required: string };

const pausedEntries: Record<string, Entry> = {
  auth: {
    title: "Provider login expired",
    reason: "The agent could not authenticate with its provider.",
    required: "Log in again, then resume the job.",
  },
  permission: {
    title: "Permission denied",
    reason: "An agent was denied a permission it needed.",
    required: "Adjust the agent's permissions, then resume the job.",
  },
  process: {
    title: "Agent process stopped",
    reason: "The agent process stopped, or you stopped it.",
    required: "Resume the job to retry the stage.",
  },
  timeout: {
    title: "Execution timed out",
    reason: "The execution ran past its time limit.",
    required: "Resume the job to retry, or raise the time limit first.",
  },
  ports: {
    title: "No free port range",
    reason: "The factory found no free port range for the job.",
    required: "Free up ports, then resume the job.",
  },
  budget: {
    title: "Budget reached",
    reason: "The execution hit its budget.",
    required: "Raise the budget if the work should continue, then resume the job.",
  },
};

const waitingEntries: Record<string, Entry> = {
  contract: {
    title: "Report broke its contract",
    reason: "The agent's report did not follow its contract.",
    required: "Open the report, then rework the job with instructions or inspect the log.",
  },
  blocked: {
    title: "A task is blocked",
    reason: "A task reported BLOCKED and needs input.",
    required: "Read the report and rework the job with the missing instructions.",
  },
  changes: {
    title: "Changes still requested",
    reason:
      "Reviewers asked for changes and the rework rounds are used up, or a merge failed and was undone.",
    required: "Read the review reports, then rework the job with your own instructions.",
  },
  escalate: {
    title: "Approver escalated",
    reason: "The approver escalated the decision to you.",
    required: "Read the reports, then merge the job or rework it.",
  },
  verify: {
    title: "Waiting for a merge decision",
    reason: "The pipeline waits for a human merge decision.",
    required: "Review the reports, then merge the job or rework it.",
  },
  sensitive: {
    title: "Sensitive paths changed",
    reason: "The branch touches sensitive paths.",
    required: "Review the changes to these paths and confirm to merge.",
  },
  busy_merge: {
    title: "Main checkout not ready",
    reason: "The main checkout is not ready for the merge.",
    required: "Clean up the main checkout, then merge the job.",
  },
};

const reportsOf = {
  requester: ["blocked", "contract", "auth", "permission", "process", "timeout", "ports", "budget"],
  reviews: ["changes", "escalate", "verify", "sensitive", "busy_merge"],
};

export type InterventionReport = { nodeId: string; step: string; exec: string; result: string };
export type Intervention = {
  title: string;
  reason: string;
  required: string;
  detail: string | null;
  stage: { step: string; label: string; exec: string | null } | null;
  reports: InterventionReport[];
  sensitivePaths: string[];
  loginCommand: string;
};

/** Pipeline node id that opens an execution; matches `layout()` in the pipeline view. */
export function nodeIdFor(steps: Step[], execution: Pick<Execution, "step_id" | "exec_id">) {
  const step = steps.find((s) => s.id === execution.step_id);
  return step && step.executions.length > 1 ? `${step.id}/${execution.exec_id}` : execution.step_id;
}

function sensitivePathsOf(note: string | null | undefined): string[] {
  const match = /Sensitive paths:\s*([^;]*)/i.exec(note ?? "");
  return (match?.[1] ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

export function interventionFor(
  job: Job,
  steps: Step[],
  executions: Execution[],
): Intervention | null {
  if (job.state !== "paused" && job.state !== "needs_human") return null;
  const reason = job.pause_reason ?? "";
  const table = job.state === "paused" ? pausedEntries : waitingEntries;
  const note = job.note?.trim() || null;
  const entry: Entry = table[reason] ?? {
    title: note ?? "Review the job before continuing.",
    reason: note ?? "Review the job before continuing.",
    required: "Review the job before continuing.",
  };
  const noteStep = /^([^:\s]+):\s/.exec(note ?? "")?.[1];
  const generated =
    !!noteStep &&
    steps.some((s) => s.id === noteStep) &&
    note!.slice(noteStep.length + 2).trim() === reason;
  const known = !!table[reason];
  const detail = known && note && !generated && reason !== "sensitive" ? note : null;
  const failed =
    job.state === "paused"
      ? executions.find((e) => e.state === "failed" || e.state === "stopped")
      : undefined;
  const stepId =
    failed?.step_id ??
    (noteStep && steps.some((s) => s.id === noteStep) ? noteStep : undefined) ??
    job.current_step ??
    undefined;
  const step = steps.find((s) => s.id === stepId);
  const requesting = failed ?? executions.find((e) => e.step_id === stepId && e.report_path);
  const stage = stepId
    ? { step: stepId, label: step?.label || stepId, exec: failed?.exec_id ?? null }
    : null;
  let candidates: Execution[] = [];
  if (reportsOf.requester.includes(reason))
    candidates = requesting && !failed ? [requesting] : failed ? [failed] : [];
  else if (reportsOf.reviews.includes(reason))
    candidates = executions.filter((e) => e.type === "review" || e.type === "approval");
  const reports = candidates
    .filter((e) => !!e.report_path)
    .map((e) => ({
      nodeId: nodeIdFor(steps, e),
      step: e.step_id,
      exec: e.exec_id,
      result: e.result ?? e.state,
    }));
  const provider = executions.find((e) =>
    ["stopped", "failed", "running"].includes(e.state),
  )?.provider;
  const loginCommand =
    { claude: "claude login", opencode: "opencode auth login" }[provider ?? ""] ?? "codex login";
  return {
    ...entry,
    detail,
    stage,
    reports,
    sensitivePaths: reason === "sensitive" ? sensitivePathsOf(job.note) : [],
    loginCommand,
  };
}
