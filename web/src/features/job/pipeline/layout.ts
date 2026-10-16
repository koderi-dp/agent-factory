import { currentExecutions, type Execution, type Step } from "@/features/board/model";

export type CompactState = "pending" | "running" | "done" | "changes" | "failed";

/** Shared step projection for compact mode (plan 13.6). */
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

export const NODE_W = 220,
  NODE_H = 96,
  GAP_X = 56,
  GAP_Y = 12,
  LOOP_GAP = 40;
export type Box = { id: string; x: number; y: number; w: number; h: number };
export type LayoutNode = Box & {
  step: string;
  stepIndex: number;
  execIndex: number;
  exec: string;
  kind: string;
  execution?: Execution;
};
export type LayoutEdge = {
  from: string;
  fromIndex: number;
  to: string;
  toIndex: number;
  kind: "flow" | "rework";
  label: string;
  lane?: number;
};
export function layout(steps: Step[], executions: Execution[] = []) {
  const current = currentExecutions(steps, executions);
  const heights = steps.map((s) =>
    s.executions.length > 1 ? s.executions.length * (NODE_H + GAP_Y) + GAP_Y + 24 : NODE_H,
  );
  const height = Math.max(NODE_H, ...heights);
  const nodes: LayoutNode[] = [],
    groups: (Box & { label: string; stepIndex: number })[] = [],
    edges: LayoutEdge[] = [];
  steps.forEach((step, i) => {
    const x = i * (NODE_W + GAP_X),
      h = heights[i],
      y = (height - h) / 2;
    if (step.executions.length > 1) {
      const done = current.filter((e) => e.step_id === step.id && e.state === "done").length;
      groups.push({
        id: step.id,
        stepIndex: i,
        x,
        y,
        w: NODE_W,
        h,
        label: `${step.label || step.id} \u00b7 ${done}/${step.executions.length} done`,
      });
    }
    step.executions.forEach((entry, j) => {
      const grouped = step.executions.length > 1;
      nodes.push({
        id: grouped ? `${step.id}/${entry.id}` : step.id,
        step: step.id,
        stepIndex: i,
        execIndex: j,
        exec: entry.id,
        x: x + (grouped ? GAP_Y : 0),
        y: y + (grouped ? 24 + GAP_Y + j * (NODE_H + GAP_Y) : 0),
        w: NODE_W - (grouped ? GAP_Y * 2 : 0),
        h: NODE_H,
        kind: step.type ?? entry.role?.type ?? "task",
        execution: current.find((e) => e.step_id === step.id && e.exec_id === entry.id),
      });
    });
    if (i)
      edges.push({
        from: steps[i - 1].id,
        to: step.id,
        fromIndex: i - 1,
        toIndex: i,
        kind: "flow",
        label: "",
      });
  });
  const loops = steps
    .map((s, i) => ({ s, i, target: steps.findIndex((t) => t.id === s.onChanges) }))
    .filter((l) => l.target >= 0)
    .sort((a, b) => Math.abs(a.i - a.target) - Math.abs(b.i - b.target) || a.i - b.i);
  loops.forEach(({ s, i, target }, lane) => {
    const round = Math.max(
      1,
      ...current.filter((e) => e.step_id === s.onChanges).map((e) => e.round),
    );
    edges.push({
      from: s.id,
      fromIndex: i,
      toIndex: target,
      to: s.onChanges!,
      kind: "rework",
      lane: height + LOOP_GAP * (lane + 1),
      label: round > 1 ? `CHANGES \u00b7 round ${round}` : "CHANGES",
    });
  });
  return {
    nodes,
    groups,
    edges,
    width: steps.length ? steps.length * (NODE_W + GAP_X) - GAP_X : 0,
    height,
  };
}
