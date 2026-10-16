import { memo } from "react";
import type { LayoutNode } from "./layout";
import type { Step, Attempt } from "@/features/board/model";
import { latestAttempt } from "@/features/board/model";
import { Card } from "@/ui/primitives/card";
import { Badge } from "@/ui/primitives/badge";
import { StatusDot } from "@/ui/status";
import { duration } from "@/lib/format";

export const PipelineNode = memo(
  function PipelineNode({
    node,
    step,
    attempts,
    activity,
    paused,
    invalid,
    onOpen,
  }: {
    node: LayoutNode;
    step: Step;
    attempts: Attempt[];
    activity?: string | null;
    paused?: string | null;
    invalid?: boolean;
    onOpen?: (id: string) => void;
  }) {
    const execution = node.execution;
    const entry = step.executions[node.execIndex];
    const attempt = execution && latestAttempt(execution, attempts);
    const profile = entry.profiles?.[entry.provider ?? ""];
    const state = paused ? "paused" : (execution?.result ?? execution?.state ?? "pending");
    const elapsed =
      attempt?.t_launch && attempt.t_exit
        ? (Date.parse(attempt.t_exit) - Date.parse(attempt.t_launch)) / 1000
        : null;
    return (
      <Card
        className={`pipeline-node ${invalid ? "pipeline-invalid" : ""}`}
        style={{ position: "absolute", left: node.x, top: node.y, width: node.w, height: node.h }}
      >
        <button
          type="button"
          className="h-full w-full space-y-1 p-2 text-left text-xs focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => onOpen?.(node.id)}
          aria-label={`${step.label || step.id}: ${entry.uses ?? entry.id}, ${state}`}
        >
          <div className="flex gap-1">
            <Badge className="text-[9px]">{node.kind.toUpperCase()}</Badge>
            <span className="truncate">
              {entry.uses ?? entry.id} {step.label}
            </span>
          </div>
          <div className="truncate font-mono text-muted-foreground">
            {execution?.provider ?? entry.provider} · {execution?.model ?? profile?.model} ·{" "}
            {execution?.effort ?? profile?.effort}
          </div>
          <div className="truncate">
            <StatusDot
              state={state}
              className={execution?.state === "running" ? "status-pulse" : ""}
            />{" "}
            {state}{" "}
            {paused ??
              (execution?.state === "running"
                ? (activity ?? attempt?.last_activity)
                : duration(elapsed))}
          </div>
          {((attempt && attempt.n > 1) || (execution?.round ?? 1) > 1) && (
            <div className="text-muted-foreground">
              {attempt && attempt.n > 1
                ? `a${attempt.n} · ${attempt.kind.replaceAll("_", " ")}`
                : ""}{" "}
              {execution && execution.round > 1 ? `round ${execution.round}` : ""}
            </div>
          )}
        </button>
      </Card>
    );
  },
  (previous, next) => {
    const evidence = (props: typeof previous) => ({
      node: props.node,
      step: props.step,
      activity: props.activity,
      paused: props.paused,
      invalid: props.invalid,
      attempts: props.attempts.filter((a) => a.execution_id === props.node.execution?.id),
    });
    return (
      previous.onOpen === next.onOpen &&
      JSON.stringify(evidence(previous)) === JSON.stringify(evidence(next))
    );
  },
);
